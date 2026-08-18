"""Disclosure-agent orchestration with deterministic safety gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attestation import CorpusAttestation, load_distribution_attestation, verify_fast_identity
from .agent_contracts import EvidenceBundle, VerifiedAnswer
from .answer_verifier import verify_answer
from .calculator import calculate
from .evidence_service import EvidenceService
from .generation import DeterministicGenerator, HyperClovaGenerator
from .query_planner import plan_query
from .reranker import ClovaReranker


@dataclass(slots=True)
class AgentSettings:
    base_database: Path
    overlay_database: Path | None = None
    corpus_revision: str = "semantic-v1"
    use_hcx: bool = True
    attestation_path: Path | None = None
    search_database: Path | None = None
    reranker: object | None = None
    # ``search_index`` is the public serving name; retain ``search_database``
    # for compatibility with earlier callers.
    search_index: Path | None = None


class DisclosureAgent:
    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        base_database: Path | None = None,
        overlay_database: Path | None = None,
        attestation_path: Path | None = None,
        search_database: Path | None = None,
        search_index: Path | None = None,
        evidence_service: EvidenceService | None = None,
        generator: object | None = None,
        reranker: object | None = None,
    ):
        configured_reranker = reranker
        corpus_revision = "semantic-v1"
        search_database = search_database or search_index
        if evidence_service is None:
            if settings is not None:
                base_database = settings.base_database
                overlay_database = settings.overlay_database
                corpus_revision = settings.corpus_revision
                attestation_path = settings.attestation_path
                search_database = settings.search_database or settings.search_index
                if configured_reranker is None:
                    configured_reranker = settings.reranker
            if base_database is None:
                raise ValueError("base_database or evidence_service is required")
            attestation: CorpusAttestation | None = None
            if attestation_path is not None:
                attestation = load_distribution_attestation(attestation_path, database=base_database)
            evidence_service = EvidenceService(
                base_database,
                overlay_database,
                corpus_revision=corpus_revision,
                attestation=attestation,
                search_database=search_database,
                reranker=configured_reranker if configured_reranker is not None else ClovaReranker(),
            )
        self.evidence_service = evidence_service
        self.generator = generator or (HyperClovaGenerator() if settings is None or settings.use_hcx else DeterministicGenerator())

    def answer(self, question: str, *, company: str | None = None, as_of: str | None = None, limit: int = 20) -> VerifiedAnswer:
        attestation = getattr(self.evidence_service, "attestation", None)
        if attestation is not None and not verify_fast_identity(self.evidence_service.base_database, attestation):
            bundle = EvidenceBundle(
                question=question,
                answerable=False,
                reason_codes=["base_attestation_failed"],
            )
            return verify_answer(bundle, self.generator.generate(bundle))  # type: ignore[union-attr]
        candidates = self.evidence_service.company_candidates()
        query_plan = plan_query(question, company_candidates=candidates, company_hint=company, as_of=as_of)
        bundle = self.evidence_service.search(query_plan, limit=limit)
        self._attach_calculation(bundle, query_plan.operation)
        if query_plan.operation in {"growth_rate", "difference", "ratio", "sum"} and bundle.calculation is None:
            bundle.answerable = False
            bundle.reason_codes.append("calculation_required")
        draft = self.generator.generate(bundle)  # type: ignore[union-attr]
        return verify_answer(bundle, draft)

    @staticmethod
    def _attach_calculation(bundle, operation: str) -> None:
        if operation == "lookup" or len(bundle.financial_facts) < 2:
            return
        first = bundle.financial_facts[0]
        grain = tuple(first.get(key) for key in ("account_name_raw", "statement_type", "scope", "period_type"))
        facts = [
            fact for fact in bundle.financial_facts
            if tuple(fact.get(key) for key in ("account_name_raw", "statement_type", "scope", "period_type")) == grain
        ]
        facts = sorted(facts, key=lambda fact: str(fact.get("period_end") or fact.get("instant_date") or ""))
        if operation in {"growth_rate", "difference", "ratio"}:
            if len(facts) < 2:
                bundle.reason_codes.append("calculation_period_alignment_failed")
                return
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
