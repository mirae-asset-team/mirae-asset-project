"""Small, serialisable contracts shared by the disclosure-agent runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class CitationRef:
    evidence_id: str
    filing_id: str
    report_name: str | None = None
    filed_at: str | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    excerpt: str | None = None


@dataclass(slots=True)
class QueryPlan:
    question: str
    company: str | None = None
    as_of: str | None = None
    as_of_source: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    operation: str = "lookup"
    account_terms: list[str] = field(default_factory=list)
    scope: str | None = None
    statement_type: str | None = None
    correction_policy: str = "current"
    question_type: str = "unknown"
    reason_codes: list[str] = field(default_factory=list)
    fact_domain: str = "text"
    predicate_terms: list[str] = field(default_factory=list)
    target_periods: list[dict[str, str | None]] = field(default_factory=list)
    requires_complete_evidence_set: bool = False
    filing_date: str | None = None
    account_id: str | None = None
    latest_period_count: int = 1
    threshold_value: Decimal | None = None
    threshold_inclusive: bool = False
    top_n: int = 10


@dataclass(slots=True)
class EvidenceRef:
    evidence_id: str
    filing_id: str
    source_id: str
    text: str
    locator: dict[str, Any] = field(default_factory=dict)
    lineage_status: str = "root"
    score: float = 0.0
    source_path: str | None = None
    filed_at: str | None = None
    report_name: str | None = None
    is_current: bool | None = None


@dataclass(slots=True)
class EvidenceBundle:
    question: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    answerable: bool = False
    reason_codes: list[str] = field(default_factory=list)
    financial_facts: list[dict[str, Any]] = field(default_factory=list)
    event_facts: list[dict[str, Any]] = field(default_factory=list)
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)
    calculation: "CalculationResult | None" = None
    coverage: dict[str, Any] = field(default_factory=dict)
    aggregate_result: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CalculationResult:
    operation: str
    value: Decimal | None
    unit: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    operands: list[Decimal] = field(default_factory=list)
    precision: int = 10


@dataclass(slots=True)
class AnswerDraft:
    answer: str
    citation_ids: list[str] = field(default_factory=list)
    numeric_values: list[str] = field(default_factory=list)
    answerable: bool = True
    reason_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerifiedAnswer:
    answer: str
    citation_ids: list[str]
    verified: bool
    answerable: bool
    reason_codes: list[str] = field(default_factory=list)
    numeric_values: list[str] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    calculation: "CalculationResult | None" = None
    financial_facts: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    aggregate_result: dict[str, Any] = field(default_factory=dict)


def to_jsonable(value: Any) -> Any:
    """Convert runtime contracts (including Decimal) into strict JSON values."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, CitationRef):
        citation = asdict(value)
        if citation["excerpt"] is None:
            citation.pop("excerpt")
        return {key: to_jsonable(item) for key, item in citation.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [to_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
