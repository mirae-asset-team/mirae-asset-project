"""Fail-closed bridge from bounded analysis plans to public HCX responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .agent_contracts import EvidenceRef, to_jsonable
from .analysis_contracts import AnalysisPlan, EvidenceSlot
from .analysis_planner import plan_analysis
from .freeform_retrieval import AnalysisRetrieval
from .tool_contracts import ToolEvidenceBundle


class AnalysisEvidenceService(Protocol):
    def company_candidates(self) -> list[str]: ...

    def search_analysis(self, plan: AnalysisPlan, *, limit: int = 20) -> AnalysisRetrieval: ...


def _ordered(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if value))


def _fact_evidence_ids(fact: Mapping[str, object]) -> set[str]:
    values = fact.get("evidence_ids")
    return {
        str(value) for value in values if value
    } if isinstance(values, (list, tuple)) else set()


def _financial_period(fact: Mapping[str, object]) -> str:
    fiscal_year = fact.get("fiscal_year")
    if isinstance(fiscal_year, int) and 1900 <= fiscal_year <= 2200:
        return str(fiscal_year)
    return str(
        fact.get("period_end")
        or fact.get("instant_date")
        or fact.get("period_start")
        or ""
    )[:4]


def _financial_value(fact: Mapping[str, object]) -> Decimal | None:
    if fact.get("validation_status") not in {None, "validated"}:
        return None
    try:
        value = Decimal(str(fact.get("value_numeric")))
        scale = Decimal(str(fact.get("scale") or 1))
    except (InvalidOperation, ValueError):
        return None
    result = value * scale
    return result if result.is_finite() else None


def _profitability_conclusion(
    facts: Sequence[Mapping[str, object]],
) -> str | None:
    by_period: dict[str, dict[str, Decimal]] = {}
    for fact in facts:
        account_id = str(fact.get("account_id") or "")
        if account_id not in {"revenue", "operating_income", "net_income"}:
            continue
        period = _financial_period(fact)
        value = _financial_value(fact)
        if not period or value is None:
            continue
        period_values = by_period.setdefault(period, {})
        if account_id in period_values:
            return None
        period_values[account_id] = value
    complete_periods = sorted(
        period for period, values in by_period.items()
        if values.get("revenue") not in {None, Decimal(0)}
        and "operating_income" in values
    )
    if len(complete_periods) < 2:
        return None
    previous, current = (by_period[period] for period in complete_periods[-2:])
    directions: list[int] = []
    for account_id in ("operating_income", "net_income"):
        if account_id not in previous or account_id not in current:
            if account_id == "operating_income":
                return None
            continue
        old_margin = previous[account_id] / previous["revenue"]
        new_margin = current[account_id] / current["revenue"]
        directions.append((new_margin > old_margin) - (new_margin < old_margin))
    if not directions:
        return None
    if all(direction > 0 for direction in directions):
        return "improved"
    if all(direction < 0 for direction in directions):
        return "deteriorated"
    if all(direction == 0 for direction in directions):
        return "stable"
    return "mixed"


def _deterministic_conclusion(
    dimension: str | None,
    facts: Sequence[Mapping[str, object]],
) -> str | None:
    if dimension == "profitability":
        return _profitability_conclusion(facts)
    return None


def _date_in_slot(value: object, slot: EvidenceSlot) -> bool:
    date_value = str(value or "")
    if not date_value:
        return False
    if slot.instant_date is not None:
        return date_value == slot.instant_date
    if slot.period_start is not None and date_value < slot.period_start:
        return False
    if slot.period_end is not None and date_value > slot.period_end:
        return False
    return True


def _fact_in_slot(fact: Mapping[str, object], slot: EvidenceSlot) -> bool:
    if slot.instant_date is not None:
        return str(fact.get("instant_date") or fact.get("event_date") or "") == slot.instant_date
    start = str(fact.get("period_start") or fact.get("event_date") or "")
    end = str(
        fact.get("period_end")
        or fact.get("instant_date")
        or fact.get("event_date")
        or ""
    )
    if slot.period_start is not None and (not start or start < slot.period_start):
        return False
    if slot.period_end is not None and (not end or end > slot.period_end):
        return False
    return bool(start or end)


def _issuer_owned(ref: EvidenceRef, slot: EvidenceSlot) -> bool:
    if slot.issuer is None:
        return False
    locator = ref.locator if isinstance(ref.locator, Mapping) else {}
    return locator.get("analysis_issuer") == slot.issuer


def _period_owned(
    ref: EvidenceRef,
    slot: EvidenceSlot,
    facts: Sequence[Mapping[str, object]],
) -> bool:
    if slot.period_start is None and slot.period_end is None and slot.instant_date is None:
        return True
    related = [fact for fact in facts if ref.evidence_id in _fact_evidence_ids(fact)]
    if related:
        return all(_fact_in_slot(fact, slot) for fact in related)
    locator = ref.locator if isinstance(ref.locator, Mapping) else {}
    locator_date = (
        locator.get("instant_date")
        or locator.get("period_end")
        or locator.get("period_start")
        or ref.filed_at
    )
    return _date_in_slot(locator_date, slot)


def _slot_payload(
    planned: EvidenceSlot,
    *,
    complete: bool,
    reasons: Sequence[str],
    evidence: Sequence[EvidenceRef],
) -> dict[str, object]:
    return {
        "slot_id": planned.slot_id,
        "domain": planned.domain,
        "issuer": planned.issuer,
        "period_start": planned.period_start,
        "period_end": planned.period_end,
        "instant_date": planned.instant_date,
        "mandatory": planned.mandatory,
        "complete": complete,
        "evidence_ids": [ref.evidence_id for ref in evidence],
        "reason_codes": list(_ordered(tuple(reasons))),
    }


def _evidence_item(ref: EvidenceRef) -> dict[str, object]:
    locator = dict(ref.locator) if isinstance(ref.locator, Mapping) else {}
    return {
        "evidence_id": ref.evidence_id,
        "evidence_ids": [ref.evidence_id],
        "filing_id": ref.filing_id,
        "rcept_no": ref.filing_id,
        "source_id": ref.source_id,
        "report_name": ref.report_name,
        "filed_at": ref.filed_at,
        "lineage_status": ref.lineage_status,
        "is_current": ref.is_current,
        "company": locator.get("analysis_issuer"),
        "issuer_corp_code": locator.get("issuer_corp_code"),
        "locator": locator,
        "text": ref.text,
        "quality_status": "analysis_slot_owned",
    }


@dataclass(frozen=True, slots=True)
class BoundedAnalysisExecution:
    plan: AnalysisPlan
    retrieval: AnalysisRetrieval | None
    complete: bool
    conclusion: str | None
    reason_codes: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...] = ()
    evidence_slots: tuple[Mapping[str, object], ...] = ()

    def to_tool_response(self) -> dict[str, object]:
        evidence_ids = [ref.evidence_id for ref in self.evidence]
        filing_ids = [ref.filing_id for ref in self.evidence]
        issuer_codes = [
            str(ref.locator.get("issuer_corp_code"))
            for ref in self.evidence
            if isinstance(ref.locator, Mapping) and ref.locator.get("issuer_corp_code")
        ]
        context = "\n\n".join(
            f"[{ref.filing_id}|{ref.evidence_id}] {ref.text}" for ref in self.evidence
        )
        base = self.plan.base_plan
        requested_scope = {
            "company": base.company,
            "period_start": base.period_start,
            "period_end": base.period_end,
            "instant_date": base.instant_date,
            "correction_policy": base.correction_policy,
        }
        bundle = ToolEvidenceBundle(
            question_intent="bounded_analysis",
            requested_scope=requested_scope,
            covered_scope={
                "company": sorted({
                    str(ref.locator.get("analysis_issuer"))
                    for ref in self.evidence
                    if isinstance(ref.locator, Mapping) and ref.locator.get("analysis_issuer")
                }),
                "evidence_slots": [str(slot["slot_id"]) for slot in self.evidence_slots],
            },
            items=[_evidence_item(ref) for ref in self.evidence],
            evidence_ids=evidence_ids,
            filing_ids=filing_ids,
            issuer_corp_codes=issuer_codes,
            quality_warnings=list(self.reason_codes),
            correction_status="policy_applied",
            retrieval_status={
                "route": "evidence_service.search_analysis",
                **(
                    to_jsonable(self.retrieval.retrieval_diagnostics)
                    if self.retrieval is not None
                    else {}
                ),
            },
            sufficiency="sufficient" if self.complete else "insufficient",
        )
        admitted_ids = set(evidence_ids)
        facts = [
            fact for fact in self.retrieval.financial_facts
            if _fact_evidence_ids(fact).intersection(admitted_ids)
        ] if self.retrieval is not None else []
        events = [
            fact for fact in self.retrieval.event_facts
            if _fact_evidence_ids(fact).intersection(admitted_ids)
        ] if self.retrieval is not None else []
        sufficiency = {
            "status": "sufficient" if self.complete else "insufficient",
            "reasons": list(self.reason_codes),
            "missing_requirements": [] if self.complete else list(self.reason_codes),
            "recommended_action": "answer" if self.complete else "abstain",
            "answer_allowed": self.complete,
        }
        return {
            "status": "success" if self.complete else "insufficient",
            "tool_name": "build_summary_context",
            "data": {
                "context": context,
                "context_chars": len(context),
                "result_count": len(self.evidence),
                "execution_mode": "bounded_analysis",
                "analysis_dimension": self.plan.judgment_dimension,
                "conclusion": self.conclusion,
                "allowed_conclusions": list(self.plan.allowed_conclusions),
                "subquestions": list(self.plan.subquestions),
                "evidence_slots": [dict(slot) for slot in self.evidence_slots],
                "financial_facts": to_jsonable(facts),
                "event_facts": to_jsonable(events),
                "limitations": [
                    "historical_disclosure_only",
                    "no_transaction_recommendation",
                    "no_forecast",
                    *self.reason_codes,
                ],
            },
            "evidence_bundle": bundle.to_dict(),
            "warnings": list(self.reason_codes),
            "metadata": {
                "schema_version": "tool-response-v1",
                "backend_version": "bounded-analysis-v1",
                "executors": ["plan_analysis", "evidence_service.search_analysis"],
                "sufficiency_check": sufficiency,
            },
        }


class BoundedAnalysisExecutor:
    """Run only catalog-bounded historical judgments over attested evidence."""

    def __init__(self, evidence_service: AnalysisEvidenceService) -> None:
        self.evidence_service = evidence_service

    def execute(self, question: str) -> BoundedAnalysisExecution | None:
        candidate_loader = getattr(self.evidence_service, "company_candidates", None)
        candidates = candidate_loader() if callable(candidate_loader) else []
        plan = plan_analysis(question, company_candidates=candidates)
        if plan.analysis_mode == "lookup":
            return None
        if plan.analysis_mode == "prohibited":
            return BoundedAnalysisExecution(
                plan, None, False, None, tuple(plan.reason_codes)
            )
        if any(slot.mandatory and slot.issuer is None for slot in plan.required_evidence_slots):
            return BoundedAnalysisExecution(
                plan, None, False, "insufficient_evidence", ("analysis_issuer_unresolved",)
            )

        search = getattr(self.evidence_service, "search_analysis", None)
        if not callable(search):
            return BoundedAnalysisExecution(
                plan,
                None,
                False,
                "insufficient_evidence",
                ("analysis_evidence_service_unavailable",),
            )
        retrieval = search(plan, limit=plan.max_evidence or 20)
        retrieved_by_id = {slot.slot_id: slot for slot in retrieval.slots}
        all_facts: tuple[Mapping[str, object], ...] = (
            *retrieval.financial_facts,
            *retrieval.event_facts,
        )
        admitted: dict[str, EvidenceRef] = {}
        payloads: list[Mapping[str, object]] = []
        reasons: list[str] = []
        mandatory_complete = True
        for planned in plan.required_evidence_slots:
            retrieved = retrieved_by_id.get(planned.slot_id)
            slot_evidence = tuple(retrieved.evidence) if retrieved is not None else ()
            slot_reasons = list(retrieved.reason_codes) if retrieved is not None else []
            slot_complete = bool(retrieved is not None and retrieved.complete)
            if retrieved is None:
                slot_reasons.append(f"required_slot_missing:{planned.slot_id}")
            else:
                issuer_owned = tuple(
                    ref for ref in slot_evidence if _issuer_owned(ref, planned)
                )
                if len(issuer_owned) != len(slot_evidence):
                    slot_complete = False
                    slot_reasons.append(
                        f"evidence_slot_issuer_mismatch:{planned.slot_id}"
                    )
                period_owned = tuple(
                    ref for ref in issuer_owned if _period_owned(ref, planned, all_facts)
                )
                if len(period_owned) != len(issuer_owned):
                    slot_complete = False
                    slot_reasons.append(
                        f"evidence_slot_period_mismatch:{planned.slot_id}"
                    )
                slot_evidence = period_owned
                if len(slot_evidence) < planned.min_evidence:
                    slot_complete = False
                    if not slot_reasons:
                        slot_reasons.append(f"required_slot_missing:{planned.slot_id}")
            if planned.mandatory and not slot_complete:
                mandatory_complete = False
                if not slot_reasons:
                    slot_reasons.append(f"required_slot_missing:{planned.slot_id}")
                reasons.extend(slot_reasons)
            for ref in slot_evidence:
                admitted.setdefault(ref.evidence_id, ref)
            payloads.append(_slot_payload(
                planned,
                complete=slot_complete,
                reasons=slot_reasons,
                evidence=slot_evidence,
            ))
        if not retrieval.complete and mandatory_complete:
            mandatory_complete = False
            reasons.append("analysis_retrieval_incomplete")
        conclusion = (
            _deterministic_conclusion(
                plan.judgment_dimension,
                retrieval.financial_facts,
            )
            if mandatory_complete
            else "insufficient_evidence"
        )
        return BoundedAnalysisExecution(
            plan=plan,
            retrieval=retrieval,
            complete=mandatory_complete,
            conclusion=conclusion,
            reason_codes=_ordered(tuple(reasons)),
            evidence=tuple(admitted.values()),
            evidence_slots=tuple(payloads),
        )


__all__ = ["BoundedAnalysisExecution", "BoundedAnalysisExecutor"]
