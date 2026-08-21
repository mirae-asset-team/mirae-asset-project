"""Immutable contracts for bounded disclosure analysis planning."""

from __future__ import annotations

from dataclasses import dataclass

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
class AnalysisPlan:
    question: str
    analysis_mode: str
    policy: PolicyDecision
    base_plan: QueryPlan
    subquestions: tuple[str, ...] = ()
    required_evidence_slots: tuple[EvidenceSlot, ...] = ()
    judgment_dimension: str | None = None
    allowed_conclusions: tuple[str, ...] = ()
    max_evidence: int = 0
    reason_codes: tuple[str, ...] = ()
