"""Immutable contracts for bounded disclosure analysis planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .agent_contracts import QueryPlan


def _normalized_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a sequence of strings")
    return tuple(value)


def _normalized_target_periods(value: object) -> tuple[Mapping[str, str | None], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("target_periods must be a sequence of mappings")
    periods: list[Mapping[str, str | None]] = []
    for period in value:
        if not isinstance(period, Mapping):
            raise ValueError("target_periods must be a sequence of mappings")
        if not all(isinstance(key, str) for key in period):
            raise ValueError("target_periods keys must be strings")
        if not all(item is None or isinstance(item, str) for item in period.values()):
            raise ValueError("target_periods values must be strings or None")
        periods.append(MappingProxyType(dict(sorted(period.items()))))
    return tuple(periods)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _normalized_string_tuple(self.reason_codes, "reason_codes"))


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
    min_periods: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_types", _normalized_string_tuple(self.report_types, "report_types"))
        object.__setattr__(self, "search_concepts", _normalized_string_tuple(self.search_concepts, "search_concepts"))
        if type(self.min_periods) is not int or self.min_periods <= 0:
            raise ValueError("min_periods must be a positive integer")


def _normalized_evidence_slots(value: object) -> tuple[EvidenceSlot, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, EvidenceSlot) for item in value):
        raise ValueError("required_evidence_slots must contain EvidenceSlot values")
    return tuple(value)


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_terms", _normalized_string_tuple(self.account_terms, "account_terms"))
        object.__setattr__(self, "reason_codes", _normalized_string_tuple(self.reason_codes, "reason_codes"))
        object.__setattr__(self, "predicate_terms", _normalized_string_tuple(self.predicate_terms, "predicate_terms"))
        object.__setattr__(self, "target_periods", _normalized_target_periods(self.target_periods))

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
        if not isinstance(self.policy, PolicyDecision):
            raise ValueError("policy must be a PolicyDecision")
        if not isinstance(self.base_plan, QueryPlanSnapshot):
            raise ValueError("base_plan must be a QueryPlan or QueryPlanSnapshot")
        object.__setattr__(self, "subquestions", _normalized_string_tuple(self.subquestions, "subquestions"))
        object.__setattr__(self, "required_evidence_slots", _normalized_evidence_slots(self.required_evidence_slots))
        object.__setattr__(self, "allowed_conclusions", _normalized_string_tuple(self.allowed_conclusions, "allowed_conclusions"))
        object.__setattr__(self, "reason_codes", _normalized_string_tuple(self.reason_codes, "reason_codes"))
