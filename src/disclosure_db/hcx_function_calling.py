"""Fail-closed HyperCLOVA X Function Calling over the existing Tool Registry.

The orchestration contract is transport-independent and can be tested with a
fake client.  The concrete client uses CLOVA Studio's documented
OpenAI-compatible Chat Completions surface; it does not own Tool schemas,
retrieval, evidence admission, or answerability policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Mapping, Protocol, Sequence
import urllib.request
import uuid

from .generation import UNANSWERABLE_TEXT, parse_hcx_json_content
from .hcx_prompts import (
    HCX_FINAL_ANSWER_SYSTEM_PROMPT,
    HCX_FUNCTION_PROMPT_VERSION,
    HCX_TOOL_SELECTION_SYSTEM_PROMPT,
)
from .tool_registry import ToolRegistry


DEFAULT_HCX_BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
DEFAULT_HCX_MODEL = "HCX-005"
FUNCTION_CALLING_MAX_TOKENS = 1024
FUNCTION_FLOW_STATUSES = frozenset({"answered", "abstained", "invalid_request", "provider_unavailable", "error"})
_DART_RCEPT_NO = re.compile(r"\d{14}\Z")
_DART_RCEPT_NO_IN_TEXT = re.compile(r"접수번호\s*[:：]?\s*\d{14}")


class HcxFunctionCallingError(RuntimeError):
    """Base error whose message is safe and contains no provider response."""


class HcxNotConfiguredError(HcxFunctionCallingError):
    pass


class HcxProtocolError(HcxFunctionCallingError):
    pass


@dataclass(frozen=True, slots=True)
class HcxToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.call_id or len(self.call_id) > 256:
            raise ValueError("hcx_tool_call_id_invalid")
        if not self.name or len(self.name) > 100:
            raise ValueError("hcx_tool_name_invalid")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("hcx_tool_arguments_not_object")
        object.__setattr__(self, "arguments", dict(self.arguments))

    def assistant_message(self) -> dict[str, object]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": self.call_id,
                "type": "function",
                "function": {
                    "name": self.name,
                    "arguments": json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":")),
                },
            }],
        }


@dataclass(frozen=True, slots=True)
class HcxGeneratedAnswer:
    answer: str
    citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.answer, str) or not self.answer.strip() or len(self.answer) > 10_000:
            raise ValueError("hcx_final_answer_invalid")
        if not all(isinstance(item, str) and item for item in self.citation_ids):
            raise ValueError("hcx_final_citation_ids_invalid")


@dataclass(frozen=True, slots=True)
class HcxEvidenceCitation:
    evidence_id: str
    rcept_no: str
    report_name: str | None = None
    filed_at: str | None = None
    correction_role: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise ValueError("hcx_evidence_id_invalid")
        if _DART_RCEPT_NO.fullmatch(self.rcept_no) is None:
            raise ValueError("hcx_rcept_no_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "rcept_no": self.rcept_no,
            "report_name": self.report_name,
            "filed_at": self.filed_at,
            "correction_role": self.correction_role,
        }


class HcxFunctionClient(Protocol):
    model: str

    @property
    def configured(self) -> bool: ...

    def select_tool(
        self, question: str, tools: Sequence[Mapping[str, object]],
    ) -> HcxToolCall: ...

    def generate_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: Mapping[str, object],
    ) -> HcxGeneratedAnswer: ...


@dataclass(slots=True)
class FunctionCallingResult:
    status: str
    answer: str
    tool_name: str | None = None
    tool_response: dict[str, object] | None = None
    answer_allowed: bool = False
    recommended_action: str = "abstain"
    citation_ids: list[str] = field(default_factory=list)
    citations: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        if self.status not in FUNCTION_FLOW_STATUSES:
            raise ValueError("function_calling_status_invalid")
        return {
            "status": self.status,
            "answer": self.answer,
            "tool_name": self.tool_name,
            "tool_response": self.tool_response,
            "answer_allowed": self.answer_allowed,
            "recommended_action": self.recommended_action,
            "citation_ids": list(dict.fromkeys(self.citation_ids)),
            "citations": [dict(item) for item in self.citations],
            "warnings": list(dict.fromkeys(self.warnings)),
            "metadata": dict(self.metadata),
        }


def hcx_tool_schemas(registry: ToolRegistry) -> list[dict[str, object]]:
    """Derive HCX/OpenAI tool definitions directly from Registry contracts."""

    tools: list[dict[str, object]] = []
    for contract in registry.list_tools():
        name = contract.get("name")
        description = contract.get("description")
        input_schema = contract.get("input_schema")
        if not isinstance(name, str) or not isinstance(description, str) or not isinstance(input_schema, Mapping):
            raise ValueError("tool_public_contract_invalid")
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": input_schema,
            },
        })
    # JSON round-tripping both proves compatibility and prevents a provider or
    # caller from mutating the Registry's nested schema objects.
    return json.loads(json.dumps(tools, ensure_ascii=False))


class HyperClovaFunctionClient:
    """Standard-library adapter for documented HCX OpenAI-compatible tools."""

    prompt_version = HCX_FUNCTION_PROMPT_VERSION

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
        env: Mapping[str, str] | None = None,
        urlopen: object | None = None,
    ) -> None:
        values = os.environ if env is None else env
        self.api_key = api_key if api_key is not None else values.get("CLOVASTUDIO_API_KEY") or None
        self.base_url = (
            base_url or values.get("CLOVASTUDIO_BASE_URL") or DEFAULT_HCX_BASE_URL
        ).rstrip("/")
        self.model = model or values.get("CLOVASTUDIO_MODEL") or DEFAULT_HCX_MODEL
        if timeout <= 0:
            raise ValueError("hcx_timeout_must_be_positive")
        self.timeout = float(timeout)
        self._urlopen = urlopen or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def select_tool(
        self, question: str, tools: Sequence[Mapping[str, object]],
    ) -> HcxToolCall:
        if not tools:
            raise HcxProtocolError("hcx_tools_empty")
        data = self._chat({
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": HCX_TOOL_SELECTION_SYSTEM_PROMPT,
                },
                {"role": "user", "content": question},
            ],
            "tools": list(tools),
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": FUNCTION_CALLING_MAX_TOKENS,
        })
        message = self._message(data)
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or len(raw_calls) != 1:
            raise HcxProtocolError("hcx_requires_exactly_one_tool_call")
        raw_call = raw_calls[0]
        if not isinstance(raw_call, Mapping) or raw_call.get("type") != "function":
            raise HcxProtocolError("hcx_tool_call_shape_invalid")
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            raise HcxProtocolError("hcx_tool_function_missing")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise HcxProtocolError("hcx_tool_arguments_invalid_json") from exc
        if not isinstance(arguments, Mapping):
            raise HcxProtocolError("hcx_tool_arguments_not_object")
        try:
            return HcxToolCall(
                str(raw_call.get("id") or ""), str(function.get("name") or ""), dict(arguments)
            )
        except ValueError as exc:
            raise HcxProtocolError(str(exc)) from exc

    def generate_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: Mapping[str, object],
    ) -> HcxGeneratedAnswer:
        tool_content = json.dumps(tool_response, ensure_ascii=False, separators=(",", ":"))
        data = self._chat({
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": HCX_FINAL_ANSWER_SYSTEM_PROMPT,
                },
                {"role": "user", "content": question},
                tool_call.assistant_message(),
                {"role": "tool", "tool_call_id": tool_call.call_id, "content": tool_content},
            ],
            "temperature": 0,
            "max_tokens": FUNCTION_CALLING_MAX_TOKENS,
        })
        parsed = parse_hcx_json_content(self._message(data).get("content"))
        if not isinstance(parsed, Mapping) or set(parsed) != {"answer", "citation_ids"}:
            raise HcxProtocolError("hcx_final_output_schema_mismatch")
        answer = parsed.get("answer")
        citations = parsed.get("citation_ids")
        if not isinstance(answer, str) or not isinstance(citations, list) or not all(
            isinstance(item, str) for item in citations
        ):
            raise HcxProtocolError("hcx_final_output_schema_mismatch")
        try:
            return HcxGeneratedAnswer(answer, tuple(citations))
        except ValueError as exc:
            raise HcxProtocolError(str(exc)) from exc

    def _chat(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        if not self.api_key:
            raise HcxNotConfiguredError("hcx_api_key_not_configured")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            },
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:  # type: ignore[operator]
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise HcxFunctionCallingError("hcx_request_failed") from exc
        if not isinstance(data, Mapping):
            raise HcxProtocolError("hcx_response_not_object")
        return data

    @staticmethod
    def _message(data: Mapping[str, object]) -> Mapping[str, object]:
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise HcxProtocolError("hcx_choices_shape_invalid")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise HcxProtocolError("hcx_message_missing")
        return message


class HcxFunctionCallingService:
    """Select one Tool, dispatch it, enforce sufficiency, then optionally generate."""

    def __init__(
        self,
        registry: ToolRegistry,
        client: HcxFunctionClient,
        *,
        max_question_chars: int = 2_000,
    ) -> None:
        if max_question_chars <= 0:
            raise ValueError("max_question_chars_must_be_positive")
        self.registry = registry
        self.client = client
        self.max_question_chars = max_question_chars

    def available_tools(self) -> list[dict[str, object]]:
        return hcx_tool_schemas(self.registry)

    def answer(self, question: object) -> FunctionCallingResult:
        if not isinstance(question, str) or not question.strip() or len(question) > self.max_question_chars:
            return self._result(
                "invalid_request", "질문 형식이 올바르지 않습니다.", warnings=["question_invalid"]
            )
        question = question.strip()
        if not self.client.configured:
            return self._result(
                "provider_unavailable", UNANSWERABLE_TEXT,
                warnings=["hcx_api_key_not_configured"], metadata={"provider_configured": False},
            )
        tools = self.available_tools()
        try:
            tool_call = self.client.select_tool(question, tools)
        except Exception as exc:
            return self._result(
                "error", UNANSWERABLE_TEXT, warnings=["hcx_tool_selection_failed"],
                metadata={"provider_configured": True, "error_type": type(exc).__name__},
            )

        # Search/context Tools must execute the original user question.  A
        # malformed or omitted question still reaches Registry validation and
        # is rejected; only an already-string provider paraphrase is replaced.
        if isinstance(tool_call.arguments.get("question"), str):
            tool_call = HcxToolCall(
                tool_call.call_id, tool_call.name, {**tool_call.arguments, "question": question}
            )
        tool_response = self.registry.dispatch(tool_call.name, tool_call.arguments)
        tool_status = str(tool_response.get("status") or "error")
        sufficiency = self._sufficiency(tool_response)
        recommended_action = str(sufficiency.get("recommended_action") or "abstain")
        answer_allowed = (
            tool_status in {"success", "partial"}
            and sufficiency.get("answer_allowed") is True
            and recommended_action in {"answer", "answer_with_warning"}
        )
        common = {
            "tool_name": tool_call.name,
            "tool_response": tool_response,
            "recommended_action": recommended_action,
            "metadata": {
                "provider_configured": True,
                "model": self.client.model,
                "prompt_version": HCX_FUNCTION_PROMPT_VERSION,
                "tool_selection_called": True,
                "final_generation_called": False,
            },
        }
        if tool_status == "invalid_request":
            return self._result(
                "invalid_request", self._abstention_text(recommended_action),
                warnings=["tool_request_rejected"], **common,
            )
        if tool_status == "error":
            return self._result(
                "error", self._abstention_text(recommended_action),
                warnings=["tool_execution_failed"], **common,
            )
        if not answer_allowed:
            return self._result(
                "abstained", self._abstention_text(recommended_action),
                warnings=["answer_generation_blocked_by_sufficiency"], **common,
            )

        available_citations = self._available_citations(tool_call.name, tool_response)
        if not available_citations:
            common["recommended_action"] = "abstain"
            return self._result(
                "abstained", UNANSWERABLE_TEXT,
                warnings=["answer_generation_blocked_by_missing_rcept_no"], **common,
            )
        if (
            tool_call.name == "get_correction_lineage"
            and self._requires_correction_pair(tool_response)
            and not self._has_correction_pair(available_citations.values())
        ):
            common["recommended_action"] = "abstain"
            return self._result(
                "abstained", UNANSWERABLE_TEXT,
                warnings=["answer_generation_blocked_by_incomplete_correction_receipts"], **common,
            )

        common["metadata"]["final_generation_called"] = True  # type: ignore[index]
        try:
            generated = self.client.generate_answer(question, tool_call, tool_response)
            known_ids = {
                str(item) for item in (
                    tool_response.get("evidence_bundle", {}).get("evidence_ids", [])  # type: ignore[union-attr]
                ) if item
            }
            if not generated.citation_ids or any(item not in known_ids for item in generated.citation_ids):
                raise HcxProtocolError("hcx_final_citations_not_in_evidence_bundle")
            if _DART_RCEPT_NO_IN_TEXT.search(generated.answer):
                raise HcxProtocolError("hcx_final_answer_contains_provider_rendered_rcept_no")
            citations = self._selected_citations(
                tool_call.name, generated.citation_ids, available_citations
            )
            if not citations:
                raise HcxProtocolError("hcx_final_citations_missing_valid_rcept_no")
        except Exception as exc:
            common["metadata"]["error_type"] = type(exc).__name__  # type: ignore[index]
            common["recommended_action"] = "abstain"
            return self._result(
                "error", UNANSWERABLE_TEXT, warnings=["hcx_final_generation_failed"], **common,
            )
        return self._result(
            "answered", self._render_answer(generated.answer, citations), answer_allowed=True,
            citation_ids=[item.evidence_id for item in citations],
            citations=[item.to_dict() for item in citations], **common,
        )

    @staticmethod
    def _available_citations(
        tool_name: str, tool_response: Mapping[str, object],
    ) -> dict[str, HcxEvidenceCitation]:
        bundle = tool_response.get("evidence_bundle")
        if not isinstance(bundle, Mapping):
            return {}
        items = bundle.get("items")
        if not isinstance(items, list):
            return {}
        role_by_rcept: dict[str, str] = {}
        data = tool_response.get("data")
        if tool_name == "get_correction_lineage" and isinstance(data, Mapping):
            original = data.get("original_filing")
            current = data.get("current_filing")
            corrected = data.get("corrected_filings")
            if isinstance(original, Mapping) and original.get("filing_id"):
                role_by_rcept[str(original["filing_id"])] = "최초공시"
            if isinstance(corrected, list):
                for row in corrected:
                    if isinstance(row, Mapping) and row.get("filing_id"):
                        role_by_rcept[str(row["filing_id"])] = "정정공시"
            if isinstance(current, Mapping) and current.get("filing_id"):
                current_id = str(current["filing_id"])
                role_by_rcept.setdefault(current_id, "현재공시")
        citations: dict[str, HcxEvidenceCitation] = {}
        for raw_item in items:
            if not isinstance(raw_item, Mapping):
                continue
            rcept_no = str(raw_item.get("rcept_no") or raw_item.get("filing_id") or "")
            if _DART_RCEPT_NO.fullmatch(rcept_no) is None:
                continue
            raw_ids = raw_item.get("evidence_ids")
            evidence_ids = list(raw_ids) if isinstance(raw_ids, (list, tuple)) else []
            if raw_item.get("evidence_id"):
                evidence_ids.insert(0, raw_item["evidence_id"])
            for evidence_id in dict.fromkeys(str(item) for item in evidence_ids if item):
                citations[evidence_id] = HcxEvidenceCitation(
                    evidence_id=evidence_id,
                    rcept_no=rcept_no,
                    report_name=str(raw_item.get("report_name") or "DART 공시"),
                    filed_at=str(raw_item["filed_at"]) if raw_item.get("filed_at") else None,
                    correction_role=role_by_rcept.get(rcept_no),
                )
        return citations

    @staticmethod
    def _has_correction_pair(citations: Sequence[HcxEvidenceCitation]) -> bool:
        roles = {item.correction_role for item in citations}
        return "최초공시" in roles and "정정공시" in roles

    @staticmethod
    def _requires_correction_pair(tool_response: Mapping[str, object]) -> bool:
        data = tool_response.get("data")
        corrected = data.get("corrected_filings") if isinstance(data, Mapping) else None
        return isinstance(corrected, list) and bool(corrected)

    @staticmethod
    def _selected_citations(
        tool_name: str,
        generated_ids: Sequence[str],
        available: Mapping[str, HcxEvidenceCitation],
    ) -> list[HcxEvidenceCitation]:
        identifiers = list(available) if tool_name == "get_correction_lineage" else list(generated_ids)
        selected: list[HcxEvidenceCitation] = []
        seen_receipts: set[str] = set()
        for evidence_id in identifiers:
            citation = available.get(evidence_id)
            if citation is None or citation.rcept_no in seen_receipts:
                continue
            seen_receipts.add(citation.rcept_no)
            selected.append(citation)
        return selected

    @staticmethod
    def _render_answer(answer: str, citations: Sequence[HcxEvidenceCitation]) -> str:
        lines = [answer.strip(), "", "근거 공시"]
        if len(citations) == 1:
            citation = citations[0]
            label = " ".join(item for item in (citation.correction_role, citation.report_name) if item)
            lines.extend([f"- {label or 'DART 공시'}", f"- 접수번호: {citation.rcept_no}"])
        else:
            for index, citation in enumerate(citations, start=1):
                label = " ".join(item for item in (citation.correction_role, citation.report_name) if item)
                lines.append(f"{index}. {label or 'DART 공시'} — 접수번호: {citation.rcept_no}")
        return "\n".join(lines)

    @staticmethod
    def _sufficiency(tool_response: Mapping[str, object]) -> Mapping[str, object]:
        metadata = tool_response.get("metadata")
        if not isinstance(metadata, Mapping):
            return {}
        sufficiency = metadata.get("sufficiency_check")
        return sufficiency if isinstance(sufficiency, Mapping) else {}

    @staticmethod
    def _abstention_text(action: str) -> str:
        if action == "ask_clarification":
            return "근거 범위를 확정할 수 있도록 회사, 계정 또는 기간을 더 구체적으로 알려주세요."
        return UNANSWERABLE_TEXT

    def _result(
        self,
        status: str,
        answer: str,
        *,
        tool_name: str | None = None,
        tool_response: dict[str, object] | None = None,
        answer_allowed: bool = False,
        recommended_action: str = "abstain",
        citation_ids: list[str] | None = None,
        citations: list[dict[str, object]] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> FunctionCallingResult:
        return FunctionCallingResult(
            status=status,
            answer=answer,
            tool_name=tool_name,
            tool_response=tool_response,
            answer_allowed=answer_allowed,
            recommended_action=recommended_action,
            citation_ids=list(citation_ids or []),
            citations=[dict(item) for item in (citations or [])],
            warnings=list(warnings or []),
            metadata={
                "model": getattr(self.client, "model", None),
                "prompt_version": HCX_FUNCTION_PROMPT_VERSION,
                **dict(metadata or {}),
            },
        )


__all__ = [
    "DEFAULT_HCX_BASE_URL",
    "DEFAULT_HCX_MODEL",
    "FUNCTION_CALLING_MAX_TOKENS",
    "FunctionCallingResult",
    "HcxFunctionCallingError",
    "HcxFunctionCallingService",
    "HcxEvidenceCitation",
    "HcxGeneratedAnswer",
    "HcxNotConfiguredError",
    "HcxProtocolError",
    "HcxToolCall",
    "HyperClovaFunctionClient",
    "hcx_tool_schemas",
]
