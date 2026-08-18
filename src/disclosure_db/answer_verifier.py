"""Post-generation safety gate for citations and numeric claims."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .agent_contracts import AnswerDraft, CalculationResult, CitationRef, EvidenceBundle, VerifiedAnswer


PROMPT_INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "system prompt", "developer message",
    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
)
VERIFIED_ABSTENTION = "검증에 실패하여 답변을 보류합니다."


def _canonical_failure(
    *,
    citation_ids: list[str],
    citations: list[CitationRef],
    reason_codes: list[str],
    calculation: CalculationResult | None,
) -> VerifiedAnswer:
    """Build the one safe shape used by every failed or unanswerable result."""
    return VerifiedAnswer(
        answer=VERIFIED_ABSTENTION,
        citation_ids=citation_ids,
        verified=False,
        answerable=False,
        reason_codes=reason_codes,
        numeric_values=[],
        citations=citations,
        calculation=calculation,
    )


def verify_answer(bundle: EvidenceBundle, draft: AnswerDraft) -> VerifiedAnswer:
    allowed = {ref.evidence_id for ref in bundle.evidence}
    reasons = list(draft.reason_codes)
    valid = True
    if not set(draft.citation_ids).issubset(allowed):
        valid = False
        reasons.append("unknown_citation")
    if draft.answerable and not draft.citation_ids:
        valid = False
        reasons.append("citation_missing")
    if draft.answerable and not bundle.answerable:
        valid = False
        reasons.append("answer_asserted_without_safe_evidence")
    if not draft.answerable:
        valid = False
        reasons.append("unanswerable_draft")
    if any(marker in draft.answer.casefold() for marker in PROMPT_INJECTION_MARKERS):
        valid = False
        reasons.append("prompt_injection_detected")
    if not draft.answerable and draft.citation_ids:
        valid = False
        reasons.append("unanswerable_answer_has_citation")
    structured_facts = [*bundle.financial_facts, *bundle.event_facts]
    structured_evidence_ids = {
        str(evidence_id)
        for fact in structured_facts
        for evidence_id in fact.get("evidence_ids", [])
    }
    if bundle.answerable and structured_facts:
        invalid_structured_evidence = any(
            not (declared_ids := {str(evidence_id) for evidence_id in fact.get("evidence_ids", [])})
            or not declared_ids.intersection(allowed)
            or not declared_ids.issubset(allowed)
            for fact in structured_facts
        )
        required_structured_ids = (
            set(bundle.calculation.evidence_ids)
            if bundle.calculation and bundle.calculation.evidence_ids
            else structured_evidence_ids
        )
        if invalid_structured_evidence or not draft.citation_ids or not required_structured_ids.issubset(set(draft.citation_ids)):
            valid = False
            reasons.append("structured_citation_missing")
    if bundle.calculation and not set(bundle.calculation.evidence_ids).issubset(set(draft.citation_ids)):
        valid = False
        reasons.append("calculation_citation_missing")
    if bundle.calculation and draft.numeric_values and bundle.calculation.value is not None:
        try:
            if any(Decimal(value) != bundle.calculation.value for value in draft.numeric_values):
                valid = False
                reasons.append("numeric_claim_mismatch")
        except InvalidOperation:
            valid = False
            reasons.append("numeric_claim_not_decimal")
    numeric_request = any(term in bundle.question for term in ("얼마", "금액", "몇", "수량", "가격", "증가율", "성장률", "증감률", "비율"))
    trusted_values = {
        str(fact.get("value_numeric"))
        for fact in [*bundle.financial_facts, *bundle.event_facts]
        if fact.get("value_numeric") is not None
    }
    if bundle.calculation and bundle.calculation.value is not None:
        trusted_values.add(str(bundle.calculation.value))
    if draft.numeric_values:
        if not trusted_values:
            valid = False
            reasons.append("numeric_claim_not_grounded")
        else:
            try:
                if any(not any(Decimal(value) == Decimal(trusted) for trusted in trusted_values) for value in draft.numeric_values):
                    valid = False
                    reasons.append("numeric_claim_not_grounded")
            except InvalidOperation:
                valid = False
                reasons.append("numeric_claim_not_decimal")
    required_claim_terms = [term for term in ("승인", "완료", "체결", "해지", "변경") if term in bundle.question]
    if bundle.answerable and required_claim_terms:
        if any(not any(term in ref.text for ref in bundle.evidence) for term in required_claim_terms):
            valid = False
            reasons.append("required_claim_term_missing")
    if numeric_request and bundle.answerable:
        if not draft.numeric_values:
            valid = False
            reasons.append("numeric_claim_missing")
    # Citation metadata is corpus-owned.  Provider output can nominate IDs only;
    # all fields are hydrated from the already-admitted evidence intersection.
    safe_citation_ids: list[str] = []
    seen_citations: set[str] = set()
    for item in draft.citation_ids:
        item = str(item)
        if item in allowed and item not in seen_citations:
            safe_citation_ids.append(item)
            seen_citations.add(item)
    evidence_by_id = {ref.evidence_id: ref for ref in bundle.evidence}
    citations = [
        CitationRef(
            evidence_id=evidence_by_id[item].evidence_id,
            filing_id=evidence_by_id[item].filing_id,
            report_name=evidence_by_id[item].report_name,
            filed_at=evidence_by_id[item].filed_at,
            locator=dict(evidence_by_id[item].locator),
        )
        for item in safe_citation_ids
    ]
    stable_reasons: list[str] = []
    for reason in reasons:
        if reason not in stable_reasons:
            stable_reasons.append(reason)
    if not valid or not draft.answerable:
        return _canonical_failure(
            citation_ids=safe_citation_ids,
            citations=citations,
            reason_codes=stable_reasons,
            calculation=bundle.calculation,
        )
    return VerifiedAnswer(
        answer=draft.answer,
        citation_ids=safe_citation_ids,
        verified=True,
        answerable=True,
        reason_codes=stable_reasons,
        numeric_values=list(draft.numeric_values),
        citations=citations,
        calculation=bundle.calculation,
    )
