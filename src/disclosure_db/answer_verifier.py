"""Post-generation safety gate for citations and numeric claims."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .agent_contracts import AnswerDraft, EvidenceBundle, VerifiedAnswer


def verify_answer(bundle: EvidenceBundle, draft: AnswerDraft) -> VerifiedAnswer:
    allowed = {ref.evidence_id for ref in bundle.evidence}
    reasons = list(draft.reason_codes)
    valid = True
    if not set(draft.citation_ids).issubset(allowed):
        valid = False
        reasons.append("unknown_citation")
    if draft.answerable and not bundle.answerable:
        valid = False
        reasons.append("answer_asserted_without_safe_evidence")
    if not draft.answerable and draft.citation_ids:
        valid = False
        reasons.append("unanswerable_answer_has_citation")
    if bundle.calculation and draft.numeric_values and bundle.calculation.value is not None:
        try:
            if any(Decimal(value) != bundle.calculation.value for value in draft.numeric_values):
                valid = False
                reasons.append("numeric_claim_mismatch")
        except InvalidOperation:
            valid = False
            reasons.append("numeric_claim_not_decimal")
    return VerifiedAnswer(
        answer=draft.answer if valid else "검증에 실패하여 답변을 보류합니다.",
        citation_ids=[item for item in draft.citation_ids if item in allowed],
        verified=valid,
        answerable=draft.answerable and valid,
        reason_codes=reasons,
        numeric_values=list(draft.numeric_values),
    )
