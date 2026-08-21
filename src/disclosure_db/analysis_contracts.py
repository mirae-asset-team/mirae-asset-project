"""Immutable contracts for bounded disclosure analysis planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .agent_contracts import QueryPlan


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceSlot:
    slot_id: str
    domain: str
    issuer: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    filing_date: str | None = None
    report_types: tuple[str, ...] = ()
    search_concepts: tuple[str, ...] = ()
    min_evidence: int = 1
    max_evidence: int = 4
    mandatory: bool = True
    absence_reason_code: str = "required_evidence_missing"


@dataclass(frozen=True, slots=True)
class QueryPlanSnapshot:
    """Immutable copy of the mutable lookup-plan contract for analysis plans."""

    question: str
    company: str | None = None
    as_of: str | None = None
    as_of_source: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    operation: str = "lookup"
    account_terms: tuple[str, ...] = ()
    scope: str | None = None
    statement_type: str | None = None
    correction_policy: str = "current"
    question_type: str = "unknown"
    reason_codes: tuple[str, ...] = ()
    fact_domain: str = "text"
    predicate_terms: tuple[str, ...] = ()
    target_periods: tuple[Mapping[str, str | None], ...] = ()
    requires_complete_evidence_set: bool = False
    filing_date: str | None = None
    account_id: str | None = None
    latest_period_count: int = 1
    threshold_value: Decimal | None = None
    threshold_inclusive: bool = False
    top_n: int = 10

    @classmethod
    def from_query_plan(cls, plan: QueryPlan) -> "QueryPlanSnapshot":
        return cls(
            question=plan.question,
            company=plan.company,
            as_of=plan.as_of,
            as_of_source=plan.as_of_source,
            period_start=plan.period_start,
            period_end=plan.period_end,
            instant_date=plan.instant_date,
            operation=plan.operation,
            account_terms=tuple(plan.account_terms),
            scope=plan.scope,
            statement_type=plan.statement_type,
            correction_policy=plan.correction_policy,
            question_type=plan.question_type,
            reason_codes=tuple(plan.reason_codes),
            fact_domain=plan.fact_domain,
            predicate_terms=tuple(plan.predicate_terms),
            target_periods=tuple(
                MappingProxyType({str(key): value for key, value in period.items()})
                for period in plan.target_periods
            ),
            requires_complete_evidence_set=plan.requires_complete_evidence_set,
            filing_date=plan.filing_date,
            account_id=plan.account_id,
            latest_period_count=plan.latest_period_count,
            threshold_value=plan.threshold_value,
            threshold_inclusive=plan.threshold_inclusive,
            top_n=plan.top_n,
        )


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    question: str
    analysis_mode: str
    policy: PolicyDecision
    base_plan: QueryPlanSnapshot | QueryPlan
    subquestions: tuple[str, ...] = ()
    required_evidence_slots: tuple[EvidenceSlot, ...] = ()
    judgment_dimension: str | None = None
    allowed_conclusions: tuple[str, ...] = ()
    max_evidence: int = 0
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.base_plan, QueryPlan):
            object.__setattr__(self, "base_plan", QueryPlanSnapshot.from_query_plan(self.base_plan))
