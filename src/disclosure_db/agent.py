"""Disclosure-agent orchestration with deterministic safety gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_contracts import VerifiedAnswer
from .answer_verifier import verify_answer
from .calculator import calculate
from .evidence_service import EvidenceService
from .generation import DeterministicGenerator, HyperClovaGenerator
from .query_planner import plan_query


@dataclass(slots=True)
class AgentSettings:
    base_database: Path
    overlay_database: Path | None = None
    corpus_revision: str = "semantic-v1"
    use_hcx: bool = True


class DisclosureAgent:
    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        base_database: Path | None = None,
        overlay_database: Path | None = None,
        evidence_service: EvidenceService | None = None,
        generator: object | None = None,
    ):
        if evidence_service is None:
            if settings is not None:
                base_database = settings.base_database
                overlay_database = settings.overlay_database
            if base_database is None:
                raise ValueError("base_database or evidence_service is required")
            evidence_service = EvidenceService(base_database, overlay_database)
        self.evidence_service = evidence_service
        self.generator = generator or (HyperClovaGenerator() if settings is None or settings.use_hcx else DeterministicGenerator())

    def answer(self, question: str, *, company: str | None = None, as_of: str | None = None, limit: int = 20) -> VerifiedAnswer:
        candidates = self.evidence_service.company_candidates()
        query_plan = plan_query(question, company_candidates=candidates, company_hint=company, as_of=as_of)
        bundle = self.evidence_service.search(query_plan, limit=limit)
        self._attach_calculation(bundle, query_plan.operation)
        draft = self.generator.generate(bundle)  # type: ignore[union-attr]
        return verify_answer(bundle, draft)

    @staticmethod
    def _attach_calculation(bundle, operation: str) -> None:
        if operation == "lookup" or len(bundle.financial_facts) < 2:
            return
        facts = sorted(bundle.financial_facts, key=lambda fact: str(fact.get("period_end") or fact.get("instant_date") or ""))
        if operation in {"growth_rate", "difference", "ratio"}:
            selected = facts[-2:]
        else:
            selected = facts
        try:
            bundle.calculation = calculate(
                operation,
                [fact["value_numeric"] for fact in selected],
                unit=str(selected[-1].get("unit_raw") or selected[-1].get("currency") or "") or None,
                evidence_ids=[str(evidence_id) for fact in selected for evidence_id in fact.get("evidence_ids", [])],
            )
        except (KeyError, TypeError, ValueError):
            bundle.reason_codes.append("calculation_unavailable")
