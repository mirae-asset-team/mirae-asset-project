"""Deterministic policy classification and disclosure-analysis planning."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .analysis_contracts import AnalysisPlan, EvidenceSlot, PolicyDecision, QueryPlanSnapshot
from .query_planner import plan_query


_DEFAULT_DIMENSIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "analysis_dimensions.json"
_PROMPT_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "이전 지시를 무시",
    "지시를 무시",
    "시스템 프롬프트",
)
_ANALYSIS_MARKERS = ("공시", "사업보고서", "분기보고서", "반기보고서", "판단", "분석", "개선", "악화", "위험요인")
_DIRECT_TRANSACTION_ACTION = re.compile(
    r"(?:"
    r"사(?:도|면)?(?:돼요?|되(?:나|나요|는지|요)?|될(?:까|까요)?)"
    r"|살(?:까|까요)"
    r"|사는게(?:좋(?:을까|을까요)|나을까|나을까요|될까|될까요)"
    r"|팔(?:아도|면)?(?:돼요?|되(?:나|나요|는지|요)?|될(?:까|까요)?)"
    r"|팔(?:까|까요)"
    r"|파는게(?:좋(?:을까|을까요)|나을까|나을까요|될까|될까요)"
    r")"
)
_DISCLOSURE_SOURCE_TEXT = r"(?:공시|(?:사업|분기|반기)?보고서)"
_DISCLOSURE_SOURCE = re.compile(_DISCLOSURE_SOURCE_TEXT)
_QUOTED_TEXT_PATTERNS = (
    re.compile(r'"(?P<mention>[^"]+)"'),
    re.compile(r"'(?P<mention>[^']+)'"),
    re.compile(r"“(?P<mention>[^”]+)”"),
    re.compile(r"‘(?P<mention>[^’]+)’"),
)
_NEUTRAL_QUOTED_MENTION_TAIL = re.compile(
    rf"\s*(?:(?:라는|이란)\s*)?(?:문구|표현|기재|언급|내용)"
    rf"\s*(?:을|를|이|가|은|는)?\s*"
    rf"(?P<source>{_DISCLOSURE_SOURCE_TEXT}\s*(?:에|에서|의)?\s*)?"
    rf"(?P<retrieval>찾|검색|조회|확인|있는지|있었는지|나오는지|포함(?:됐|되었|되어|된|되)?는지)"
)
_REPORTED_TRANSACTION_MENTION_TAIL = re.compile(
    rf"\s*(?:검토|논의|확인|언급|기재)(?:한|했던|된|됐던)\s*"
    rf"(?:내용|문구|표현|기록|대목)\s*(?:을|를|이|가|은|는)?\s*"
    rf"(?P<source>{_DISCLOSURE_SOURCE_TEXT}\s*(?:에|에서|의)?\s*)?"
    rf"(?P<retrieval>찾|검색|조회|확인|있는지|있었는지|나오는지)"
)
_ADVISORY_CONTINUATION_TEXT = (
    r"(?:"
    r"(?:추천|조언)\s*(?:(?:을|를|도)\s*)?(?:해|하|부탁|말|줘)"
    r"|(?:(?:내|네|제|저의|당신의)\s*)?(?:판단|결론|의견|생각)"
    r"\s*(?:도|을|를|은|는)?\s*(?:말|알려|내|제시|해|줘)"
    r"|(?:(?:그|이|해당)\s*(?:(?:질문|내용|문구|표현)\s*)?(?:에|에는|대해)?\s*)?"
    r"(?:답해|답변|대답)"
    r")"
)
_ADVISORY_CONTINUATION = re.compile(_ADVISORY_CONTINUATION_TEXT)
_NEXT_CLAUSE_ADVISORY_CONTINUATION = re.compile(
    rf"\s*(?:(?:그리고|이어서|또)\s*)?{_ADVISORY_CONTINUATION_TEXT}"
)
_CLAUSE_BOUNDARIES = ".!?。！？\n"
_RECOMMENDATION_PATTERNS = (
    r"목표\s*주가",
    r"(?:주가|가격).{0,20}(?:오를|내릴|상승|하락|방향|전망|예상)",
    r"(?:예상|기대).{0,20}(?:수익률|수익)",
    r"(?:수익률|수익).{0,20}(?:예상|기대)",
    r"(?:매수|매도|보유|매집|매각|매입).{0,12}(?:해야|할까|해도|추천|의견|결론)",
    r"(?:사야|팔아야|들어가도|투자해도|포지션|비중|포트폴리오|숏|롱)",
    r"(?:나에게|저에게|개인\s*투자자|투자\s*성향|위험\s*감수).{0,20}(?:적합|맞|추천|투자)",
)


def _normalized_intent_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def _normalized_intent_view(text: str) -> tuple[str, str, tuple[int, ...]]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    compact_chars: list[str] = []
    source_offsets: list[int] = []
    for offset, character in enumerate(normalized):
        if re.match(r"[\s\W_]", character):
            continue
        compact_chars.append(character)
        source_offsets.append(offset)
    return normalized, "".join(compact_chars), tuple(source_offsets)


def _clause_start(text: str, offset: int) -> int:
    return max((text.rfind(boundary, 0, offset) for boundary in _CLAUSE_BOUNDARIES), default=-1) + 1


def _clause_end(text: str, offset: int) -> int:
    boundaries = tuple(
        position
        for boundary in _CLAUSE_BOUNDARIES
        if (position := text.find(boundary, offset)) >= 0
    )
    return min(boundaries, default=len(text))


def _has_disclosure_source_before(text: str, offset: int) -> bool:
    return bool(_DISCLOSURE_SOURCE.search(text[_clause_start(text, offset):offset]))


def _has_advisory_continuation_after(text: str, offset: int) -> bool:
    current_clause_end = _clause_end(text, offset)
    if _ADVISORY_CONTINUATION.search(text[offset:current_clause_end]):
        return True
    if current_clause_end == len(text):
        return False
    next_clause_start = current_clause_end + 1
    next_clause_end = _clause_end(text, next_clause_start)
    return bool(
        _NEXT_CLAUSE_ADVISORY_CONTINUATION.match(
            text[next_clause_start:next_clause_end]
        )
    )


def _neutral_quoted_transaction_ranges(text: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for pattern in _QUOTED_TEXT_PATTERNS:
        for quoted in pattern.finditer(text):
            mention = quoted["mention"]
            if not _DIRECT_TRANSACTION_ACTION.search(_normalized_intent_text(mention)):
                continue
            tail = _NEUTRAL_QUOTED_MENTION_TAIL.match(text, quoted.end())
            if tail is None:
                continue
            if not (_has_disclosure_source_before(text, quoted.start("mention")) or tail["source"]):
                continue
            if _has_advisory_continuation_after(text, tail.start("retrieval")):
                continue
            ranges.append((quoted.start("mention"), quoted.end("mention")))
    return tuple(ranges)


def _is_neutral_transaction_mention(
    text: str,
    start: int,
    end: int,
    quoted_ranges: tuple[tuple[int, int], ...],
) -> bool:
    if any(quoted_start <= start and end <= quoted_end for quoted_start, quoted_end in quoted_ranges):
        return True
    tail = _REPORTED_TRANSACTION_MENTION_TAIL.match(text, end)
    return (
        tail is not None
        and bool(_has_disclosure_source_before(text, start) or tail["source"])
        and not _has_advisory_continuation_after(text, tail.start("retrieval"))
    )


def _is_direct_transaction_action(text: str) -> bool:
    normalized, compact, source_offsets = _normalized_intent_view(text)
    quoted_ranges = _neutral_quoted_transaction_ranges(normalized)
    for match in _DIRECT_TRANSACTION_ACTION.finditer(compact):
        start = source_offsets[match.start()]
        end = source_offsets[match.end() - 1] + 1
        if not _is_neutral_transaction_mention(normalized, start, end, quoted_ranges):
            return True
    return False


def _unique_strings(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field_name} must be a non-empty list of strings")
    return tuple(value)


def load_dimension_catalog(path: str | Path | None = None) -> tuple[Mapping[str, Any], ...]:
    """Load the declarative catalog and reject ambiguous dimension or slot IDs."""
    catalog_path = Path(path) if path is not None else _DEFAULT_DIMENSIONS_PATH
    with catalog_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    dimensions = data.get("dimensions") if isinstance(data, dict) else None
    if not isinstance(dimensions, list):
        raise ValueError("analysis dimension catalog must contain a dimensions list")

    dimension_ids: set[str] = set()
    slot_ids: set[str] = set()
    validated: list[Mapping[str, Any]] = []
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise ValueError("analysis dimension must be an object")
        dimension_id = dimension.get("dimension_id")
        if not isinstance(dimension_id, str) or not dimension_id or dimension_id in dimension_ids:
            raise ValueError("analysis dimension IDs must be unique non-empty strings")
        dimension_ids.add(dimension_id)
        if not dimension.get("match_any") and not dimension.get("match_all"):
            raise ValueError(f"analysis dimension {dimension_id} needs match_any or match_all")
        _unique_strings(dimension.get("subquestions"), field_name=f"{dimension_id}.subquestions")
        _unique_strings(dimension.get("allowed_conclusions"), field_name=f"{dimension_id}.allowed_conclusions")
        slots = dimension.get("slots")
        if not isinstance(slots, list) or not slots:
            raise ValueError(f"analysis dimension {dimension_id} needs slots")
        for slot in slots:
            if not isinstance(slot, dict):
                raise ValueError(f"analysis dimension {dimension_id} slot must be an object")
            slot_id = slot.get("slot_id")
            if not isinstance(slot_id, str) or not slot_id or slot_id in slot_ids:
                raise ValueError("analysis slot IDs must be unique non-empty strings")
            slot_ids.add(slot_id)
            if slot.get("domain") not in {"financial", "event", "text"}:
                raise ValueError(f"analysis slot {slot_id} has an invalid domain")
            _unique_strings(slot.get("search_concepts"), field_name=f"{slot_id}.search_concepts")
            if not isinstance(slot.get("min_evidence"), int) or not isinstance(slot.get("max_evidence"), int):
                raise ValueError(f"analysis slot {slot_id} evidence bounds must be integers")
            if slot["min_evidence"] < 0 or slot["max_evidence"] < slot["min_evidence"]:
                raise ValueError(f"analysis slot {slot_id} has invalid evidence bounds")
        validated.append(dimension)
    return tuple(validated)


def classify_policy(question: str) -> PolicyDecision:
    """Apply recommendation, injection, and bounded-analysis policy in precedence order."""
    text = question.strip()
    folded = text.casefold()
    if _is_direct_transaction_action(text) or any(re.search(pattern, text) for pattern in _RECOMMENDATION_PATTERNS):
        return PolicyDecision("refuse_recommendation", ("policy_recommendation_or_suitability_refusal",))
    if any(marker in folded for marker in _PROMPT_INJECTION_MARKERS):
        return PolicyDecision("refuse_prompt_injection", ("policy_prompt_injection_refusal",))
    if any(marker in text for marker in _ANALYSIS_MARKERS):
        return PolicyDecision("allow_analysis", ("policy_historical_disclosure_analysis",))
    return PolicyDecision("allow_lookup", ("policy_bounded_lookup",))


def _matches_dimension(text: str, dimension: Mapping[str, Any]) -> bool:
    match_all = dimension.get("match_all", [])
    if match_all and all(term in text for term in match_all):
        return True
    return any(term in text for term in dimension.get("match_any", []))


def _make_slot(slot: Mapping[str, Any], *, issuer: str | None, base_plan: Any) -> EvidenceSlot:
    return EvidenceSlot(
        slot_id=slot["slot_id"],
        domain=slot["domain"],
        issuer=issuer,
        period_start=base_plan.period_start,
        period_end=base_plan.period_end,
        instant_date=base_plan.instant_date,
        filing_date=base_plan.filing_date,
        report_types=tuple(slot.get("report_types", ())),
        search_concepts=tuple(slot["search_concepts"]),
        min_evidence=slot["min_evidence"],
        max_evidence=slot["max_evidence"],
        mandatory=bool(slot["mandatory"]),
        absence_reason_code=slot["absence_reason_code"],
    )


def _ordered_reason_codes(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for group in groups for code in group))


def plan_analysis(
    question: str,
    *,
    company_candidates: Iterable[str] = (),
    company_hint: str | None = None,
    as_of: str | None = None,
) -> AnalysisPlan:
    """Create a bounded plan; prohibited requests receive no evidence slots."""
    text = question.strip()
    policy = classify_policy(text)
    base_plan = QueryPlanSnapshot.from_query_plan(plan_query(
        text,
        company_candidates=company_candidates,
        company_hint=company_hint,
        as_of=as_of,
    ))
    if policy.action.startswith("refuse_"):
        return AnalysisPlan(
            question=text,
            analysis_mode="prohibited",
            policy=policy,
            base_plan=base_plan,
            reason_codes=policy.reason_codes,
        )
    if policy.action == "allow_lookup":
        return AnalysisPlan(
            question=text,
            analysis_mode="lookup",
            policy=policy,
            base_plan=base_plan,
            reason_codes=_ordered_reason_codes(policy.reason_codes, base_plan.reason_codes),
        )

    catalog = load_dimension_catalog()
    dimension = next(
        (
            item
            for item in catalog
            if item["dimension_id"] == "correction_materiality" and _matches_dimension(text, item)
        ),
        None,
    )
    if dimension is None:
        dimension = next((item for item in catalog if _matches_dimension(text, item)), None)
    if dimension is None:
        return AnalysisPlan(
            question=text,
            analysis_mode="lookup",
            policy=policy,
            base_plan=base_plan,
            reason_codes=_ordered_reason_codes(policy.reason_codes, base_plan.reason_codes),
        )

    slots = tuple(_make_slot(slot, issuer=base_plan.company, base_plan=base_plan) for slot in dimension["slots"])
    return AnalysisPlan(
        question=text,
        analysis_mode="judgment",
        policy=policy,
        base_plan=base_plan,
        subquestions=tuple(dimension["subquestions"]),
        required_evidence_slots=slots,
        judgment_dimension=dimension["dimension_id"],
        allowed_conclusions=tuple(dimension["allowed_conclusions"]),
        max_evidence=dimension["max_evidence"],
        reason_codes=_ordered_reason_codes(policy.reason_codes, base_plan.reason_codes),
    )
