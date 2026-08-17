"""Small, serialisable contracts shared by the disclosure-agent runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


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
    calculation: "CalculationResult | None" = None


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


def to_jsonable(value: Any) -> Any:
    """Convert runtime contracts (including Decimal) into strict JSON values."""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
