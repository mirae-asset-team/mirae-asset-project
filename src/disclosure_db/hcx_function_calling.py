"""Fail-closed HyperCLOVA X Function Calling over the existing Tool Registry.

The orchestration contract is transport-independent and can be tested with a
fake client.  The concrete client uses CLOVA Studio's documented
OpenAI-compatible Chat Completions surface; it does not own Tool schemas,
retrieval, evidence admission, or answerability policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
import logging
import os
import re
from typing import Mapping, Protocol, Sequence
import urllib.error
import urllib.request
import uuid

from .generation import UNANSWERABLE_TEXT
from .bounded_analysis import BoundedAnalysisExecution, BoundedAnalysisExecutor
from .analysis_planner import classify_policy
from .calculator import calculate
from .claim_verification import (
    ClaimAdmission,
    HcxAnswerClaim,
    build_claim_admission,
    verify_generated_claims,
)
from .disclosure_tools import (
    format_financial_value,
    normalized_financial_value,
    parse_korean_krw_amounts,
)
from .evidence_sufficiency import EvidenceSufficiencyChecker
from .hcx_prompts import (
    HCX_FINAL_ANSWER_SYSTEM_PROMPT,
    HCX_FUNCTION_PROMPT_VERSION,
    HCX_ROUTED_FINAL_ANSWER_SYSTEM_PROMPT,
    HCX_TOOL_SELECTION_SYSTEM_PROMPT,
)
from .input_hardening import (
    PUBLIC_QUESTION_MAX_CHARS,
    QuestionInputError,
    detect_prompt_injection,
    preflight_public_question,
)
from .question_routing import QuestionRoute
from .tool_registry import ToolRegistry
from .tool_contracts import ToolEvidenceBundle


DEFAULT_HCX_BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
DEFAULT_HCX_MODEL = "HCX-005"
FUNCTION_CALLING_MAX_TOKENS = 1024
FINAL_ANSWER_TOOL_NAME = "submit_grounded_answer"
FUNCTION_FLOW_STATUSES = frozenset({"answered", "abstained", "invalid_request", "provider_unavailable", "error"})
_DART_RCEPT_NO = re.compile(r"\d{14}\Z")
_DART_RCEPT_NO_IN_TEXT = re.compile(r"접수번호\s*[:：]?\s*\d{14}")
_FORECAST_CLAIM = re.compile(
    r"(?:될\s*것으로\s*(?:예상|전망)|예상(?:됩니다|입니다|된다|될|치|\s*(?:매출|수익|실적|금액|수치))|"
    r"전망(?:됩니다|입니다|된다|될|치)|추정(?:됩니다|입니다|된다|될|치)|forecast|estimated?)",
    re.IGNORECASE,
)
_AMOUNT_VERIFICATION_MARKERS = ("맞죠", "맞나요", "맞습니까", "맞아요", "맞는지", "맞는가")
_AMOUNT_UNIT_TOKEN = re.compile(r"([0-9][0-9,.]*)\s*(조|십억|억|천만|백만|만|원)")
_AMOUNT_UNIT_FACTOR = {
    "조": Decimal(10) ** 12,
    "십억": Decimal(10) ** 9,
    "억": Decimal(10) ** 8,
    "천만": Decimal(10) ** 7,
    "백만": Decimal(10) ** 6,
    "만": Decimal(10) ** 4,
    "원": Decimal(1),
}
_SAFE_DIAGNOSTIC_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_LOGGER = logging.getLogger(__name__)


class HcxFunctionCallingError(RuntimeError):
    """Base error whose message is safe and contains no provider response."""

    def __init__(
        self,
        code: str,
        *,
        stage: str | None = None,
        http_status: int | None = None,
        provider_error_code: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code if _SAFE_DIAGNOSTIC_TOKEN.fullmatch(code) else "hcx_error"
        self.stage = stage if stage and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(stage) else None
        self.http_status = http_status
        self.provider_error_code = (
            provider_error_code
            if provider_error_code and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(provider_error_code)
            else None
        )


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
    conclusion: str | None = None
    claims: tuple[HcxAnswerClaim, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.answer, str) or not self.answer.strip() or len(self.answer) > 10_000:
            raise ValueError("hcx_final_answer_invalid")
        if not all(isinstance(item, str) and item for item in self.citation_ids):
            raise ValueError("hcx_final_citation_ids_invalid")
        if self.conclusion is not None and not isinstance(self.conclusion, str):
            raise ValueError("hcx_final_conclusion_invalid")
        try:
            claims = tuple(HcxAnswerClaim.from_value(item) for item in self.claims)
        except (TypeError, ValueError) as exc:
            raise ValueError("hcx_final_claims_invalid") from exc
        if not all(isinstance(item, str) and item for item in self.limitations):
            raise ValueError("hcx_final_limitations_invalid")
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "limitations", tuple(dict.fromkeys(self.limitations)))


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

    def generate_routed_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: Mapping[str, object],
    ) -> HcxGeneratedAnswer: ...


class QuestionRouter(Protocol):
    def route(self, question: str) -> QuestionRoute | None: ...


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


def _final_answer_tool(
    evidence_ids: Sequence[str],
    allowed_conclusions: Sequence[str] = (),
    *,
    fact_refs: Sequence[str] = (),
    calculation_refs: Sequence[str] = (),
    evidence_slot_ids: Sequence[str] = (),
) -> dict[str, object]:
    """Build the private HCX output contract from admitted Evidence IDs."""

    allowed_ids = list(dict.fromkeys(str(item) for item in evidence_ids if item))
    if not allowed_ids:
        raise HcxProtocolError(
            "hcx_final_evidence_ids_empty", stage="final_generation_preflight"
        )
    properties: dict[str, object] = {
        "answer": {
            "type": "string",
            "description": "DART Tool Evidence에만 근거한 한국어 최종 답변",
        },
        "citation_ids": {
            "type": "array",
            "description": "답변에 실제 사용한 evidence ID",
            "items": {"type": "string", "enum": allowed_ids},
            "minItems": 1,
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "citation_ids": {
                        "type": "array", "minItems": 1,
                        "items": {"type": "string", "enum": allowed_ids},
                    },
                    "fact_refs": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(fact_refs)},
                    },
                    "calculation_refs": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(calculation_refs)},
                    },
                    "evidence_slot_ids": {
                        "type": "array", "minItems": 1,
                        "items": {"type": "string", "enum": list(evidence_slot_ids)},
                    },
                    "numeric_values": {
                        "type": "array", "items": {"type": "string"},
                    },
                },
                "required": [
                    "claim_id", "text", "citation_ids", "fact_refs",
                    "calculation_refs", "evidence_slot_ids", "numeric_values",
                ],
            },
        },
        "limitations": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 256},
        },
    }
    required = ["answer", "citation_ids", "claims", "limitations"]
    bounded_conclusions = list(dict.fromkeys(
        str(item) for item in allowed_conclusions if item != "insufficient_evidence"
    ))
    if bounded_conclusions:
        properties["conclusion"] = {
            "type": "string",
            "description": "백엔드가 허용한 과거 공시 판단 범주",
            "enum": bounded_conclusions,
        }
        required.append("conclusion")
    return {
        "type": "function",
        "function": {
            "name": FINAL_ANSWER_TOOL_NAME,
            "description": (
                "Tool Evidence만 사용한 최종 답변과 실제 사용한 evidence ID를 제출한다."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
    }


def _argument_schema(value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        properties = {str(key): _argument_schema(item) for key, item in value.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        }
    if isinstance(value, (list, tuple)):
        item_schema = _argument_schema(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    return {"type": "string"}


def _conversation_tool(tool_call: HcxToolCall) -> dict[str, object]:
    """Echo the executed Tool definition so HCX accepts the follow-up turn."""

    properties = {
        str(name): _argument_schema(value)
        for name, value in tool_call.arguments.items()
    }
    return {
        "type": "function",
        "function": {
            "name": tool_call.name,
            "description": "The disclosure Tool already executed for this conversation.",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
            },
        },
    }


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
            raise HcxProtocolError("hcx_tools_empty", stage="tool_selection_preflight")
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
        }, stage="tool_selection_request")
        message = self._message(data, stage="tool_selection_response")
        return self._tool_call(message, stage="tool_selection_response")

    @staticmethod
    def _tool_call(
        message: Mapping[str, object],
        *,
        stage: str,
        expected_name: str | None = None,
    ) -> HcxToolCall:
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or len(raw_calls) != 1:
            raise HcxProtocolError("hcx_requires_exactly_one_tool_call", stage=stage)
        raw_call = raw_calls[0]
        if not isinstance(raw_call, Mapping) or raw_call.get("type") != "function":
            raise HcxProtocolError("hcx_tool_call_shape_invalid", stage=stage)
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            raise HcxProtocolError("hcx_tool_function_missing", stage=stage)
        name = str(function.get("name") or "")
        if expected_name is not None and name != expected_name:
            raise HcxProtocolError("hcx_unexpected_tool_call", stage=stage)
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise HcxProtocolError("hcx_tool_arguments_invalid_json", stage=stage) from exc
        if not isinstance(arguments, Mapping):
            raise HcxProtocolError("hcx_tool_arguments_not_object", stage=stage)
        try:
            return HcxToolCall(
                str(raw_call.get("id") or ""), name, dict(arguments)
            )
        except ValueError as exc:
            raise HcxProtocolError(str(exc), stage=stage) from exc

    def generate_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: Mapping[str, object],
    ) -> HcxGeneratedAnswer:
        bundle = tool_response.get("evidence_bundle")
        raw_evidence_ids = bundle.get("evidence_ids") if isinstance(bundle, Mapping) else None
        evidence_ids = (
            [str(item) for item in raw_evidence_ids if item]
            if isinstance(raw_evidence_ids, (list, tuple))
            else []
        )
        data = tool_response.get("data")
        raw_conclusions = data.get("allowed_conclusions") if isinstance(data, Mapping) else None
        allowed_conclusions = (
            [str(item) for item in raw_conclusions if isinstance(item, str)]
            if isinstance(raw_conclusions, (list, tuple))
            else []
        )
        admission = build_claim_admission(tool_response)
        final_tool = _final_answer_tool(
            evidence_ids,
            allowed_conclusions,
            fact_refs=tuple(admission.facts),
            calculation_refs=tuple(admission.calculations),
            evidence_slot_ids=tuple(admission.evidence_slots),
        )
        provider_tool_response = dict(tool_response)
        raw_metadata = tool_response.get("metadata")
        provider_tool_response["metadata"] = {
            **(dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}),
            "claim_contract": admission.public_contract(),
        }
        tool_content = json.dumps(
            provider_tool_response, ensure_ascii=False, separators=(",", ":")
        )
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
            # CLOVA validates every assistant tool_call against the tools list
            # in the same stateless request.  Keep the already-executed Tool
            # alongside the private forced final-answer Tool.
            "tools": [_conversation_tool(tool_call), final_tool],
            "tool_choice": {
                "type": "function",
                "function": {"name": FINAL_ANSWER_TOOL_NAME},
            },
            "temperature": 0,
            "max_tokens": FUNCTION_CALLING_MAX_TOKENS,
        }, stage="final_generation_request")
        final_call = self._tool_call(
            self._message(data, stage="final_generation_response"),
            stage="final_generation_response",
            expected_name=FINAL_ANSWER_TOOL_NAME,
        )
        expected_fields = {"answer", "citation_ids", "claims", "limitations"}
        if allowed_conclusions:
            expected_fields.add("conclusion")
        if set(final_call.arguments) != expected_fields:
            raise HcxProtocolError(
                "hcx_final_output_schema_mismatch", stage="final_generation_response"
            )
        answer = final_call.arguments.get("answer")
        citations = final_call.arguments.get("citation_ids")
        conclusion = final_call.arguments.get("conclusion")
        claims = final_call.arguments.get("claims")
        limitations = final_call.arguments.get("limitations")
        if not isinstance(answer, str) or not isinstance(citations, list) or not all(
            isinstance(item, str) for item in citations
        ) or not isinstance(claims, list) or not isinstance(limitations, list) or not all(
            isinstance(item, str) for item in limitations
        ) or (allowed_conclusions and not isinstance(conclusion, str)):
            raise HcxProtocolError(
                "hcx_final_output_schema_mismatch", stage="final_generation_response"
            )
        try:
            return HcxGeneratedAnswer(
                answer,
                tuple(citations),
                str(conclusion) if conclusion is not None else None,
                tuple(HcxAnswerClaim.from_value(item) for item in claims),
                tuple(limitations),
            )
        except ValueError as exc:
            raise HcxProtocolError(str(exc), stage="final_generation_response") from exc

    def generate_routed_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: Mapping[str, object],
    ) -> HcxGeneratedAnswer:
        """Generate once after a deterministic route using the private final Tool."""

        bundle = tool_response.get("evidence_bundle")
        raw_evidence_ids = bundle.get("evidence_ids") if isinstance(bundle, Mapping) else None
        evidence_ids = list(dict.fromkeys(
            str(item) for item in raw_evidence_ids if item
        )) if isinstance(raw_evidence_ids, (list, tuple)) else []
        if not evidence_ids:
            raise HcxProtocolError(
                "hcx_final_evidence_ids_empty", stage="final_generation_preflight"
            )
        metadata = tool_response.get("metadata")
        strict_contract = (
            tool_response.get("status") is not None
            and isinstance(metadata, Mapping)
            and isinstance(metadata.get("sufficiency_check"), Mapping)
        )
        if strict_contract:
            admission = build_claim_admission(tool_response)
            final_tool = _final_answer_tool(
                evidence_ids,
                fact_refs=tuple(admission.facts),
                calculation_refs=tuple(admission.calculations),
                evidence_slot_ids=tuple(admission.evidence_slots),
            )
            data = self._chat({
                "model": self.model,
                "messages": [
                    {"role": "system", "content": HCX_ROUTED_FINAL_ANSWER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps({
                            "question": question,
                            "tool_name": tool_call.name,
                            "tool_result": tool_response,
                            "claim_contract": admission.public_contract(),
                        }, ensure_ascii=False, separators=(",", ":")),
                    },
                ],
                "tools": [final_tool],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": FINAL_ANSWER_TOOL_NAME},
                },
                "temperature": 0,
                "max_tokens": FUNCTION_CALLING_MAX_TOKENS,
            }, stage="routed_final_generation_request")
            final_call = self._tool_call(
                self._message(data, stage="routed_final_generation_response"),
                stage="routed_final_generation_response",
                expected_name=FINAL_ANSWER_TOOL_NAME,
            )
            expected_fields = {"answer", "citation_ids", "claims", "limitations"}
            if set(final_call.arguments) != expected_fields:
                raise HcxProtocolError(
                    "hcx_final_output_schema_mismatch",
                    stage="routed_final_generation_response",
                )
            answer = final_call.arguments.get("answer")
            citations = final_call.arguments.get("citation_ids")
            claims = final_call.arguments.get("claims")
            limitations = final_call.arguments.get("limitations")
            if (
                not isinstance(answer, str)
                or not isinstance(citations, list)
                or not all(isinstance(item, str) for item in citations)
                or not isinstance(claims, list)
                or not isinstance(limitations, list)
                or not all(isinstance(item, str) for item in limitations)
            ):
                raise HcxProtocolError(
                    "hcx_final_output_schema_mismatch",
                    stage="routed_final_generation_response",
                )
            try:
                return HcxGeneratedAnswer(
                    answer,
                    tuple(citations),
                    claims=tuple(HcxAnswerClaim.from_value(item) for item in claims),
                    limitations=tuple(limitations),
                )
            except ValueError as exc:
                raise HcxProtocolError(
                    str(exc), stage="routed_final_generation_response"
                ) from exc
        data = self._chat({
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": HCX_ROUTED_FINAL_ANSWER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "question": question,
                        "tool_name": tool_call.name,
                        "tool_result": tool_response,
                    }, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "temperature": 0,
            "max_tokens": FUNCTION_CALLING_MAX_TOKENS,
        }, stage="routed_final_generation_request")
        message = self._message(data, stage="routed_final_generation_response")
        answer = message.get("content")
        if not isinstance(answer, str):
            raise HcxProtocolError(
                "hcx_final_answer_invalid", stage="routed_final_generation_response"
            )
        try:
            return HcxGeneratedAnswer(answer, tuple(evidence_ids[:5]))
        except ValueError as exc:
            raise HcxProtocolError(str(exc), stage="routed_final_generation_response") from exc

    def _chat(
        self, payload: Mapping[str, object], *, stage: str = "provider_request",
    ) -> Mapping[str, object]:
        if not self.api_key:
            raise HcxNotConfiguredError("hcx_api_key_not_configured", stage=stage)
        provider_request_id = str(uuid.uuid4())
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": provider_request_id,
            },
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:  # type: ignore[operator]
                raw_response = response.read()
        except urllib.error.HTTPError as exc:
            raise HcxFunctionCallingError(
                "hcx_http_error",
                stage=stage,
                http_status=exc.code,
                provider_error_code=self._provider_error_code(exc),
            ) from exc
        except Exception as exc:
            raise HcxFunctionCallingError("hcx_request_failed", stage=stage) from exc
        try:
            data = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HcxProtocolError("hcx_response_invalid_json", stage=stage) from exc
        if not isinstance(data, Mapping):
            raise HcxProtocolError("hcx_response_not_object", stage=stage)
        return data

    @staticmethod
    def _provider_error_code(error: urllib.error.HTTPError) -> str | None:
        """Extract only a bounded provider code; discard the raw error body."""

        try:
            raw = error.read(65_536)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        provider_error = payload.get("error") if isinstance(payload, Mapping) else None
        code = provider_error.get("code") if isinstance(provider_error, Mapping) else None
        token = str(code) if code is not None else ""
        return token if _SAFE_DIAGNOSTIC_TOKEN.fullmatch(token) else None

    @staticmethod
    def _message(
        data: Mapping[str, object], *, stage: str = "provider_response",
    ) -> Mapping[str, object]:
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise HcxProtocolError("hcx_choices_shape_invalid", stage=stage)
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise HcxProtocolError("hcx_message_missing", stage=stage)
        return message


class HcxFunctionCallingService:
    """Select one Tool, dispatch it, enforce sufficiency, then optionally generate."""

    def __init__(
        self,
        registry: ToolRegistry,
        client: HcxFunctionClient,
        *,
        max_question_chars: int = 2_000,
        router: QuestionRouter | None = None,
        analysis_executor: BoundedAnalysisExecutor | None = None,
    ) -> None:
        if max_question_chars <= 0:
            raise ValueError("max_question_chars_must_be_positive")
        if max_question_chars > PUBLIC_QUESTION_MAX_CHARS:
            raise ValueError("max_question_chars_exceeds_public_limit")
        self.registry = registry
        self.client = client
        self.max_question_chars = max_question_chars
        self.router = router
        self.analysis_executor = analysis_executor
        self.evidence_checker = EvidenceSufficiencyChecker()

    @staticmethod
    def _analysis_metadata(execution: BoundedAnalysisExecution) -> dict[str, object]:
        return {
            "execution_mode": "bounded_analysis",
            "analysis_dimension": execution.plan.judgment_dimension,
            "conclusion": execution.conclusion,
            "evidence_slots": [dict(slot) for slot in execution.evidence_slots],
            "limitations": [
                "historical_disclosure_only",
                "no_transaction_recommendation",
                "no_forecast",
                *execution.reason_codes,
            ],
        }

    def available_tools(self) -> list[dict[str, object]]:
        return hcx_tool_schemas(self.registry)

    @staticmethod
    def _fact_period_key(fact: Mapping[str, object]) -> str:
        period = fact.get("period")
        if not isinstance(period, Mapping):
            return ""
        return str(period.get("instant_date") or period.get("period_end") or period.get("period_start") or "")

    @staticmethod
    def _calculation_payload(result: object) -> dict[str, object]:
        value = str(getattr(result, "value"))
        unit = getattr(result, "unit")
        display_value = format_financial_value(value, 1, unit)
        if display_value is None and unit == "%":
            display_value = f"{Decimal(value):.2f}%"
        return {
            "operation": str(getattr(result, "operation")),
            "value": value,
            "display_value": display_value,
            "unit": unit,
            "evidence_ids": list(getattr(result, "evidence_ids")),
            "operands": [str(item) for item in getattr(result, "operands")],
        }

    @staticmethod
    def _bundle_from_response(response: Mapping[str, object]) -> Mapping[str, object]:
        bundle = response.get("evidence_bundle")
        return bundle if isinstance(bundle, Mapping) else {}

    @staticmethod
    def _generated_claims(generated: object) -> tuple[object, ...]:
        raw_claims = getattr(generated, "claims", ())
        if isinstance(raw_claims, (list, tuple)):
            return tuple(raw_claims)
        return ()

    @staticmethod
    def _fact_row_admitted(
        row: Mapping[str, object], admission: ClaimAdmission,
    ) -> bool:
        fact_ref: str | None = None
        for key in ("financial_fact_id", "event_fact_id", "fact_id"):
            if row.get(key):
                fact_ref = str(row[key])
                break
        if fact_ref is None:
            return False
        admitted_fact = admission.facts.get(fact_ref)
        if admitted_fact is None:
            return False
        raw_ids = row.get("evidence_ids")
        ids = [str(item) for item in raw_ids if item] if isinstance(raw_ids, (list, tuple)) else []
        if row.get("evidence_id"):
            ids.insert(0, str(row["evidence_id"]))
        return tuple(dict.fromkeys(ids)) == admitted_fact.evidence_ids

    def _deterministic_claim_fallback(
        self,
        tool_response: Mapping[str, object],
        admission: ClaimAdmission,
        available_evidence_ids: Sequence[str],
        route: QuestionRoute | None,
    ) -> HcxGeneratedAnswer | None:
        if not admission.facts:
            return None
        safe_response = dict(tool_response)
        raw_data = tool_response.get("data")
        data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
        raw_facts = data.get("facts")
        if isinstance(raw_facts, list):
            safe_facts: list[dict[str, object]] = []
            for raw_row in raw_facts:
                if not isinstance(raw_row, Mapping) or not self._fact_row_admitted(raw_row, admission):
                    continue
                row = dict(raw_row)
                if not row.get("display_value") and row.get("value_numeric") is not None:
                    unit = str(row.get("unit") or "KRW")
                    scale = Decimal(str(row.get("scale") or 1))
                    raw_value = Decimal(str(row["value_numeric"]))
                    if unit in {"KRW", "원"} and scale == 1 and raw_value.is_finite():
                        rendered = (
                            f"{int(raw_value):,}원"
                            if raw_value == raw_value.to_integral_value()
                            else f"{raw_value:,}원"
                        )
                    else:
                        rendered = format_financial_value(raw_value, scale, unit)
                    if rendered:
                        row["display_value"] = rendered
                safe_facts.append(row)
            data["facts"] = safe_facts
        if admission.rejected_calculations:
            data["calculations"] = []
            comparison = data.get("comparison")
            if isinstance(comparison, Mapping):
                data["comparison"] = {**dict(comparison), "calculations": []}
        safe_response["data"] = data
        safe_ids = [
            evidence_id for evidence_id in available_evidence_ids
            if any(evidence_id in fact.evidence_ids for fact in admission.facts.values())
        ]
        if not safe_ids:
            safe_ids = list(available_evidence_ids)
        if route is not None and route.workflow == "financial_change_reason":
            premise = str(data.get("premise") or "")
            if premise.endswith("_not_confirmed"):
                return self._deterministic_change_reason_answer(
                    route, safe_response, safe_ids,
                )
        comparison = data.get("comparison")
        if isinstance(comparison, Mapping) and comparison.get("status") == "complete":
            comparison_answer = self._deterministic_financial_comparison_answer(
                safe_response, safe_ids,
            )
            if comparison_answer is not None:
                return comparison_answer
        if isinstance(data.get("facts"), list) and data["facts"]:
            return self._deterministic_financial_answer(safe_response, safe_ids)
        if data.get("total_count") is not None or (
            isinstance(data.get("quantitative_trend"), Mapping)
            and data["quantitative_trend"].get("total_count") is not None
        ):
            trend_data = data.get("quantitative_trend")
            if isinstance(trend_data, Mapping):
                safe_response["data"] = dict(trend_data)
            return self._deterministic_trend_answer(safe_response, safe_ids)
        if route is not None and route.workflow == "financial_statement_metric":
            values = data.get("validated_statement_values")
            rows = [row for row in values if isinstance(row, Mapping)] if isinstance(values, list) else []
            if rows:
                text = "\n".join(
                    f"{row.get('period') or ''} {row.get('metric') or '재무 수치'}은 {row.get('display_value')}입니다.".strip()
                    for row in rows if row.get("display_value")
                )
                if text:
                    return HcxGeneratedAnswer(text, tuple(safe_ids[:5]))
        return None

    @staticmethod
    def _claim_metadata(
        claims: Sequence[HcxAnswerClaim], limitations: Sequence[str], trace: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "claim_support": [claim.to_dict() for claim in claims],
            "limitations": list(dict.fromkeys(str(item) for item in limitations if item)),
            "verification_trace": dict(trace),
        }

    @staticmethod
    def _allows_deterministic_fallback(
        tool_call: HcxToolCall, route: QuestionRoute | None,
    ) -> bool:
        if route is None:
            return False
        if route.workflow == "financial_statement_metric":
            return True
        return bool(
            tool_call.name == "get_financial_facts"
            and route.workflow in {"single", "financial_derived", "financial_comparison"}
        )

    def _verified_deterministic_fallback_result(
        self,
        *,
        tool_call: HcxToolCall,
        tool_response: Mapping[str, object],
        route: QuestionRoute | None,
        available_citations: Mapping[str, HcxEvidenceCitation],
        common: Mapping[str, object],
        warning_codes: Sequence[str],
        limitation_code: str,
        fallback_override: HcxGeneratedAnswer | None = None,
        metadata_updates: Mapping[str, object] | None = None,
    ) -> FunctionCallingResult:
        result_common = dict(common)
        result_common["metadata"] = dict(
            common.get("metadata") if isinstance(common.get("metadata"), Mapping) else {}
        )
        if metadata_updates:
            result_common["metadata"].update(metadata_updates)  # type: ignore[union-attr]
        admission = build_claim_admission(tool_response)
        fallback = (
            fallback_override
            or self._deterministic_claim_fallback(
                tool_response,
                admission,
                tuple(available_citations),
                route,
            )
        )
        if fallback is None:
            result_common["recommended_action"] = "abstain"
            return self._result(
                "abstained",
                UNANSWERABLE_TEXT,
                warnings=[*warning_codes, "deterministic_fallback_not_available"],
                **result_common,
            )
        citations = self._selected_citations(
            tool_call.name,
            fallback.citation_ids,
            available_citations,
        )
        citation_ids = tuple(dict.fromkeys(
            evidence_id
            for evidence_id in fallback.citation_ids
            if evidence_id in available_citations
        ))
        selected_facts = {
            fact_ref: fact
            for fact_ref, fact in admission.facts.items()
            if set(fact.evidence_ids).issubset(set(citation_ids))
        }
        selected_calculations = {
            calculation_ref: calculation
            for calculation_ref, calculation in admission.calculations.items()
            if set(calculation.evidence_ids).issubset(set(citation_ids))
        }
        owned_slots = tuple(dict.fromkeys(
            slot_id
            for item in (*selected_facts.values(), *selected_calculations.values())
            for slot_id in item.evidence_slot_ids
        ))
        fallback_claim = HcxAnswerClaim(
            "deterministic-fallback-1",
            fallback.answer,
            citation_ids,
            tuple(selected_facts),
            tuple(selected_calculations),
            owned_slots,
            tuple(dict.fromkeys(
                [
                    value
                    for fact in selected_facts.values()
                    for value in fact.numeric_values
                ] + [
                    value
                    for calculation in selected_calculations.values()
                    for value in calculation.numeric_values
                ]
            )),
        )
        limitations = (
            "historical_disclosure_only",
            "no_transaction_recommendation",
            "no_forecast",
            limitation_code,
        )
        verification = verify_generated_claims(
            answer=fallback.answer,
            citation_ids=citation_ids,
            raw_claims=(fallback_claim,),
            raw_limitations=limitations,
            admission=admission,
        )
        if not verification.verified or not citations:
            result_common["recommended_action"] = "abstain"
            result_common["metadata"].update(self._claim_metadata(  # type: ignore[union-attr]
                (), limitations, verification.trace("abstained"),
            ))
            return self._result(
                "abstained",
                UNANSWERABLE_TEXT,
                warnings=[
                    *warning_codes,
                    "deterministic_fallback_verification_failed",
                    *verification.failure_codes,
                ],
                **result_common,
            )
        result_common["metadata"].update({  # type: ignore[union-attr]
            "execution_mode": "deterministic",
            **self._claim_metadata(
                verification.claims,
                verification.limitations,
                verification.trace("fallback"),
            ),
        })
        return self._result(
            "answered",
            self._render_answer(fallback.answer, citations),
            answer_allowed=True,
            citation_ids=list(citation_ids),
            citations=[item.to_dict() for item in citations],
            warnings=list(warning_codes),
            **result_common,
        )

    @staticmethod
    def _requested_unit_response(
        route: QuestionRoute, response: Mapping[str, object],
    ) -> dict[str, object]:
        """Replace fact display strings with exact backend-rendered requested units."""

        output_unit = str(route.context.get("requested_output_unit") or "")
        if output_unit not in {"jo", "eok", "won"}:
            return dict(response)
        data = response.get("data")
        facts = data.get("facts") if isinstance(data, Mapping) else None
        if not isinstance(facts, list):
            return dict(response)
        rendered_facts: list[object] = []
        for raw_fact in facts:
            if not isinstance(raw_fact, Mapping):
                rendered_facts.append(raw_fact)
                continue
            fact = dict(raw_fact)
            rendered = format_financial_value(
                fact.get("value_numeric"), fact.get("scale") or 1,
                fact.get("unit") or "KRW", output_unit=output_unit,
            )
            if rendered:
                fact["display_value"] = rendered
                fact["requested_output_unit"] = output_unit
            rendered_facts.append(fact)
        return {**dict(response), "data": {**dict(data), "facts": rendered_facts}}

    def _merged_response(
        self,
        *,
        tool_name: str,
        request: Mapping[str, object],
        data: Mapping[str, object],
        responses: Sequence[Mapping[str, object]],
        checker_tool: str,
        checker_data: Mapping[str, object],
        required_response_indexes: Sequence[int] = (),
        extra_reasons: Sequence[str] = (),
        force_answer_allowed: bool | None = None,
    ) -> dict[str, object]:
        items: list[dict[str, object]] = []
        evidence_ids: list[str] = []
        filing_ids: list[str] = []
        issuer_codes: list[str] = []
        warnings: list[str] = []
        covered_scope: dict[str, object] = {}
        retrieval_status: dict[str, object] = {"route": "deterministic_multi_executor"}
        correction_statuses: list[str] = []
        for response in responses:
            bundle = self._bundle_from_response(response)
            raw_items = bundle.get("items")
            if isinstance(raw_items, list):
                items.extend(dict(item) for item in raw_items if isinstance(item, Mapping))
            for target, field_name in (
                (evidence_ids, "evidence_ids"),
                (filing_ids, "filing_ids"),
                (issuer_codes, "issuer_corp_codes"),
                (warnings, "quality_warnings"),
            ):
                values = bundle.get(field_name)
                if isinstance(values, (list, tuple)):
                    target.extend(str(item) for item in values if item)
            raw_covered = bundle.get("covered_scope")
            if isinstance(raw_covered, Mapping):
                for key, value in raw_covered.items():
                    if value not in (None, "", []):
                        existing = covered_scope.get(str(key))
                        if isinstance(existing, list) and isinstance(value, list):
                            covered_scope[str(key)] = list(dict.fromkeys([*existing, *value]))
                        else:
                            covered_scope[str(key)] = value
            raw_retrieval = bundle.get("retrieval_status")
            if isinstance(raw_retrieval, Mapping):
                retrieval_status[str(response.get("tool_name") or len(retrieval_status))] = dict(raw_retrieval)
            correction_status = bundle.get("correction_status")
            if correction_status:
                correction_statuses.append(str(correction_status))
            raw_warnings = response.get("warnings")
            if isinstance(raw_warnings, list):
                warnings.extend(str(item) for item in raw_warnings if item)

        merged_bundle = ToolEvidenceBundle(
            question_intent=tool_name,
            requested_scope=dict(request),
            covered_scope=covered_scope,
            items=items,
            evidence_ids=evidence_ids,
            filing_ids=filing_ids,
            issuer_corp_codes=issuer_codes,
            quality_warnings=warnings,
            correction_status=(
                "resolved" if "resolved" in correction_statuses
                else correction_statuses[0] if correction_statuses
                else "not_evaluated"
            ),
            retrieval_status=retrieval_status,
        )
        checked = self.evidence_checker.check(checker_tool, request, merged_bundle, checker_data).to_dict()
        required_failed = any(
            index >= len(responses)
            or self._sufficiency(responses[index]).get("answer_allowed") is not True
            for index in required_response_indexes
        )
        if required_failed or force_answer_allowed is False:
            checked = {
                "status": "insufficient",
                "reasons": list(dict.fromkeys([*checked.get("reasons", []), *extra_reasons])),
                "missing_requirements": list(dict.fromkeys([
                    *checked.get("missing_requirements", []),
                    "required_executor_evidence",
                ])),
                "recommended_action": "abstain",
                "answer_allowed": False,
            }
        elif force_answer_allowed is True and checked.get("answer_allowed") is True:
            checked["reasons"] = list(dict.fromkeys([*checked.get("reasons", []), *extra_reasons]))
        merged_bundle.sufficiency = str(checked["status"])
        status = "success" if checked["status"] == "sufficient" else "partial" if checked["answer_allowed"] else "insufficient"
        return {
            "status": status,
            "tool_name": tool_name,
            "data": dict(data),
            "evidence_bundle": merged_bundle.to_dict(),
            "warnings": list(dict.fromkeys(warnings)),
            "metadata": {
                "schema_version": "tool-response-v1",
                "backend_version": "tool-registry-v1",
                "executors": [str(response.get("tool_name") or "unknown") for response in responses],
                "sufficiency_check": checked,
            },
        }

    def _financial_derived_response(
        self, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        data = primary.get("data")
        facts = data.get("facts") if isinstance(data, Mapping) else None
        ordered = sorted(
            (dict(item) for item in facts if isinstance(item, Mapping)),
            key=self._fact_period_key,
        ) if isinstance(facts, list) else []
        operation = str(route.context.get("operation") or "")
        calculations: list[dict[str, object]] = []
        try:
            if operation == "growth_rate":
                if len(ordered) < 2:
                    raise ValueError("growth_rate_requires_two_periods")
                for previous, current in zip(ordered, ordered[1:]):
                    ids = [
                        str(item)
                        for fact in (previous, current)
                        for item in fact.get("evidence_ids", [])
                        if item
                    ]
                    result = calculate(
                        operation,
                        [previous.get("value_numeric"), current.get("value_numeric")],
                        unit="%",
                        evidence_ids=ids,
                    )
                    calculations.append({
                        **self._calculation_payload(result),
                        "from_period": previous.get("period"),
                        "to_period": current.get("period"),
                    })
            elif operation in {"difference", "ratio"}:
                if len(ordered) < 2:
                    raise ValueError(f"{operation}_requires_two_periods")
                selected = ordered[-2:]
                result = calculate(
                    operation,
                    [item.get("value_numeric") for item in selected],
                    unit=selected[-1].get("unit") if operation == "difference" else None,
                    evidence_ids=[
                        str(evidence_id)
                        for item in selected
                        for evidence_id in item.get("evidence_ids", [])
                        if evidence_id
                    ],
                )
                calculations.append(self._calculation_payload(result))
            elif operation == "sum":
                if not ordered:
                    raise ValueError("sum_requires_facts")
                result = calculate(
                    operation,
                    [item.get("value_numeric") for item in ordered],
                    unit=ordered[-1].get("unit"),
                    evidence_ids=[
                        str(evidence_id)
                        for item in ordered
                        for evidence_id in item.get("evidence_ids", [])
                        if evidence_id
                    ],
                )
                calculations.append(self._calculation_payload(result))
            else:
                raise ValueError("derived_operation_unsupported")
        except (TypeError, ValueError):
            calculations = []
        return self._merged_response(
            tool_name="get_financial_facts",
            request=route.arguments,
            data={**dict(data or {}), "calculations": calculations},
            responses=[primary],
            checker_tool="get_financial_facts",
            checker_data=dict(data or {}),
            required_response_indexes=(0,),
            extra_reasons=("backend_calculation_complete",) if calculations else ("backend_calculation_unavailable",),
            force_answer_allowed=bool(calculations),
        )

    @staticmethod
    def _fact_comparable_value(fact: Mapping[str, object]) -> Decimal | None:
        raw_normalized = fact.get("normalized_value")
        try:
            value = (
                Decimal(str(raw_normalized))
                if raw_normalized not in (None, "")
                else Decimal(str(fact.get("value_numeric"))) * Decimal(str(fact.get("scale") or 1))
            )
        except (InvalidOperation, ValueError):
            return None
        return value if value.is_finite() else None

    @classmethod
    def _calculation_grain_key(cls, fact: Mapping[str, object]) -> tuple[str, ...] | None:
        period = fact.get("period")
        identifiers = fact.get("company_identifiers")
        if not isinstance(period, Mapping) or not isinstance(identifiers, Mapping):
            return None
        company = next((
            str(identifiers.get(name) or "").strip()
            for name in ("issuer_corp_code", "stock_code", "listed_name", "issuer_name", "company")
            if str(identifiers.get(name) or "").strip()
        ), "")
        filing_id = str(fact.get("filing_id") or fact.get("rcept_no") or "").strip()
        scope = str(fact.get("scope") or "").strip()
        period_type = str(period.get("period_type") or "").strip()
        if period_type == "instant":
            period_key = str(period.get("instant_date") or "").strip()
        elif period_type == "duration":
            period_key = "|".join((
                str(period.get("period_start") or "").strip(),
                str(period.get("period_end") or "").strip(),
            ))
        else:
            return None
        if not all((company, filing_id, scope, period_key)):
            return None
        return company, filing_id, scope, period_type, period_key

    @classmethod
    def _facts_share_calculation_grain(
        cls, selected: list[tuple[dict[str, object], dict[str, object], Decimal]],
    ) -> bool:
        keys = [cls._calculation_grain_key(fact) for _, fact, _ in selected]
        return bool(keys) and all(key is not None for key in keys) and len(set(keys)) == 1

    def _financial_comparison_response(
        self, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        companies = [str(item) for item in route.context.get("companies", []) if item]
        periods = [str(item) for item in route.context.get("periods", []) if item]
        metrics = [str(item) for item in route.context.get("metrics", []) if item]
        if not metrics and route.context.get("metric"):
            metrics = [str(route.context["metric"])]
        raw_requirements = route.context.get("requirements")
        requirements = [
            dict(item) for item in raw_requirements if isinstance(item, Mapping)
        ] if isinstance(raw_requirements, list) else []
        if not requirements:
            requirements = [
                {"company": company, "period": periods[0] if len(periods) == 1 else None, "account": route.context.get("metric")}
                for company in companies
            ]
        responses: list[Mapping[str, object]] = [primary]
        for requirement in requirements[1:]:
            request = {
                **route.arguments,
                "company": requirement.get("company"),
                "account": requirement.get("account") or route.arguments.get("account"),
                "top_k": 1,
            }
            period = str(requirement.get("period") or "")
            if period:
                request.pop("instant_date", None)
                if requirement.get("instant_date"):
                    request.pop("start_date", None)
                    request.pop("end_date", None)
                    request["instant_date"] = requirement["instant_date"]
                elif requirement.get("start_date") or requirement.get("end_date"):
                    request.pop("start_date", None)
                    request.pop("end_date", None)
                    if requirement.get("start_date"):
                        request["start_date"] = requirement["start_date"]
                    if requirement.get("end_date"):
                        request["end_date"] = requirement["end_date"]
                else:
                    request.update({"start_date": f"{period}-01-01", "end_date": f"{period}-12-31"})
            else:
                request.pop("start_date", None)
                request.pop("end_date", None)
                request.pop("instant_date", None)
            responses.append(self.registry.dispatch("get_financial_facts", request))
        selected: list[tuple[dict[str, object], dict[str, object], Decimal]] = []
        all_facts: list[dict[str, object]] = []
        requirement_coverage: list[dict[str, object]] = []
        for requirement, response in zip(requirements, responses):
            data = response.get("data")
            facts = data.get("facts") if isinstance(data, Mapping) else None
            ordered = sorted(
                (dict(item) for item in facts if isinstance(item, Mapping)),
                key=self._fact_period_key,
            ) if isinstance(facts, list) else []
            expected_period = str(requirement.get("period") or "")
            matching = [
                fact for fact in ordered
                if not expected_period or self._fact_period_key(fact).startswith(expected_period)
            ]
            if len(matching) != 1:
                requirement_coverage.append({**requirement, "covered": False})
                continue
            fact = matching[0]
            value = self._fact_comparable_value(fact)
            if value is None or not fact.get("display_value"):
                requirement_coverage.append({**requirement, "covered": False})
                continue
            requirement_coverage.append({
                **requirement, "covered": True,
                "actual_period": self._fact_period_key(fact)[:4] or None,
            })
            selected.append((requirement, fact, value))
            all_facts.append(fact)
        calculations: list[dict[str, object]] = []
        calculation_failed = False
        calculation_grain_mismatch = False
        derived_operation = str(route.context.get("derived_operation") or "")
        accounting_identity: dict[str, object] | None = None
        if len(metrics) == 1 and len(companies) == 1 and len(periods) >= 2:
            ordered_selected = sorted(selected, key=lambda item: str(item[0].get("period") or ""))
            for (previous_requirement, previous, previous_value), (current_requirement, current, current_value) in zip(
                ordered_selected, ordered_selected[1:],
            ):
                evidence_ids = [
                    str(item)
                    for fact in (previous, current)
                    for item in fact.get("evidence_ids", [])
                    if item
                ]
                try:
                    difference = calculate(
                        "difference", [previous_value, current_value],
                        unit=current.get("unit"), evidence_ids=evidence_ids,
                    )
                    growth = calculate(
                        "growth_rate", [previous_value, current_value],
                        unit="%", evidence_ids=evidence_ids,
                    )
                except (TypeError, ValueError):
                    calculation_failed = True
                    break
                calculations.extend((
                    {
                        **self._calculation_payload(difference),
                        "company": companies[0],
                        "from_period": previous_requirement.get("period"),
                        "to_period": current_requirement.get("period"),
                    },
                    {
                        **self._calculation_payload(growth),
                        "company": companies[0],
                        "from_period": previous_requirement.get("period"),
                        "to_period": current_requirement.get("period"),
                    },
                ))
        # A catalog-declared ratio (부채비율, 영업이익률 …) reaches here with its
        # two source accounts already fetched, in (denominator, numerator)
        # order. Computing it from validated facts keeps the answer grounded in
        # the same evidence as the operands.
        if (
            derived_operation in {"percentage_ratio", "accounting_identity"}
            and selected
            and not self._facts_share_calculation_grain(selected)
        ):
            calculation_failed = True
            calculation_grain_mismatch = True
        if (
            derived_operation == "percentage_ratio"
            and len(selected) == 2
            and not calculation_grain_mismatch
        ):
            (_, denominator_fact, denominator_value), (_, numerator_fact, numerator_value) = selected
            evidence_ids = [
                str(item)
                for fact in (denominator_fact, numerator_fact)
                for item in fact.get("evidence_ids", [])
                if item
            ]
            try:
                ratio = calculate(
                    "percentage_ratio", [denominator_value, numerator_value],
                    unit="%", evidence_ids=evidence_ids,
                )
            except (TypeError, ValueError):
                # A zero or non-finite denominator is not an answerable ratio.
                calculation_failed = True
            else:
                calculations.append({
                    **self._calculation_payload(ratio),
                    "company": companies[0] if companies else None,
                    "metric": route.context.get("metric"),
                    "metric_id": route.context.get("metric_id"),
                    "period": route.context.get("period"),
                })
        if (
            derived_operation == "accounting_identity"
            and len(selected) == 3
            and not calculation_grain_mismatch
        ):
            metric_labels = [
                str(item) for item in route.context.get("metrics", []) if item
            ]
            metric_ids = [
                str(item) for item in route.context.get("metric_ids", []) if item
            ]
            account_ids_by_label = dict(zip(metric_labels, metric_ids))
            by_account = {
                str(
                    requirement.get("account_id")
                    or account_ids_by_label.get(str(requirement.get("account") or ""))
                    or ""
                ): (fact, value)
                for requirement, fact, value in selected
            }
            if set(by_account) == {"total_assets", "total_liabilities", "total_equity"}:
                assets_fact, assets = by_account["total_assets"]
                liabilities_fact, liabilities = by_account["total_liabilities"]
                equity_fact, equity = by_account["total_equity"]
                right_side_evidence_ids = [
                    str(item)
                    for fact in (liabilities_fact, equity_fact)
                    for item in fact.get("evidence_ids", [])
                    if item
                ]
                residual_evidence_ids = [
                    str(item)
                    for fact in (assets_fact, liabilities_fact, equity_fact)
                    for item in fact.get("evidence_ids", [])
                    if item
                ]
                try:
                    right_side = calculate(
                        "sum", [liabilities, equity], unit="KRW",
                        evidence_ids=right_side_evidence_ids,
                    )
                    residual = calculate(
                        "difference", [right_side.value, assets],
                        unit="KRW", evidence_ids=residual_evidence_ids,
                    )
                except (TypeError, ValueError):
                    calculation_failed = True
                else:
                    calculations.extend((
                        {**self._calculation_payload(right_side), "role": "liabilities_plus_equity"},
                        {**self._calculation_payload(residual), "role": "assets_minus_liabilities_and_equity"},
                    ))
                    accounting_identity = {
                        "status": "matches" if residual.value == 0 else "does_not_match",
                        "assets": format(assets, "f"),
                        "liabilities_plus_equity": format(right_side.value, "f"),
                        "difference": format(residual.value, "f"),
                        "unit": "KRW",
                    }

        if derived_operation == "percentage_ratio":
            operation_complete = len(calculations) == 1
        elif derived_operation == "accounting_identity":
            operation_complete = accounting_identity is not None
        else:
            operation_complete = (
                len(metrics) != 1
                or len(companies) != 1
                or len(periods) < 2
                or len(calculations) == 2 * (len(periods) - 1)
            )
        complete = (
            len(requirements) >= 2
            and len(selected) == len(requirements)
            and not calculation_failed
            and operation_complete
        )
        comparison: dict[str, object] = {
            "status": "complete" if complete else "incomplete",
            "companies": companies,
            "metric": route.context.get("metric"),
            "metrics": metrics,
            "period": route.context.get("period"),
            "periods": periods,
            "values": [
                {
                    "company": requirement.get("company"),
                    "period": requirement.get("period"),
                    "metric": requirement.get("account"),
                    "display_value": fact.get("display_value"),
                }
                for requirement, fact, _ in selected
            ],
            "calculations": calculations,
            "accounting_identity": accounting_identity,
            "winner": None,
        }
        if complete and len(metrics) == 1 and len(companies) >= 2 and len(periods) <= 1:
            maximum = max(value for _, _, value in selected)
            winners = [str(requirement.get("company")) for requirement, _, value in selected if value == maximum]
            comparison["winner"] = winners[0] if len(winners) == 1 else None
            comparison["tie"] = len(winners) > 1
        elif complete and len(metrics) == 1 and len(companies) == 1 and periods:
            maximum = max(value for _, _, value in selected)
            largest = [str(requirement.get("period")) for requirement, _, value in selected if value == maximum]
            comparison["largest_period"] = largest[0] if len(largest) == 1 else None
        checker_request = {
            "account": route.arguments.get("account"),
            "companies": companies,
            "periods": periods,
            "requirements": requirements,
        }
        return self._merged_response(
            tool_name="get_financial_facts",
            request=checker_request,
            data={
                "facts": all_facts,
                "comparison": comparison,
                "calculations": calculations,
                "requirement_coverage": requirement_coverage,
            },
            responses=responses,
            checker_tool="get_financial_facts",
            checker_data={"facts": all_facts, "requirement_coverage": requirement_coverage},
            required_response_indexes=tuple(range(len(responses))),
            extra_reasons=(
                ("all_financial_comparison_requirements_covered",)
                if complete
                else (
                    "financial_calculation_grain_mismatch",
                    "financial_comparison_requirement_missing",
                )
                if calculation_grain_mismatch
                else ("financial_comparison_requirement_missing",)
            ),
            force_answer_allowed=complete,
        )

    def _trend_response(
        self, question: str, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        summary_request = {
            "question": question,
            "correction_policy": "current",
            "top_k": 12,
            "max_chars": 8_000,
        }
        for name in ("company", "start_date", "end_date"):
            if route.arguments.get(name):
                summary_request[name] = route.arguments[name]
        content = self.registry.dispatch("build_summary_context", summary_request)
        primary_data = primary.get("data") if isinstance(primary.get("data"), Mapping) else {}
        return self._merged_response(
            tool_name="analyze_disclosure_trend",
            request=route.arguments,
            data={"quantitative_trend": dict(primary_data), "content_trend": dict(content.get("data") or {})},
            responses=[primary, content],
            checker_tool="analyze_disclosure_trend",
            checker_data=dict(primary_data),
            required_response_indexes=(0, 1),
            extra_reasons=("quantitative_and_content_evidence_merged",),
            force_answer_allowed=True,
        )

    def _change_reason_response(
        self, question: str, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        primary_data = primary.get("data") if isinstance(primary.get("data"), Mapping) else {}
        facts = primary_data.get("facts") if isinstance(primary_data, Mapping) else None
        ordered = sorted(
            (dict(item) for item in facts if isinstance(item, Mapping)),
            key=self._fact_period_key,
        ) if isinstance(facts, list) else []
        target_end = str(route.context.get("target_end_date") or "")
        if target_end:
            target_year = target_end[:4]
            indexes = [index for index, fact in enumerate(ordered) if self._fact_period_key(fact).startswith(target_year)]
            current_index = indexes[-1] if indexes else -1
        else:
            current_index = len(ordered) - 1
        calculations: list[dict[str, object]] = []
        if current_index <= 0:
            return self._merged_response(
                tool_name="get_financial_facts", request=route.arguments,
                data={**dict(primary_data), "calculations": [], "premise": "unverified"},
                responses=[primary], checker_tool="get_financial_facts", checker_data=dict(primary_data),
                required_response_indexes=(0,), extra_reasons=("comparison_period_missing",),
                force_answer_allowed=False,
            )
        previous, current = ordered[current_index - 1], ordered[current_index]
        previous_value = self._fact_comparable_value(previous)
        current_value = self._fact_comparable_value(current)
        if previous_value is None or current_value is None:
            return self._merged_response(
                tool_name="get_financial_facts", request=route.arguments,
                data={**dict(primary_data), "calculations": [], "premise": "unverified"},
                responses=[primary], checker_tool="get_financial_facts", checker_data=dict(primary_data),
                required_response_indexes=(0,), extra_reasons=("backend_normalized_value_unavailable",),
                force_answer_allowed=False,
            )
        evidence_ids = [
            str(item)
            for fact in (previous, current)
            for item in fact.get("evidence_ids", [])
            if item
        ]
        try:
            difference = calculate(
                "difference", [previous_value, current_value],
                unit="KRW", evidence_ids=evidence_ids,
            )
            growth = calculate(
                "growth_rate", [previous_value, current_value],
                unit="%", evidence_ids=evidence_ids,
            )
        except (TypeError, ValueError):
            return self._merged_response(
                tool_name="get_financial_facts", request=route.arguments,
                data={**dict(primary_data), "calculations": [], "premise": "unverified"},
                responses=[primary], checker_tool="get_financial_facts", checker_data=dict(primary_data),
                required_response_indexes=(0,), extra_reasons=("backend_calculation_unavailable",),
                force_answer_allowed=False,
            )
        calculations.extend((self._calculation_payload(difference), self._calculation_payload(growth)))
        if difference.value > 0:
            actual_direction = "increase"
        elif difference.value < 0:
            actual_direction = "decrease"
        else:
            actual_direction = "unchanged"
        claimed_direction = str(route.context.get("claimed_direction") or "") or None
        premise_confirmed = (
            actual_direction != "unchanged"
            if claimed_direction is None
            else claimed_direction == actual_direction
        )
        premise = (
            f"confirmed_{actual_direction}"
            if premise_confirmed and claimed_direction is not None
            else "change_confirmed"
            if premise_confirmed
            else f"{claimed_direction}_not_confirmed"
            if claimed_direction is not None
            else "change_not_confirmed"
        )
        base_data = {
            **dict(primary_data),
            "calculations": calculations,
            "premise": premise,
            "claimed_direction": claimed_direction,
            "actual_direction": actual_direction,
        }
        if not premise_confirmed:
            return self._merged_response(
                tool_name="get_financial_facts", request=route.arguments, data=base_data,
                responses=[primary], checker_tool="get_financial_facts", checker_data=dict(primary_data),
                required_response_indexes=(0,), extra_reasons=("question_premise_rejected_without_search",),
                force_answer_allowed=True,
            )
        search_request: dict[str, object] = {
            "question": question,
            "company": route.context.get("company"),
            "correction_policy": "current",
            "top_k": 10,
        }
        # target_start/end are fiscal periods, while search_disclosures dates
        # are filing dates. Do not exclude an FY2025 annual report filed in 2026.
        search = self.registry.dispatch("search_disclosures", search_request)
        return self._merged_response(
            tool_name="get_financial_facts", request=route.arguments,
            data={**base_data, "reason_evidence": dict(search.get("data") or {})},
            responses=[primary, search], checker_tool="get_financial_facts", checker_data=dict(primary_data),
            required_response_indexes=(0, 1), extra_reasons=("change_verified_before_reason_search",),
            force_answer_allowed=True,
        )

    def _correction_response(
        self, route: QuestionRoute, search: Mapping[str, object],
    ) -> tuple[HcxToolCall, dict[str, object]]:
        bundle = self._bundle_from_response(search)
        items = bundle.get("items")
        rows = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
        corrected = [item for item in rows if item.get("is_correction") is True]
        selected = corrected[0] if corrected else rows[0] if rows else None
        filing_id = str(selected.get("filing_id") or "") if selected is not None else ""
        if not filing_id:
            return HcxToolCall(f"call_{uuid.uuid4().hex[:24]}", "get_correction_lineage", {"filing_id": "00000000000000"}), self._merged_response(
                tool_name="get_correction_lineage", request=route.arguments,
                data={"search": dict(search.get("data") or {}), "original_filing": None, "corrected_filings": [], "current_filing": None, "lineage_confidence": "none"},
                responses=[search], checker_tool="get_correction_lineage", checker_data={},
                required_response_indexes=(0,), extra_reasons=("correction_receipt_not_found",),
                force_answer_allowed=False,
            )
        call = HcxToolCall(f"call_{uuid.uuid4().hex[:24]}", "get_correction_lineage", {"filing_id": filing_id})
        lineage = self.registry.dispatch(call.name, call.arguments)
        lineage_data = lineage.get("data") if isinstance(lineage.get("data"), Mapping) else {}
        return call, self._merged_response(
            tool_name="get_correction_lineage", request=call.arguments,
            data={**dict(lineage_data), "search": dict(search.get("data") or {})},
            responses=[search, lineage], checker_tool="get_correction_lineage", checker_data=dict(lineage_data),
            required_response_indexes=(1,), extra_reasons=("correction_search_and_lineage_merged",),
            force_answer_allowed=True,
        )

    def _document_summary_response(
        self, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        report_type = str(route.context.get("report_type") or "")
        bundle = self._bundle_from_response(primary)
        items = bundle.get("items")
        matched = [
            item for item in items
            if isinstance(item, Mapping) and report_type in str(item.get("report_name") or "")
        ] if isinstance(items, list) else []
        primary_data = primary.get("data") if isinstance(primary.get("data"), Mapping) else {}
        return self._merged_response(
            tool_name="build_summary_context",
            request=route.arguments,
            data={
                **dict(primary_data),
                "document_existence": "confirmed" if matched else "not_found",
                "requested_report_type": report_type,
            },
            responses=[primary],
            checker_tool="build_summary_context",
            checker_data=dict(primary_data),
            required_response_indexes=(0,),
            extra_reasons=("requested_document_confirmed",) if matched else ("requested_document_not_found",),
            force_answer_allowed=bool(matched),
        )

    def _event_disclosure_response(
        self, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        patterns = [
            re.sub(r"[^가-힣A-Za-z0-9]", "", str(item))
            for item in route.context.get("required_report_patterns", [])
            if item
        ]
        if not patterns:
            return dict(primary)
        bundle = self._bundle_from_response(primary)
        items = bundle.get("items")
        required_start = str(route.context.get("required_start_date") or "")
        required_end = str(route.context.get("required_end_date") or "")
        matched = [
            item for item in items
            if isinstance(item, Mapping)
            and any(
                pattern in re.sub(
                    r"[^가-힣A-Za-z0-9]", "", str(item.get("report_name") or "")
                )
                for pattern in patterns
            )
            and (
                not required_start
                or required_start <= str(item.get("filed_at") or "") <= required_end
            )
        ] if isinstance(items, list) else []
        if matched:
            evidence_ids = list(dict.fromkeys(
                str(evidence_id)
                for item in matched
                for evidence_id in (
                    item.get("evidence_ids")
                    if isinstance(item.get("evidence_ids"), (list, tuple))
                    else [item.get("evidence_id")]
                )
                if evidence_id
            ))
            filing_ids = list(dict.fromkeys(
                str(item.get("filing_id") or item.get("rcept_no"))
                for item in matched
                if item.get("filing_id") or item.get("rcept_no")
            ))
            primary_data = primary.get("data") if isinstance(primary.get("data"), Mapping) else {}
            return {
                **dict(primary),
                "data": {
                    **dict(primary_data),
                    "results": matched,
                    "document_existence": "confirmed",
                    "required_report_patterns": patterns,
                },
                "evidence_bundle": {
                    **dict(bundle),
                    "items": matched,
                    "evidence_ids": evidence_ids,
                    "filing_ids": filing_ids,
                },
            }
        primary_data = primary.get("data") if isinstance(primary.get("data"), Mapping) else {}
        return self._merged_response(
            tool_name="search_disclosures",
            request=route.arguments,
            data={
                **dict(primary_data),
                "document_existence": "not_found",
                "required_report_patterns": patterns,
            },
            responses=[primary],
            checker_tool="search_disclosures",
            checker_data=dict(primary_data),
            required_response_indexes=(0,),
            extra_reasons=("requested_event_report_not_found",),
            force_answer_allowed=False,
        )

    @staticmethod
    def _statement_item_matches(item: Mapping[str, object], route: QuestionRoute) -> bool:
        year = str(route.context.get("fiscal_year") or "")
        period = item.get("period")
        if not year or not isinstance(period, Mapping):
            return False
        start = str(period.get("period_start") or period.get("start") or "")
        end = str(period.get("period_end") or period.get("end") or period.get("instant_date") or "")
        if not end.startswith(year):
            return False
        report_name = str(item.get("report_name") or "")
        if route.context.get("period_kind") == "quarter":
            quarter = int(route.context.get("quarter") or 0)
            expected_month = {1: "03", 2: "06", 3: "09", 4: "12"}.get(quarter)
            if expected_month is None or end[5:7] != expected_month:
                return False
            if quarter != 4 and "분기보고서" not in report_name and "반기보고서" not in report_name:
                return False
        else:
            if "사업보고서" not in report_name or end[5:7] != "12" or not start.startswith(year):
                return False
        locator = item.get("locator")
        locator_fields = {str(key).casefold() for key in locator} if isinstance(locator, Mapping) else set()
        has_cell_location = bool(
            item.get("table_id")
            and locator_fields.intersection({"row", "row_index", "row_label", "column", "column_index", "column_label", "cell"})
        )
        return bool(
            item.get("structured_value") is not None
            and item.get("unit")
            and item.get("rcept_no")
            and has_cell_location
        )

    def _financial_statement_response(
        self, route: QuestionRoute, primary: Mapping[str, object],
    ) -> dict[str, object]:
        raw_bundle = self._bundle_from_response(primary)
        raw_items = raw_bundle.get("items")
        admitted = [
            dict(item) for item in raw_items
            if isinstance(item, Mapping) and self._statement_item_matches(item, route)
        ] if isinstance(raw_items, list) else []
        validated_values: list[dict[str, object]] = []
        for item in admitted:
            rendered = format_financial_value(
                item.get("structured_value"), item.get("scale") or 1, item.get("unit"),
            )
            if rendered:
                item["display_value"] = rendered
                validated_values.append({
                    "metric": route.context.get("metric"),
                    "period": item.get("period"),
                    "display_value": rendered,
                    "evidence_ids": list(item.get("evidence_ids") or []),
                    "rcept_no": item.get("rcept_no"),
                })
        admitted = [item for item in admitted if item.get("display_value")]
        evidence_ids = [
            str(evidence_id) for item in admitted
            for evidence_id in item.get("evidence_ids", []) if evidence_id
        ]
        filing_ids = [str(item.get("filing_id")) for item in admitted if item.get("filing_id")]
        context = "\n\n".join(
            f"[{item.get('rcept_no')}|{item.get('evidence_id')}] {item.get('text') or ''}"
            for item in admitted
        )
        bundle = ToolEvidenceBundle(
            question_intent="build_summary_context",
            requested_scope={**route.arguments, **route.context},
            covered_scope=dict(raw_bundle.get("covered_scope") or {}),
            items=admitted,
            evidence_ids=evidence_ids,
            filing_ids=filing_ids,
            issuer_corp_codes=list(raw_bundle.get("issuer_corp_codes") or []),
            quality_warnings=[] if admitted else ["financial_statement_cell_contract_not_met"],
            correction_status=str(raw_bundle.get("correction_status") or "not_evaluated"),
            retrieval_status=dict(raw_bundle.get("retrieval_status") or {}),
        )
        data = {
            "context": context,
            "context_chars": len(context),
            "result_count": len(admitted),
            "validated_statement_values": validated_values,
            "fiscal_year": route.context.get("fiscal_year"),
            "period_kind": route.context.get("period_kind"),
            "unavailable_reason": None if admitted else "row_column_unit_period_contract_not_met",
        }
        checked = self.evidence_checker.check(
            "build_summary_context", route.arguments, bundle, data,
        ).to_dict()
        if not admitted:
            checked.update({
                "status": "insufficient",
                "reasons": ["financial_statement_cell_contract_not_met"],
                "missing_requirements": ["row", "column", "unit", "fiscal_period", "rcept_no"],
                "recommended_action": "abstain",
                "answer_allowed": False,
            })
        bundle.sufficiency = str(checked["status"])
        return {
            "status": "success" if checked["answer_allowed"] else "insufficient",
            "tool_name": "build_summary_context",
            "data": data,
            "evidence_bundle": bundle.to_dict(),
            "warnings": list(bundle.quality_warnings),
            "metadata": {
                "schema_version": "tool-response-v1",
                "backend_version": "tool-registry-v1",
                "executors": ["build_summary_context"],
                "sufficiency_check": checked,
            },
        }

    def _execute_route(
        self, question: str, route: QuestionRoute, tool_call: HcxToolCall,
    ) -> tuple[HcxToolCall, dict[str, object]]:
        primary = self.registry.dispatch(tool_call.name, tool_call.arguments)
        if str(primary.get("status") or "error") in {"invalid_request", "error"}:
            return tool_call, primary
        if route.context.get("requested_output_unit"):
            primary = self._requested_unit_response(route, primary)
        if route.workflow == "financial_derived":
            return tool_call, self._financial_derived_response(route, primary)
        if route.workflow == "financial_comparison":
            return tool_call, self._financial_comparison_response(route, primary)
        if route.workflow == "trend_with_content":
            return tool_call, self._trend_response(question, route, primary)
        if route.workflow == "financial_change_reason":
            return tool_call, self._change_reason_response(question, route, primary)
        if route.workflow == "event_disclosure_check":
            return tool_call, self._event_disclosure_response(route, primary)
        if route.workflow == "correction_search_then_lineage":
            return self._correction_response(route, primary)
        if route.workflow == "document_summary_check":
            return tool_call, self._document_summary_response(route, primary)
        if route.workflow == "financial_statement_metric":
            return tool_call, self._financial_statement_response(route, primary)
        return tool_call, primary

    def answer(self, question: object) -> FunctionCallingResult:
        try:
            question = preflight_public_question(question)
        except QuestionInputError as exc:
            if exc.code in {
                "question_date_invalid",
                "question_conditions_contradictory",
                "question_duplicate_condition_changes_meaning",
            }:
                return self._result(
                    "abstained",
                    "질문의 날짜 또는 조건을 명확히 다시 입력해 주세요.",
                    recommended_action="ask_clarification",
                    warnings=[exc.code],
                    metadata={"tool_selection_called": False, "final_generation_called": False},
                )
            return self._result(
                "invalid_request", "질문 형식이 올바르지 않습니다.", warnings=[exc.code]
            )
        if len(question) > self.max_question_chars:
            return self._result(
                "invalid_request", "질문 형식이 올바르지 않습니다.", warnings=["question_too_long"]
            )
        if detect_prompt_injection(question):
            return self._result(
                "abstained",
                "질문에 실행 지시 변경 요청이 포함되어 처리할 수 없습니다.",
                recommended_action="abstain",
                warnings=["prompt_injection_detected"],
                metadata={
                    "provider_configured": bool(self.client.configured),
                    "route_source": "deterministic",
                    "route_reason": "prompt_injection_detected",
                    "tool_selection_called": False,
                    "final_generation_called": False,
                },
            )

        analysis_execution: BoundedAnalysisExecution | None = None
        if self.analysis_executor is not None:
            try:
                analysis_execution = self.analysis_executor.execute(question)
            except Exception as exc:
                diagnostics = self._record_failure("bounded_analysis", exc)
                return self._result(
                    "error",
                    UNANSWERABLE_TEXT,
                    recommended_action="abstain",
                    warnings=["bounded_analysis_failed_closed"],
                    metadata={
                        "provider_configured": bool(self.client.configured),
                        "route_source": "deterministic",
                        "route_reason": "bounded_analysis_failed_closed",
                        "tool_selection_called": False,
                        "final_generation_called": False,
                        **diagnostics,
                    },
                )
        if analysis_execution is not None and analysis_execution.plan.analysis_mode == "prohibited":
            reason = analysis_execution.reason_codes[0] if analysis_execution.reason_codes else "policy_refusal"
            message = (
                "공시 근거를 사용하더라도 매수·매도 추천은 제공하지 않습니다."
                if reason == "policy_recommendation_or_suitability_refusal"
                else "공시되지 않은 미래 수치나 전망은 제공하지 않습니다."
                if reason == "policy_forecast_refusal"
                else "해당 요청은 공시 분석 범위에서 처리할 수 없습니다."
            )
            return self._result(
                "abstained",
                message,
                recommended_action="abstain",
                warnings=list(analysis_execution.reason_codes),
                metadata={
                    "provider_configured": bool(self.client.configured),
                    "route_source": "deterministic",
                    "route_reason": reason,
                    "execution_mode": "policy_refusal",
                    "conclusion": None,
                    "tool_selection_called": False,
                    "final_generation_called": False,
                },
            )
        if analysis_execution is not None and not analysis_execution.complete:
            tool_response = analysis_execution.to_tool_response()
            return self._result(
                "abstained",
                "확인된 공시 근거가 필수 항목을 모두 충족하지 않아 판단을 보류합니다.",
                tool_name="build_summary_context",
                tool_response=tool_response,
                recommended_action="abstain",
                warnings=list(analysis_execution.reason_codes),
                metadata={
                    "provider_configured": bool(self.client.configured),
                    "route_source": "deterministic",
                    "route_reason": "mandatory_analysis_evidence_missing",
                    "tool_selection_called": False,
                    "final_generation_called": False,
                    **self._analysis_metadata(analysis_execution),
                },
            )

        route = (
            self.router.route(question)
            if analysis_execution is None and self.router is not None
            else None
        )
        if route is not None and route.kind in {"clarification", "unavailable"}:
            recommended_action = "ask_clarification" if route.kind == "clarification" else "abstain"
            bundle = ToolEvidenceBundle(
                question_intent=route.metric_kind.casefold(),
                requested_scope=dict(route.context),
                quality_warnings=[route.reason],
                correction_status="not_evaluated",
                sufficiency="insufficient",
            )
            tool_response = {
                "status": "insufficient",
                "tool_name": "resolver",
                "data": {
                    "ambiguity": dict(route.context) if route.kind == "clarification" else None,
                    "unavailable_reason": route.message if route.kind == "unavailable" else None,
                    "available_range": route.context.get("available_range"),
                },
                "evidence_bundle": bundle.to_dict(),
                "warnings": [route.reason],
                "metadata": {
                    "schema_version": "tool-response-v1",
                    "sufficiency_check": {
                        "status": "insufficient",
                        "reasons": [route.reason],
                        "missing_requirements": ["resolved_supported_question"],
                        "recommended_action": recommended_action,
                        "answer_allowed": False,
                    },
                },
            }
            return self._result(
                "abstained",
                route.message or "질문의 계정이나 범위를 더 구체적으로 알려주세요.",
                tool_response=tool_response,
                recommended_action=recommended_action,
                warnings=["deterministic_clarification" if route.kind == "clarification" else "deterministic_unavailable"],
                metadata={
                    "provider_configured": bool(self.client.configured),
                    "route_source": "deterministic",
                    "route_reason": route.reason,
                    "question_corrections": list(route.corrections),
                    "tool_selection_called": False,
                    "final_generation_called": False,
                },
            )

        tool_selection_called = analysis_execution is None and route is None
        if not self.client.configured and route is None:
            analysis_response = (
                analysis_execution.to_tool_response()
                if analysis_execution is not None
                else None
            )
            return self._result(
                "provider_unavailable", UNANSWERABLE_TEXT,
                tool_name="build_summary_context" if analysis_execution is not None else None,
                tool_response=analysis_response,
                warnings=["hcx_api_key_not_configured"],
                metadata={
                    "provider_configured": False,
                    "route_source": "deterministic" if route is not None or analysis_execution is not None else "provider",
                    "route_reason": (
                        "bounded_analysis"
                        if analysis_execution is not None
                        else route.reason if route is not None
                        else "provider_tool_selection"
                    ),
                    "question_corrections": list(route.corrections) if route is not None else [],
                    "tool_selection_called": False,
                    "final_generation_called": False,
                    **(
                        self._analysis_metadata(analysis_execution)
                        if analysis_execution is not None
                        else {}
                    ),
                },
            )
        if analysis_execution is not None:
            tool_call = HcxToolCall(
                f"call_{uuid.uuid4().hex[:24]}",
                "build_summary_context",
                {"question": question},
            )
        elif route is not None:
            tool_call = HcxToolCall(
                f"call_{uuid.uuid4().hex[:24]}",
                str(route.tool_name),
                route.arguments,
            )
        else:
            tools = self.available_tools()
            try:
                tool_call = self.client.select_tool(question, tools)
            except Exception as exc:
                diagnostics = self._record_failure("tool_selection", exc)
                return self._result(
                    "error", UNANSWERABLE_TEXT, warnings=["hcx_tool_selection_failed"],
                    metadata={"provider_configured": True, **diagnostics},
                )

        # Search/context Tools must execute the original user question.  A
        # malformed or omitted question still reaches Registry validation and
        # is rejected; only an already-string provider paraphrase is replaced.
        if isinstance(tool_call.arguments.get("question"), str):
            tool_call = HcxToolCall(
                tool_call.call_id,
                tool_call.name,
                {
                    **tool_call.arguments,
                    "question": (
                        route.normalized_question
                        if route is not None and route.normalized_question
                        else question
                    ),
                },
            )
        if analysis_execution is not None:
            tool_response = analysis_execution.to_tool_response()
        elif route is not None:
            tool_call, tool_response = self._execute_route(question, route, tool_call)
        else:
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
                "provider_configured": bool(self.client.configured),
                "model": self.client.model,
                "prompt_version": HCX_FUNCTION_PROMPT_VERSION,
                "route_source": "deterministic" if route is not None or analysis_execution is not None else "provider",
                "route_reason": (
                    "bounded_analysis"
                    if analysis_execution is not None
                    else route.reason if route is not None
                    else "provider_tool_selection"
                ),
                "metric_kind": (
                    "ANALYSIS" if analysis_execution is not None
                    else route.metric_kind if route is not None
                    else "SEARCH"
                ),
                "workflow": (
                    "bounded_analysis"
                    if analysis_execution is not None
                    else route.workflow if route is not None
                    else "function_calling_fallback"
                ),
                "question_corrections": list(route.corrections) if route is not None else [],
                "tool_selection_called": tool_selection_called,
                "final_generation_called": False,
                **(
                    self._analysis_metadata(analysis_execution)
                    if analysis_execution is not None
                    else {}
                ),
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
                "abstained", self._abstention_text(recommended_action, sufficiency),
                warnings=["answer_generation_blocked_by_sufficiency"], **common,
            )

        available_citations = self._available_citations(tool_call.name, tool_response)
        common["metadata"]["available_citation_count"] = len(available_citations)  # type: ignore[index]
        common["metadata"]["valid_rcept_no_count"] = len({  # type: ignore[index]
            item.rcept_no for item in available_citations.values()
        })
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

        if (
            route is not None
            and route.reason == "structured_financial_lookup"
            and any(marker in question for marker in _AMOUNT_VERIFICATION_MARKERS)
        ):
            available_ids = list(
                tool_response.get("evidence_bundle", {}).get("evidence_ids", [])  # type: ignore[union-attr]
            )
            verification = self._deterministic_amount_verification_answer(
                question, tool_response, available_ids,
            )
            if verification is not None:
                generated, claimed_amount_matches = verification
                return self._verified_deterministic_fallback_result(
                    tool_call=tool_call,
                    tool_response=tool_response,
                    route=route,
                    available_citations=available_citations,
                    common=common,
                    warning_codes=("deterministic_amount_verification",),
                    limitation_code="deterministic_amount_verification",
                    fallback_override=generated,
                    metadata_updates={
                        "deterministic_amount_verification_used": True,
                        "claimed_amount_matches": claimed_amount_matches,
                    },
                )

        if route is not None and route.workflow == "financial_change_reason":
            data = tool_response.get("data")
            premise = str(data.get("premise") or "") if isinstance(data, Mapping) else ""
            if premise.endswith("_not_confirmed"):
                common["metadata"]["deterministic_premise_correction_used"] = True  # type: ignore[index]
                return self._verified_deterministic_fallback_result(
                    tool_call=tool_call,
                    tool_response=tool_response,
                    route=route,
                    available_citations=available_citations,
                    common=common,
                    warning_codes=("question_premise_not_confirmed",),
                    limitation_code="question_premise_not_confirmed",
                )

        if not self.client.configured:
            if not self._allows_deterministic_fallback(tool_call, route):
                common["recommended_action"] = "abstain"
                return self._result(
                    "provider_unavailable",
                    UNANSWERABLE_TEXT,
                    warnings=["hcx_api_key_not_configured"],
                    **common,
                )
            return self._verified_deterministic_fallback_result(
                tool_call=tool_call,
                tool_response=tool_response,
                route=route,
                available_citations=available_citations,
                common=common,
                warning_codes=("provider_unavailable_deterministic_fallback",),
                limitation_code="provider_unavailable_deterministic_fallback",
            )

        common["metadata"]["final_generation_called"] = True  # type: ignore[index]
        failure_stage = "final_generation"
        try:
            generated = (
                self.client.generate_answer(question, tool_call, tool_response)
                if analysis_execution is not None
                else self.client.generate_routed_answer(question, tool_call, tool_response)
            )
        except Exception as exc:
            common["metadata"].update(  # type: ignore[union-attr]
                self._record_failure(failure_stage, exc, tool_name=tool_call.name)
            )
            if (
                isinstance(exc, HcxFunctionCallingError)
                and analysis_execution is None
                and self._allows_deterministic_fallback(tool_call, route)
            ):
                return self._verified_deterministic_fallback_result(
                    tool_call=tool_call,
                    tool_response=tool_response,
                    route=route,
                    available_citations=available_citations,
                    common=common,
                    warning_codes=(
                        "hcx_final_generation_failed",
                        "provider_failure_deterministic_fallback",
                    ),
                    limitation_code="provider_failure_deterministic_fallback",
                )
            common["recommended_action"] = "abstain"
            return self._result(
                "error", UNANSWERABLE_TEXT, warnings=["hcx_final_generation_failed"], **common,
            )

        failure_stage = "claim_verification"
        admission = build_claim_admission(tool_response)
        raw_claims = self._generated_claims(generated)
        limitations = tuple(
            str(item) for item in getattr(generated, "limitations", ()) if item
        ) or tuple(
            str(item) for item in (
                tool_response.get("data", {}).get("limitations", [])  # type: ignore[union-attr]
            ) if item
        ) or (
            "historical_disclosure_only",
            "no_transaction_recommendation",
            "no_forecast",
        )
        generated_text = str(getattr(generated, "answer", ""))
        generated_policy = classify_policy(generated_text)
        policy_ok = (
            generated_policy.action not in {"refuse_recommendation", "refuse_forecast"}
            and _FORECAST_CLAIM.search(generated_text) is None
            and _DART_RCEPT_NO_IN_TEXT.search(generated_text) is None
        )
        generated_conclusion = getattr(generated, "conclusion", None)
        response_data = tool_response.get("data")
        response_conclusion = (
            response_data.get("conclusion") if isinstance(response_data, Mapping) else None
        )
        conclusion_ok = True
        if analysis_execution is not None:
            conclusion_ok = (
                isinstance(generated_conclusion, str)
                and generated_conclusion != "insufficient_evidence"
                and generated_conclusion in analysis_execution.plan.allowed_conclusions
                and response_conclusion in (None, generated_conclusion)
            )
        elif generated_conclusion is not None:
            conclusion_ok = response_conclusion == generated_conclusion
        verification = verify_generated_claims(
            answer=generated_text,
            citation_ids=tuple(str(item) for item in getattr(generated, "citation_ids", ()) if item),
            raw_claims=raw_claims,
            raw_limitations=limitations,
            admission=admission,
            policy_ok=policy_ok,
            conclusion_ok=conclusion_ok,
        )
        if not verification.verified:
            available_ids = list(admission.evidence_ids)
            fallback = (
                None
                if analysis_execution is not None
                else self._deterministic_claim_fallback(
                    tool_response, admission, available_ids, route
                )
            )
            trace_status = "fallback" if fallback is not None else "abstained"
            common["metadata"].update(self._claim_metadata(  # type: ignore[union-attr]
                (),
                (*limitations, "provider_output_rejected"),
                verification.trace(trace_status),
            ))
            failure_warnings = [
                "claim_verification_failed", *verification.failure_codes,
            ]
            if analysis_execution is not None:
                safe_data = dict(response_data) if isinstance(response_data, Mapping) else {}
                safe_data["conclusion"] = "insufficient_evidence"
                tool_response = {**tool_response, "data": safe_data}
                common["tool_response"] = tool_response
                common["metadata"]["conclusion"] = "insufficient_evidence"  # type: ignore[index]
            if fallback is None:
                common["recommended_action"] = "abstain"
                return self._result(
                    "abstained",
                    UNANSWERABLE_TEXT,
                    warnings=failure_warnings,
                    **common,
                )
            citations = self._selected_citations(
                tool_call.name, fallback.citation_ids, available_citations
            )
            if not citations:
                common["recommended_action"] = "abstain"
                return self._result(
                    "abstained",
                    UNANSWERABLE_TEXT,
                    warnings=failure_warnings,
                    **common,
                )
            citation_ids = tuple(item.evidence_id for item in citations)
            owned_slots = tuple(
                slot_id for slot_id, evidence_ids in admission.evidence_slots.items()
                if set(citation_ids).issubset(set(evidence_ids))
            ) or tuple(admission.evidence_slots)
            fallback_claim = HcxAnswerClaim(
                "deterministic-fallback-1",
                fallback.answer,
                citation_ids,
                tuple(admission.facts),
                tuple(admission.calculations),
                owned_slots,
                tuple(dict.fromkeys(
                    value
                    for fact in admission.facts.values()
                    for value in fact.numeric_values
                )),
            )
            common["metadata"].update(self._claim_metadata(  # type: ignore[union-attr]
                (fallback_claim,),
                (*limitations, "provider_output_rejected", "deterministic_fallback"),
                verification.trace("fallback"),
            ))
            return self._result(
                "answered",
                self._render_answer(fallback.answer, citations),
                answer_allowed=True,
                citation_ids=list(citation_ids),
                citations=[item.to_dict() for item in citations],
                warnings=failure_warnings,
                **common,
            )

        verified_text = "\n".join(claim.text for claim in verification.claims)
        citations = self._selected_citations(
            tool_call.name,
            tuple(str(item) for item in getattr(generated, "citation_ids", ()) if item),
            available_citations,
        )
        if not citations:
            common["recommended_action"] = "abstain"
            common["metadata"].update(self._claim_metadata(  # type: ignore[union-attr]
                (), (*limitations, "provider_output_rejected"),
                verification.trace("abstained"),
            ))
            return self._result(
                "abstained", UNANSWERABLE_TEXT,
                warnings=["claim_verification_failed", "citation_receipt_missing"],
                **common,
            )
        if analysis_execution is not None:
            safe_data = dict(response_data) if isinstance(response_data, Mapping) else {}
            safe_data["conclusion"] = generated_conclusion
            tool_response = {**tool_response, "data": safe_data}
            common["tool_response"] = tool_response
            common["metadata"]["conclusion"] = generated_conclusion  # type: ignore[index]
        common["metadata"].update(self._claim_metadata(  # type: ignore[union-attr]
            verification.claims,
            verification.limitations,
            verification.trace("verified"),
        ))
        return self._result(
            "answered", self._render_answer(verified_text, citations), answer_allowed=True,
            citation_ids=[item.evidence_id for item in citations],
            citations=[item.to_dict() for item in citations], **common,
        )

    @staticmethod
    def _deterministic_financial_answer(
        tool_response: Mapping[str, object],
        available_evidence_ids: Sequence[str],
    ) -> HcxGeneratedAnswer | None:
        data = tool_response.get("data")
        facts = data.get("facts") if isinstance(data, Mapping) else None
        if not isinstance(facts, list) or not facts or not available_evidence_ids:
            return None
        sentences: list[str] = []
        for raw_fact in facts[:5]:
            if not isinstance(raw_fact, Mapping) or raw_fact.get("value_numeric") is None:
                continue
            identifiers = raw_fact.get("company_identifiers")
            company = identifiers.get("company") if isinstance(identifiers, Mapping) else None
            account_name = raw_fact.get("account_name") or raw_fact.get("account_id") or "재무 수치"
            period = raw_fact.get("period")
            period_label = ""
            if isinstance(period, Mapping):
                instant = str(period.get("instant_date") or "")
                start = str(period.get("period_start") or "")
                end = str(period.get("period_end") or "")
                if instant:
                    period_label = instant
                elif start and end and start[:4] == end[:4]:
                    period_label = f"{end[:4]}년"
                elif start or end:
                    period_label = "~".join(item for item in (start, end) if item)
            scope = {"consolidated": "연결", "separate": "별도"}.get(str(raw_fact.get("scope") or ""), "")
            subject = " ".join(str(item) for item in (company, period_label, scope, account_name) if item)
            display_value = str(raw_fact.get("display_value") or "")
            if not display_value:
                continue
            sentences.append(f"{subject}은 {display_value}입니다.")
        if not sentences:
            return None
        answer = sentences[0] if len(sentences) == 1 else "\n".join(f"- {item}" for item in sentences)
        return HcxGeneratedAnswer(answer, tuple(available_evidence_ids[:5]))

    @classmethod
    def _deterministic_amount_verification_answer(
        cls,
        question: str,
        tool_response: Mapping[str, object],
        available_evidence_ids: Sequence[str],
    ) -> tuple[HcxGeneratedAnswer, bool] | None:
        claimed_amounts = parse_korean_krw_amounts(question)
        unit_tokens = list(_AMOUNT_UNIT_TOKEN.finditer(question))
        if len(claimed_amounts) != 1 or not unit_tokens or not available_evidence_ids:
            return None
        data = tool_response.get("data")
        facts = data.get("facts") if isinstance(data, Mapping) else None
        ordered = sorted(
            (dict(item) for item in facts if isinstance(item, Mapping)),
            key=cls._fact_period_key,
        ) if isinstance(facts, list) else []
        candidates = [
            fact for fact in ordered
            if fact.get("support_level") == "structured"
            and fact.get("validation_status") == "validated"
        ]
        if len(candidates) != 1:
            return None
        fact = candidates[0]
        actual = normalized_financial_value(
            fact.get("value_numeric"), fact.get("scale") or 1, fact.get("unit") or "KRW",
        )
        display_value = str(fact.get("display_value") or "") or format_financial_value(
            fact.get("value_numeric"), fact.get("scale") or 1, fact.get("unit") or "KRW",
        )
        if actual is None or not display_value:
            return None
        claimed = claimed_amounts[0]
        if re.search(r"(?:-|마이너스)\s*[0-9]", question):
            claimed = -claimed
        precision_text = unit_tokens[-1].group(1).replace(",", "")
        decimal_places = len(precision_text.partition(".")[2])
        rounding_step = _AMOUNT_UNIT_FACTOR[unit_tokens[-1].group(2)] / (Decimal(10) ** decimal_places)
        claimed_amount_matches = abs(actual - claimed) <= rounding_step / 2

        identifiers = fact.get("company_identifiers")
        company = str(identifiers.get("company") or "") if isinstance(identifiers, Mapping) else ""
        period = cls._fact_period_key(fact)[:4]
        account = str(fact.get("account_name") or fact.get("account_id") or "재무 수치")
        scope = {"consolidated": "연결", "separate": "별도"}.get(str(fact.get("scope") or ""), "")
        subject = " ".join(item for item in (company, f"{period}년" if period else "", scope, account) if item)
        if claimed_amount_matches:
            answer = f"네. 공시 수치 기준으로 {subject}은 {display_value}입니다."
        else:
            answer = (
                f"아니요. 질문에 제시된 금액은 공시 수치와 일치하지 않습니다. "
                f"{subject}은 {display_value}입니다."
            )
        fact_evidence_ids = [
            str(item) for item in fact.get("evidence_ids", [])
            if str(item) in available_evidence_ids
        ]
        if not fact_evidence_ids:
            return None
        return HcxGeneratedAnswer(answer, tuple(fact_evidence_ids)), claimed_amount_matches

    @staticmethod
    def _deterministic_financial_comparison_answer(
        tool_response: Mapping[str, object],
        available_evidence_ids: Sequence[str],
    ) -> HcxGeneratedAnswer | None:
        data = tool_response.get("data")
        comparison = data.get("comparison") if isinstance(data, Mapping) else None
        if not isinstance(comparison, Mapping) or comparison.get("status") != "complete" or not available_evidence_ids:
            return None
        values = comparison.get("values")
        rows = [item for item in values if isinstance(item, Mapping)] if isinstance(values, list) else []
        if len(rows) < 2:
            return None
        lines = [
            f"{row.get('company')} {row.get('period')}년 "
            f"{row.get('metric') or comparison.get('metric') or '재무 수치'}은 {row.get('display_value')}입니다."
            for row in rows
            if row.get("company") and row.get("period") and row.get("display_value")
        ]
        if len(lines) != len(rows):
            return None
        largest_period = comparison.get("largest_period")
        winner = comparison.get("winner")
        metric = str(comparison.get("metric") or "재무 수치")
        if largest_period:
            lines.append(f"따라서 {largest_period}년 {metric}이 더 큽니다.")
        elif winner:
            lines.append(f"따라서 {winner}의 {metric}이 더 큽니다.")
        calculations = comparison.get("calculations")
        calculation_rows = [item for item in calculations if isinstance(item, Mapping)] if isinstance(calculations, list) else []
        difference = next((item for item in calculation_rows if item.get("operation") == "difference"), None)
        growth = next((item for item in calculation_rows if item.get("operation") == "growth_rate"), None)
        if difference is not None and difference.get("value") is not None:
            display_difference = format_financial_value(str(difference["value"]), 1, str(difference.get("unit") or "KRW"))
            if display_difference:
                lines.append(f"기간 차이는 {display_difference}입니다.")
        if growth is not None and growth.get("value") is not None:
            try:
                growth_value = Decimal(str(growth["value"]))
            except (InvalidOperation, ValueError):
                pass
            else:
                lines.append(f"증가율은 약 {growth_value:.2f}%입니다.")
        ratio = next((item for item in calculation_rows if item.get("operation") == "percentage_ratio"), None)
        if ratio is not None and ratio.get("value") is not None:
            try:
                ratio_value = Decimal(str(ratio["value"]))
            except (InvalidOperation, ValueError):
                pass
            else:
                lines.append(f"{metric}은 약 {ratio_value:.2f}%입니다.")
        identity = comparison.get("accounting_identity")
        if isinstance(identity, Mapping):
            right_side = format_financial_value(
                identity.get("liabilities_plus_equity"), 1, identity.get("unit") or "KRW",
            )
            difference_value = format_financial_value(
                identity.get("difference"), 1, identity.get("unit") or "KRW",
            )
            if right_side and identity.get("status") == "matches":
                lines.append(f"부채총계와 자본총계의 합은 {right_side}으로 자산총계와 일치합니다.")
            elif right_side and difference_value:
                lines.append(
                    f"부채총계와 자본총계의 합은 {right_side}이며, "
                    f"자산총계와의 차이는 {difference_value}입니다."
                )
        return HcxGeneratedAnswer("\n".join(lines), tuple(available_evidence_ids[:5]))

    @classmethod
    def _deterministic_change_reason_answer(
        cls,
        route: QuestionRoute,
        tool_response: Mapping[str, object],
        available_evidence_ids: Sequence[str],
    ) -> HcxGeneratedAnswer | None:
        data = tool_response.get("data")
        facts = data.get("facts") if isinstance(data, Mapping) else None
        if not isinstance(facts, list) or len(facts) < 2 or not available_evidence_ids:
            return None
        ordered = sorted(
            (dict(item) for item in facts if isinstance(item, Mapping)),
            key=cls._fact_period_key,
        )
        target_end = str(route.context.get("target_end_date") or "")
        if target_end:
            target_year = target_end[:4]
            indexes = [
                index for index, fact in enumerate(ordered)
                if cls._fact_period_key(fact).startswith(target_year)
            ]
            current_index = indexes[-1] if indexes else -1
        else:
            current_index = len(ordered) - 1
        if current_index <= 0:
            return None
        previous, current = ordered[current_index - 1], ordered[current_index]
        previous_display = str(previous.get("display_value") or "") or format_financial_value(
            previous.get("value_numeric"), previous.get("scale") or 1,
            previous.get("unit") or "KRW",
        )
        current_display = str(current.get("display_value") or "") or format_financial_value(
            current.get("value_numeric"), current.get("scale") or 1,
            current.get("unit") or "KRW",
        )
        if not previous_display or not current_display:
            return None
        claimed = str(route.context.get("claimed_direction") or "change")
        actual = str(data.get("actual_direction") or "unchanged") if isinstance(data, Mapping) else "unchanged"
        direction_ko = {"increase": "증가", "decrease": "감소", "unchanged": "변동 없음"}
        claimed_ko = direction_ko.get(claimed, "변동")
        actual_ko = direction_ko.get(actual, "변동 없음")
        previous_period = cls._fact_period_key(previous)[:4]
        current_period = cls._fact_period_key(current)[:4]
        identifiers = current.get("company_identifiers")
        company = str(identifiers.get("company") or "") if isinstance(identifiers, Mapping) else ""
        account = str(current.get("account_name") or current.get("account_id") or "재무 수치")
        subject = " ".join(item for item in (company, account) if item)
        answer = (
            f"질문의 {claimed_ko} 전제는 공시 수치와 일치하지 않습니다. "
            f"{subject}은 {previous_period}년 {previous_display}에서 "
            f"{current_period}년 {current_display}로 {actual_ko}했습니다. "
            f"따라서 {claimed_ko} 원인은 검색하지 않았습니다."
        )
        return HcxGeneratedAnswer(answer, tuple(available_evidence_ids[:5]))

    @staticmethod
    def _deterministic_trend_answer(
        tool_response: Mapping[str, object],
        available_evidence_ids: Sequence[str],
    ) -> HcxGeneratedAnswer | None:
        data = tool_response.get("data")
        if not isinstance(data, Mapping) or not available_evidence_ids:
            return None
        total_count = data.get("total_count")
        if not isinstance(total_count, int) or total_count <= 0:
            return None
        bundle = tool_response.get("evidence_bundle")
        requested_scope = bundle.get("requested_scope") if isinstance(bundle, Mapping) else None
        company = requested_scope.get("company") if isinstance(requested_scope, Mapping) else None
        requested_range = data.get("requested_range")
        start_date = requested_range.get("start_date") if isinstance(requested_range, Mapping) else None
        end_date = requested_range.get("end_date") if isinstance(requested_range, Mapping) else None

        period_rows = data.get("count_by_period")
        period_parts = [
            f"{row.get('period')} {row.get('count')}건"
            for row in period_rows
            if isinstance(row, Mapping) and row.get("period") and row.get("count") is not None
        ] if isinstance(period_rows, list) else []
        type_labels = {
            "periodic": "정기공시",
            "major": "주요사항공시",
            "exchange": "거래소공시",
            "holding": "지주회사공시",
        }
        type_rows = data.get("count_by_type")
        type_parts = [
            f"{type_labels.get(str(row.get('filing_type')), str(row.get('filing_type')))} {row.get('count')}건"
            for row in type_rows
            if isinstance(row, Mapping) and row.get("filing_type") and row.get("count") is not None
        ] if isinstance(type_rows, list) else []

        subject = str(company or "해당 회사")
        range_label = "~".join(str(item) for item in (start_date, end_date) if item)
        sentences = [f"{subject}의 {range_label or '요청 기간'} 공시는 총 {total_count}건입니다."]
        if period_parts:
            sentences.append("월별 건수는 " + ", ".join(period_parts) + "입니다.")
        if type_parts:
            sentences.append("유형별 건수는 " + ", ".join(type_parts) + "입니다.")
        if data.get("coverage_complete") is not True:
            sentences.append("요청 기간 전체가 아니라 현재 데이터에 수록된 공시 기준입니다.")
        return HcxGeneratedAnswer(" ".join(sentences), tuple(available_evidence_ids[:5]))

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
        for evidence_id in identifiers:
            citation = available.get(evidence_id)
            if citation is None:
                continue
            selected.append(citation)
        return selected

    @staticmethod
    def _render_answer(answer: str, citations: Sequence[HcxEvidenceCitation]) -> str:
        displayed: list[HcxEvidenceCitation] = []
        seen_receipts: set[str] = set()
        for citation in citations:
            if citation.rcept_no in seen_receipts:
                continue
            seen_receipts.add(citation.rcept_no)
            displayed.append(citation)
        lines = [answer.strip(), "", "근거 공시"]
        if len(displayed) == 1:
            citation = displayed[0]
            label = " ".join(item for item in (citation.correction_role, citation.report_name) if item)
            lines.extend([f"- {label or 'DART 공시'}", f"- 접수번호: {citation.rcept_no}"])
        else:
            for index, citation in enumerate(displayed, start=1):
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
    def _abstention_text(action: str, sufficiency: Mapping[str, object] | None = None) -> str:
        if action == "ask_clarification":
            return "근거 범위를 확정할 수 있도록 회사, 계정 또는 기간을 더 구체적으로 알려주세요."
        missing = {str(item) for item in (sufficiency or {}).get("missing_requirements") or []}
        if "validated_structured_financial_fact" in missing:
            return (
                "요청하신 회사·계정·기간의 검증된 재무 수치가 공시 코퍼스에서 확인되지 않아 "
                "답변을 보류합니다. 회계연도나 연결/별도 범위를 바꿔 다시 시도해 주세요. "
                "금융지주·보험사 손익계산서에는 매출액 계정이 별도로 표시되지 않을 수 있습니다."
            )
        if "search_results" in missing or "citable_evidence" in missing:
            return (
                "요청과 일치하는 공시 근거를 코퍼스에서 찾지 못해 답변을 보류합니다. "
                "회사명이나 검색어를 바꾸어 다시 시도해 주세요."
            )
        return UNANSWERABLE_TEXT

    def _record_failure(
        self,
        fallback_stage: str,
        error: Exception,
        *,
        tool_name: str | None = None,
    ) -> dict[str, object]:
        """Log content-free diagnostics and return the same safe trace fields."""

        stage = fallback_stage
        error_code = "hcx_unexpected_error"
        http_status: int | None = None
        provider_error_code: str | None = None
        if isinstance(error, HcxFunctionCallingError):
            stage = error.stage or fallback_stage
            error_code = error.code
            http_status = error.http_status
            provider_error_code = error.provider_error_code
        elif _SAFE_DIAGNOSTIC_TOKEN.fullmatch(str(error)):
            error_code = str(error)
        diagnostics: dict[str, object] = {
            "error_stage": stage,
            "error_type": type(error).__name__,
            "error_code": error_code,
        }
        if http_status is not None:
            diagnostics["http_status"] = http_status
        if provider_error_code is not None:
            diagnostics["provider_error_code"] = provider_error_code
        event = {
            "event": "hcx_function_calling_failure",
            "prompt_version": HCX_FUNCTION_PROMPT_VERSION,
            **diagnostics,
        }
        if tool_name and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(tool_name):
            event["tool_name"] = tool_name
        _LOGGER.warning(
            "hcx_function_calling_failure %s",
            json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        return diagnostics

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
    "FINAL_ANSWER_TOOL_NAME",
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
