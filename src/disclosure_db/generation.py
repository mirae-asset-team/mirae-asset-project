"""Grounded answer generation: deterministic fallback first, HCX only when configured."""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .agent_contracts import AnswerDraft, EvidenceBundle, to_jsonable
from .financial_accounts import attach_particle


UNANSWERABLE_TEXT = "검증 가능한 근거가 충분하지 않아 답변할 수 없습니다."


def _display_decimal(value: object) -> str:
    """Group digits for prose while leaving verifier numeric_values untouched."""
    raw = str(value)
    try:
        numeric = Decimal(raw)
    except InvalidOperation:
        return raw
    if not numeric.is_finite():
        return raw
    fixed = format(numeric, "f")
    whole, separator, fraction = fixed.partition(".")
    grouped = f"{int(whole):,}"
    return grouped + (separator + fraction if separator else "")


def _known_ids(bundle: EvidenceBundle, requested: Iterable[str]) -> list[str]:
    requested_ids = set(requested)
    return list(dict.fromkeys(ref.evidence_id for ref in bundle.evidence if ref.evidence_id in requested_ids))


def _claim_citation_ids(bundle: EvidenceBundle) -> list[str]:
    if bundle.calculation is not None:
        return _known_ids(bundle, bundle.calculation.evidence_ids)
    if bundle.financial_facts:
        facts = bundle.financial_facts if len(bundle.financial_facts) > 1 else bundle.financial_facts[:1]
        return _known_ids(bundle, [evidence_id for fact in facts for evidence_id in fact.get("evidence_ids", [])])
    if bundle.event_facts:
        facts = bundle.event_facts if len(bundle.event_facts) > 1 else bundle.event_facts[:1]
        return _known_ids(
            bundle,
            [evidence_id for fact in facts for evidence_id in fact.get("evidence_ids", [])],
        )
    return [bundle.evidence[0].evidence_id] if bundle.evidence else []


def parse_hcx_json_content(content: Any) -> Any:
    """Parse one JSON object from an HCX response without accepting ambiguity."""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError("hcx_output_not_json")

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{" and depth == 0:
            start = index
            depth = 1
        elif character == "{" and depth > 0:
            depth += 1
        elif character == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:index + 1])
                start = None
    if len(candidates) != 1:
        raise ValueError("hcx_output_json_ambiguous")
    return json.loads(candidates[0])


# Retain the former private name for callers on the existing generator path.
_parse_hcx_content = parse_hcx_json_content


class DeterministicGenerator:
    def generate(self, bundle: EvidenceBundle) -> AnswerDraft:
        if not bundle.answerable or not bundle.evidence:
            return AnswerDraft(answer=UNANSWERABLE_TEXT, answerable=False, reason_codes=list(bundle.reason_codes) + ["insufficient_evidence"])
        citations = _claim_citation_ids(bundle)
        if (bundle.calculation is not None or bundle.financial_facts or bundle.event_facts) and not citations:
            return AnswerDraft(
                answer=UNANSWERABLE_TEXT,
                citation_ids=[],
                answerable=False,
                reason_codes=list(bundle.reason_codes) + ["claim_evidence_missing"],
            )
        numeric_values: list[str] = []
        aggregate_operation = str(bundle.aggregate_result.get("operation") or "")
        if aggregate_operation in {"list_above", "rank"}:
            companies = list(bundle.aggregate_result.get("companies", []))
            numeric_values = [str(item.get("value_numeric")) for item in companies if item.get("value_numeric") is not None]
            rows = ", ".join(
                f"{index}. {item.get('listed_name')} ({item.get('fiscal_year')}년, {item.get('value_numeric')}×{item.get('scale')}, {('연결' if item.get('scope') == 'consolidated' else '별도')})"
                for index, item in enumerate(companies, 1)
            )
            label = "순위" if aggregate_operation == "rank" else "기업 목록"
            answer = f"검증된 전체 기업 기준 {attach_particle(label, '은')} {rows}입니다."
        elif bundle.calculation and bundle.calculation.value is not None:
            numeric_values = [str(bundle.calculation.value)]
            answer = f"계산 결과는 {bundle.calculation.value} {bundle.calculation.unit or ''}입니다.".strip()
        elif bundle.financial_facts:
            facts = list(bundle.financial_facts)
            fact = facts[0]
            numeric_values = [str(item.get("value_numeric", "")) for item in facts]
            scope = "연결" if fact.get("scope") == "consolidated" else "별도" if fact.get("scope") == "separate" else "범위 미상"
            if len(facts) > 1:
                values = ", ".join(
                    f"{item.get('fiscal_year') or str(item.get('period_end') or item.get('instant_date') or '')[:4]}년 {_display_decimal(item.get('value_numeric'))} {item.get('unit_raw') or item.get('currency') or ''}".strip()
                    for item in facts
                )
                answer = f"{fact.get('account_name_raw', '해당 항목')} 최근 {len(facts)}개년({scope})은 {values}입니다."
            else:
                year = fact.get("fiscal_year") or str(fact.get("period_end") or fact.get("instant_date") or "")[:4]
                answer = f"{year}년 {fact.get('account_name_raw', '해당 항목')}({scope})은 {_display_decimal(fact.get('value_numeric'))} {fact.get('unit_raw') or fact.get('currency') or ''}입니다.".strip()
        elif bundle.event_facts:
            facts = list(bundle.event_facts)
            fact = facts[0]
            values = [str(item.get("value_numeric")) for item in facts if item.get("value_numeric") is not None]
            value = str(fact.get("value_numeric") or fact.get("value_raw") or "")
            numeric_values = values
            if len(values) > 1:
                answer = "확인된 값은 " + ", ".join(values) + "입니다."
            elif fact.get("answer_kind") == "text":
                answer = value
            else:
                answer = f"{fact.get('predicate_raw') or fact.get('predicate_id')}은(는) {value} {fact.get('unit') or ''}입니다.".strip()
        else:
            answer = bundle.evidence[0].text.strip() or UNANSWERABLE_TEXT
        return AnswerDraft(answer=answer, citation_ids=citations, numeric_values=numeric_values, answerable=True)


class HyperClovaGenerator:
    """Minimal HCX-007-compatible adapter with deterministic fallback.

    The adapter is opt-in: without CLOVASTUDIO_API_KEY it never makes a network call.  The
    prompt contains only retrieved evidence, and the verifier remains authoritative.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        v3_base_url: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
    ):
        self.api_key = api_key or os.getenv("CLOVASTUDIO_API_KEY")
        self.base_url = (base_url or os.getenv("CLOVASTUDIO_BASE_URL") or "https://clovastudio.stream.ntruss.com/v1/openai").rstrip("/")
        self.v3_base_url = (
            v3_base_url
            or os.getenv("CLOVASTUDIO_V3_BASE_URL")
            or "https://clovastudio.stream.ntruss.com/v3/chat-completions"
        ).rstrip("/")
        self.model = model or os.getenv("CLOVASTUDIO_MODEL") or "HCX-005"
        self.timeout = timeout
        self.fallback = DeterministicGenerator()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, bundle: EvidenceBundle) -> AnswerDraft:
        if not self.api_key:
            return self.fallback.generate(bundle)
        if not bundle.answerable or not bundle.evidence:
            return self.fallback.generate(bundle)
        structured = bool(bundle.financial_facts or bundle.event_facts or bundle.calculation is not None)
        answer_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "citation_ids": {"type": "array", "items": {"type": "string"}},
                "numeric_values": {"type": "array", "items": {"type": "string"}},
                "answerable": {"type": "boolean"},
            },
            "required": ["answer", "citation_ids", "numeric_values", "answerable"],
        }
        messages = [
            {"role": "system", "content": "한국어 공시 분석가. 제공된 근거만 사용하고 JSON만 반환한다."},
            {"role": "user", "content": json.dumps({
                "question": bundle.question,
                "evidence": [{"evidence_id": ref.evidence_id, "text": ref.text} for ref in bundle.evidence],
                "financial_facts": to_jsonable(bundle.financial_facts),
                "event_facts": to_jsonable(bundle.event_facts),
                "calculation": to_jsonable(bundle.calculation),
                "schema": {"answer": "string", "citation_ids": ["string"], "numeric_values": ["string"], "answerable": True},
            }, ensure_ascii=False)},
        ]
        if self.model == "HCX-007":
            payload = {
                "messages": messages,
                "topP": 0.8,
                "topK": 0,
                "maxCompletionTokens": 512,
                "temperature": 0,
                "repetitionPenalty": 1.1,
                "thinking": {"effort": "none"},
                "stop": [],
                "responseFormat": {"type": "json", "schema": answer_schema},
            }
            endpoint = f"{self.v3_base_url}/{self.model}"
        else:
            payload = {"model": self.model, "temperature": 0, "messages": messages}
            endpoint = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = (
                data["result"]["message"]["content"]
                if self.model == "HCX-007"
                else data["choices"][0]["message"]["content"]
            )
            parsed: dict[str, Any] = parse_hcx_json_content(content)
            required_keys = {"answer", "citation_ids", "numeric_values", "answerable"}
            if not isinstance(parsed, dict) or set(parsed) != required_keys:
                raise ValueError("hcx_output_schema_mismatch")
            if (
                not isinstance(parsed["answer"], str)
                or not isinstance(parsed["citation_ids"], list)
                or not all(isinstance(item, str) for item in parsed["citation_ids"])
                or not isinstance(parsed["numeric_values"], list)
                or not all(isinstance(item, str) for item in parsed["numeric_values"])
                or not isinstance(parsed["answerable"], bool)
            ):
                raise ValueError("hcx_output_schema_mismatch")
            return AnswerDraft(
                answer=str(parsed.get("answer", UNANSWERABLE_TEXT)),
                citation_ids=[str(item) for item in parsed.get("citation_ids", [])],
                numeric_values=[str(item) for item in parsed.get("numeric_values", [])],
                answerable=parsed["answerable"],
            )
        except Exception:
            # Structured values have a deterministic, evidence-bound fallback. A
            # generic text response must abstain rather than echoing retrieved text.
            if structured:
                fallback = self.fallback.generate(bundle)
                return replace(fallback, reason_codes=[*fallback.reason_codes, "hcx_fallback"])
            return AnswerDraft(
                answer=UNANSWERABLE_TEXT,
                answerable=False,
                reason_codes=[*bundle.reason_codes, "hcx_unavailable_for_text"],
            )
