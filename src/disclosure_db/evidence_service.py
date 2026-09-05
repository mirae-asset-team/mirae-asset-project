"""Safe evidence retrieval over the immutable corpus and financial overlay."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import unicodedata
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .attestation import CorpusAttestation, verify_fast_identity
from .agent_contracts import CalculationResult, EvidenceBundle, EvidenceRef, QueryPlan
from .analysis_contracts import AnalysisPlan, EvidenceSlot
from .dense_client import DenseSearchClient, flatten_evidence_ids
from .freeform_retrieval import (
    AnalysisRetrieval,
    MAX_CANDIDATES_PER_VARIANT,
    MAX_EVIDENCE_PER_SLOT,
    MAX_TOTAL_EVIDENCE,
    SlotRetrieval,
    build_query_variants,
    fuse_slot_results,
    has_transaction_action_surface,
)
from .financial_overlay import fetch_event_facts, fetch_financial_coverage, fetch_overlay_facts, overlay_matches_base
from .financial_accounts import load_financial_account_catalog, resolve_financial_account
from .pipeline import query_database
from .retrieval_evaluation import compile_retrieval_query, retrieval_tokens
from .reranker import ClovaReranker
from .search_index import rrf_fuse


_INSTRUCTION_LIKE_EVIDENCE_MARKERS = (
    "ignore previous", "ignore all previous", "ignore all instructions",
    "disregard previous", "system prompt", "system message", "developer message",
    "developer instructions", "follow these instructions", "you are chatgpt",
    "<system>", "[system]", "이전 지시를 무시", "지시를 무시",
    "시스템 프롬프트", "개발자 메시지", "개발자 지침",
)
_SEMANTIC_TEXT_SLOT_IDS = frozenset({
    "disclosed_risk_factors",
    "governance_events",
    "management_discussion_text",
})
_MIN_SEMANTIC_EVIDENCE_CHARACTERS = 80


def evidence_text_is_admitted(text: object) -> bool:
    """Reject instruction-like corpus text before it reaches any public model context."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) not in {"Cc", "Cf"}
    )
    normalized = " ".join(normalized.split()).casefold()
    compact = "".join(normalized.split())
    return not any(
        marker in normalized or "".join(marker.split()) in compact
        for marker in _INSTRUCTION_LIKE_EVIDENCE_MARKERS
    )


def semantic_text_evidence_is_answer_capable(text: object, slot: EvidenceSlot) -> bool:
    """Reject short keyword collisions for broad semantic analysis slots."""

    slot_id = slot.slot_id.split("__i", 1)[0]
    if slot_id not in _SEMANTIC_TEXT_SLOT_IDS:
        return True
    normalized = " ".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()
    concepts = tuple(
        " ".join(unicodedata.normalize("NFKC", concept).split()).casefold()
        for concept in slot.search_concepts
        if concept.strip()
    )
    return bool(
        len(normalized) >= _MIN_SEMANTIC_EVIDENCE_CHARACTERS
        and concepts
        and any(concept in normalized for concept in concepts)
    )


def _readonly_connection(database: Path) -> sqlite3.Connection:
    """Open a frozen serving database without SQLite write-back attempts."""
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


class EvidenceService:
    def __init__(
        self,
        base_database: Path,
        overlay_database: Path | None = None,
        *,
        corpus_revision: str = "semantic-v1",
        attestation: CorpusAttestation | None = None,
        search_database: Path | None = None,
        reranker: ClovaReranker | None = None,
        dense_client: DenseSearchClient | None = None,
    ):
        self.base_database = Path(base_database)
        self.overlay_database = Path(overlay_database) if overlay_database else None
        self.corpus_revision = corpus_revision
        self.attestation = attestation
        self.search_database = Path(search_database) if search_database else None
        self.reranker = reranker
        self.dense_client = dense_client if dense_client is not None else DenseSearchClient.from_environment(
            base_sha256=getattr(attestation, "sha256", None),
            base_size_bytes=getattr(attestation, "size_bytes", None),
            corpus_revision=corpus_revision,
        )
        self._dense_filing_cache: dict[tuple[object, ...], tuple[str, ...]] = {}
        if (self.overlay_database is not None or self.search_database is not None) and attestation is None:
            raise ValueError("attestation is required when runtime overlay/search is configured")
        self._search_index = None
        self._search_index_error: str | None = None
        if self.search_database is not None:
            if not self.search_database.exists():
                self._search_index_error = "search_index_unavailable"
            else:
                try:
                    from .search_index import SafeSearchIndex

                    self._search_index = SafeSearchIndex(
                        self.search_database,
                        base_sha256=self.attestation.sha256,
                        expected_base_size=self.attestation.size_bytes,
                    )
                except ValueError:
                    self._search_index_error = "search_index_attestation_mismatch"
                except (OSError, sqlite3.Error):
                    self._search_index_error = "search_index_sqlite_error"
        self._companies: list[str] | None = None
        config_directory = Path(
            os.environ.get(
                "DISCLOSURE_CONFIG_DIR",
                Path(__file__).resolve().parents[2] / "config",
            )
        )
        catalog_path = config_directory / "financial_account_catalog.json"
        self.financial_account_catalog = None
        if catalog_path.exists():
            self.financial_account_catalog = load_financial_account_catalog(catalog_path)
            self.account_aliases = self.financial_account_catalog.alias_expansions()
        else:
            aliases_path = config_directory / "financial_account_aliases.json"
            try:
                raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
                self.account_aliases = {
                    str(key): [str(item) for item in values]
                    for key, values in raw_aliases.items()
                    if isinstance(values, list)
                }
            except (OSError, json.JSONDecodeError):
                self.account_aliases = {}
        predicate_path = config_directory / "agent_gold_predicates.json"
        try:
            predicate_payload = json.loads(predicate_path.read_text(encoding="utf-8"))
            self.event_predicate_aliases = {
                str(item["id"]): [str(item["id"]), *(str(value) for value in item.get("predicate_values", []))]
                for item in predicate_payload.get("predicates", [])
                if isinstance(item, dict) and item.get("id")
            }
        except (OSError, json.JSONDecodeError):
            self.event_predicate_aliases = {}

    @property
    def search_index_ready(self) -> bool:
        return self.search_database is None or self._search_index is not None

    @staticmethod
    def _slot_version_as_of(plan: AnalysisPlan) -> str | None:
        base = plan.base_plan
        return base.as_of if base.as_of_source == "api" else (
            None if base.period_start or base.period_end or base.instant_date else base.as_of
        )

    @staticmethod
    def _matches_slot_report(ref: EvidenceRef, slot: EvidenceSlot) -> bool:
        return not slot.report_types or any(report_type in (ref.report_name or "") for report_type in slot.report_types)

    @staticmethod
    def _matches_slot_issuer(ref: EvidenceRef, slot: EvidenceSlot) -> bool:
        issuer = ref.locator.get("analysis_issuer") if isinstance(ref.locator, dict) else None
        return issuer is None or slot.issuer is None or issuer == slot.issuer

    @staticmethod
    def _fuse_refs(rankings: list[list[EvidenceRef]], *, limit: int) -> list[EvidenceRef]:
        rows = [[{"evidence_id": ref.evidence_id, "ref": ref} for ref in ranking] for ranking in rankings]
        return [
            row["ref"]
            for row in rrf_fuse(rows, limit=limit)
            if isinstance(row.get("ref"), EvidenceRef)
        ]

    def _dense_filings(
        self,
        *,
        company: str | None,
        filing_id: str | None,
        as_of: str | None,
        filed_at: str | None,
        start_date: str | None,
        end_date: str | None,
        correction_policy: str,
    ) -> list[str]:
        if correction_policy not in {"current", "original", "corrected", "both"}:
            raise ValueError("correction_policy_invalid")
        cache_key = (company, filing_id, as_of, filed_at, start_date, end_date, correction_policy)
        cached = self._dense_filing_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        with closing(_readonly_connection(self.base_database)) as connection:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(filing)")}
            clauses: list[str] = []
            params: list[object] = []
            company_fields = [
                name
                for name in ("issuer_name", "listed_name", "reporter_name", "stock_code", "issuer_corp_code")
                if name in columns
            ]
            if company and not company_fields:
                self._dense_filing_cache[cache_key] = ()
                return []
            if company:
                clauses.append("(" + " OR ".join(f"f.{name}=?" for name in company_fields) + ")")
                params.extend([company] * len(company_fields))
            if filing_id:
                clauses.append("f.filing_id=?")
                params.append(filing_id)
            if filed_at:
                clauses.append("f.filed_at=?")
                params.append(filed_at)
            if start_date:
                clauses.append("f.filed_at>=?")
                params.append(start_date)
            if end_date:
                clauses.append("f.filed_at<=?")
                params.append(end_date)
            if correction_policy == "original":
                clauses.append("v.lineage_status='root'")
                if as_of is not None:
                    clauses.append("v.effective_from<=? AND (v.effective_to IS NULL OR ?<v.effective_to)")
                    params.extend((as_of, as_of))
            elif correction_policy == "both":
                clauses.append("v.lineage_status IN ('root','resolved')")
                if as_of is not None:
                    clauses.append("v.effective_from<=?")
                    params.append(as_of)
            else:
                clauses.append("v.lineage_status IN ('root','resolved')")
                if as_of is None:
                    clauses.append("v.is_current=1")
                else:
                    clauses.append("v.effective_from<=? AND (v.effective_to IS NULL OR ?<v.effective_to)")
                    params.extend((as_of, as_of))
                if correction_policy == "corrected":
                    clauses.append("f.is_correction=1")
            rows = connection.execute(
                f"""SELECT f.filing_id FROM filing f
                       JOIN filing_version v ON v.filing_id=f.filing_id
                      WHERE {' AND '.join(clauses)}
                      ORDER BY f.filing_id LIMIT 20001""",
                params,
            ).fetchall()
        filings = tuple(dict.fromkeys(str(row[0]) for row in rows)) if len(rows) <= 20_000 else ()
        self._dense_filing_cache[cache_key] = filings
        return list(filings)

    def _search_dense(
        self,
        question: str,
        *,
        company: str | None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        correction_policy: str = "current",
        limit: int = 40,
    ) -> tuple[list[EvidenceRef], dict[str, object]]:
        if self.dense_client is None:
            return [], {"dense_configured": False, "dense_used": False}
        started = time.perf_counter()
        diagnostics: dict[str, object] = {"dense_configured": True, "dense_used": False}
        try:
            filing_ids = self._dense_filings(
                company=company,
                filing_id=filing_id,
                as_of=as_of,
                filed_at=filed_at,
                start_date=start_date,
                end_date=end_date,
                correction_policy=correction_policy,
            )
            if not filing_ids:
                diagnostics["dense_reason_code"] = "dense_filtered_filings_unavailable"
                return [], diagnostics
            hits = self.dense_client.search(question, limit=limit, filing_ids=filing_ids)
            allowed_filings = set(filing_ids)
            hits = [hit for hit in hits if hit.filing_id in allowed_filings]
            ordered_ids = flatten_evidence_ids(hits)
            hydrated = self._hydrate_ids(
                ordered_ids,
                as_of=as_of,
                correction_policy=correction_policy,
            )
            hydrated = [ref for ref in hydrated if ref.filing_id in allowed_filings]
            declared_filings_by_evidence: dict[str, set[str]] = {}
            for hit in hits:
                for evidence_id in hit.evidence_ids:
                    declared_filings_by_evidence.setdefault(evidence_id, set()).add(hit.filing_id)
            hydrated = [
                ref for ref in hydrated
                if declared_filings_by_evidence.get(ref.evidence_id) == {ref.filing_id}
            ]
            if filed_at is not None:
                hydrated = [ref for ref in hydrated if ref.filed_at == filed_at]
            by_id = {ref.evidence_id: ref for ref in hydrated}
            score_by_id: dict[str, float] = {}
            for hit in hits:
                for evidence_id in hit.evidence_ids:
                    score_by_id.setdefault(evidence_id, hit.score)
            ordered = [by_id[evidence_id] for evidence_id in ordered_ids if evidence_id in by_id]
            safe_ordered = [
                ref
                for ref in ordered
                if evidence_text_is_admitted(ref.text)
            ]
            for ref in safe_ordered:
                ref.score = float(score_by_id.get(ref.evidence_id, 0.0))
                ref.locator["dense_score"] = ref.score
                ref.locator["retrieval_source"] = "bge-m3-dense"
            diagnostics.update(
                {
                    "dense_used": bool(safe_ordered),
                    "dense_chunk_count": len(hits),
                    "dense_allowed_filing_count": len(filing_ids),
                    "dense_evidence_count": len(safe_ordered),
                    "dense_excluded_prompt_injection_count": len(ordered) - len(safe_ordered),
                }
            )
            return safe_ordered, diagnostics
        except (RuntimeError, ValueError, OSError, sqlite3.Error):
            diagnostics["dense_reason_code"] = "dense_service_unavailable"
            return [], diagnostics
        finally:
            diagnostics["dense_latency_ms"] = int(round((time.perf_counter() - started) * 1000))

    def search_dense(
        self,
        question: str,
        *,
        company: str | None = None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        correction_policy: str = "current",
        limit: int = 40,
    ) -> tuple[list[EvidenceRef], dict[str, object]]:
        """Return hydrated, version-checked Dense evidence for the hybrid adapter."""

        return self._search_dense(
            question,
            company=company,
            filing_id=filing_id,
            as_of=as_of,
            filed_at=filed_at,
            start_date=start_date,
            end_date=end_date,
            correction_policy=correction_policy,
            limit=limit,
        )

    def _search_text_slot(
        self,
        plan: AnalysisPlan,
        slot: EvidenceSlot,
        variants: tuple[str, ...],
    ) -> tuple[list[EvidenceRef], dict[str, object]]:
        """Search an attested index for each bounded text variant and hydrate only safe IDs."""
        started = time.perf_counter()
        diagnostics: dict[str, object] = {
            "variant_ids": list(range(len(variants))),
            "candidate_count": 0,
            "wrong_issuer_count": 0,
            "wrong_version_count": 0,
            "excluded_prompt_injection_count": 0,
            "excluded_unanswerable_count": 0,
            "sparse_ranks": [],
        }
        if not variants or self._search_index is None:
            diagnostics["reason_code"] = self._search_index_error or "search_index_unavailable"
            diagnostics["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
            return [], diagnostics
        try:
            rankings: list[list[EvidenceRef]] = []
            version_as_of = self._slot_version_as_of(plan)
            for variant_id, variant in enumerate(variants):
                rows = self._search_index.search(
                    variant,
                    company=slot.issuer or plan.base_plan.company,
                    as_of=version_as_of,
                    filed_at=slot.filing_date,
                    start_date=slot.period_start,
                    end_date=slot.period_end,
                    limit=MAX_CANDIDATES_PER_VARIANT,
                    correction_policy=plan.base_plan.correction_policy,
                )
                diagnostics["candidate_count"] = int(diagnostics["candidate_count"]) + len(rows)
                sparse_ranks = diagnostics["sparse_ranks"]
                assert isinstance(sparse_ranks, list)
                for rank, row in enumerate(rows, start=1):
                    if slot.issuer is not None and row.get("company") not in {None, slot.issuer}:
                        diagnostics["wrong_issuer_count"] = int(diagnostics["wrong_issuer_count"]) + 1
                    if str(row.get("lineage_status") or "") not in {"root", "resolved"}:
                        diagnostics["wrong_version_count"] = int(diagnostics["wrong_version_count"]) + 1
                    sparse_ranks.append({
                        "variant_id": variant_id,
                        "rank": rank,
                        "evidence_id": str(row["evidence_id"]),
                    })
                hydrated = self._hydrate_ids(
                    (str(row["evidence_id"]) for row in rows),
                    as_of=version_as_of,
                    correction_policy=plan.base_plan.correction_policy,
                )
                by_id = {ref.evidence_id: ref for ref in hydrated}
                refs = [by_id[str(row["evidence_id"])] for row in rows if str(row["evidence_id"]) in by_id]
                safe_refs: list[EvidenceRef] = []
                for ref in refs:
                    ref.locator["analysis_issuer"] = slot.issuer or plan.base_plan.company
                    ref.locator["analysis_version_admitted"] = True
                    if not evidence_text_is_admitted(ref.text):
                        diagnostics["excluded_prompt_injection_count"] = int(diagnostics["excluded_prompt_injection_count"]) + 1
                        continue
                    if not semantic_text_evidence_is_answer_capable(ref.text, slot):
                        diagnostics["excluded_unanswerable_count"] = int(diagnostics["excluded_unanswerable_count"]) + 1
                        continue
                    safe_refs.append(ref)
                rankings.append(safe_refs)
            dense_refs, dense_diagnostics = self._search_dense(
                variants[0],
                company=slot.issuer or plan.base_plan.company,
                as_of=version_as_of,
                filed_at=slot.filing_date,
                start_date=slot.period_start,
                end_date=slot.period_end,
                correction_policy=plan.base_plan.correction_policy,
            )
            for ref in dense_refs:
                ref.locator["analysis_issuer"] = slot.issuer or plan.base_plan.company
                ref.locator["analysis_version_admitted"] = True
            answer_capable_dense_refs = [
                ref for ref in dense_refs
                if semantic_text_evidence_is_answer_capable(ref.text, slot)
            ]
            diagnostics["excluded_unanswerable_count"] = (
                int(diagnostics["excluded_unanswerable_count"])
                + len(dense_refs) - len(answer_capable_dense_refs)
            )
            if answer_capable_dense_refs:
                rankings.append(answer_capable_dense_refs)
            diagnostics.update(dense_diagnostics)
            result = fuse_slot_results(rankings, limit=min(slot.max_evidence, MAX_EVIDENCE_PER_SLOT))
        except ValueError:
            diagnostics["reason_code"] = "search_index_attestation_mismatch"
            result = []
        except (OSError, sqlite3.Error):
            diagnostics["reason_code"] = "search_index_sqlite_error"
            result = []
        diagnostics["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
        return result, diagnostics

    def _search_structured_slot(
        self,
        plan: AnalysisPlan,
        slot: EvidenceSlot,
        variants: tuple[str, ...],
    ) -> tuple[list[EvidenceRef], list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        started = time.perf_counter()
        base = plan.base_plan
        query = variants[0] if variants else ""
        evidence: list[EvidenceRef] = []
        financial_facts: list[dict[str, object]] = []
        event_facts: list[dict[str, object]] = []
        financial_fact_ids: set[str] = set()
        event_fact_ids: set[str] = set()
        concepts = slot.search_concepts if slot.domain == "financial" else (None,)
        evidence_limit = min(slot.max_evidence, MAX_EVIDENCE_PER_SLOT)
        per_query_limit = max(
            slot.min_periods,
            math.ceil(evidence_limit / max(1, len(concepts))),
        )
        rankings: list[list[EvidenceRef]] = []
        candidate_ids: set[str] = set()
        for concept in concepts:
            resolution = resolve_financial_account(
                str(concept or ""),
                catalog=self.financial_account_catalog,
            ) if slot.domain == "financial" else None
            slot_domain = slot.domain
            if resolution is not None:
                if resolution.support_level == "retrieval_only":
                    slot_domain = "text"
                elif resolution.support_level == "derived":
                    slot_domain = "financial_derived"
                elif resolution.status in {"ambiguous", "unsupported"}:
                    slot_domain = "none"
            slot_plan = QueryPlan(
                question=query,
                company=slot.issuer or base.company,
                as_of=base.as_of,
                as_of_source=base.as_of_source,
                period_start=slot.period_start,
                period_end=slot.period_end,
                instant_date=slot.instant_date,
                scope=base.scope,
                # A judgment slot already declares the account concepts it needs.
                # Reusing a question-level IS/BS classification here can suppress
                # a sibling slot from the other statement (for example assets in
                # a profitability-and-financial-health analysis).
                statement_type=None if slot.domain == "financial" else base.statement_type,
                correction_policy=base.correction_policy,
                filing_date=slot.filing_date,
                fact_domain=slot_domain,
                account_terms=[concept] if concept is not None else [],
                predicate_terms=list(slot.search_concepts) if slot.domain == "event" else [],
                latest_period_count=max(base.latest_period_count, slot.min_periods),
                account_id=resolution.canonical_id if resolution is not None else None,
                account_status=resolution.status if resolution is not None else "unknown",
                account_match_type=resolution.match_type if resolution is not None else None,
                account_support_level=resolution.support_level if resolution is not None else None,
                account_retrieval_route=resolution.retrieval_route if resolution is not None else None,
                account_candidates=list(resolution.candidates) if resolution is not None else [],
                account_warning=resolution.warning if resolution is not None else None,
                required_account_ids=list(resolution.required_accounts) if resolution is not None else [],
                account_formula=dict(resolution.formula) if resolution is not None and resolution.formula is not None else None,
            )
            bundle = self.search(slot_plan, limit=min(per_query_limit, MAX_EVIDENCE_PER_SLOT))
            ranking = list(bundle.evidence)
            for ref in ranking:
                ref.locator["analysis_issuer"] = slot.issuer or base.company
                ref.locator["analysis_version_admitted"] = True
            rankings.append(ranking)
            candidate_ids.update(ref.evidence_id for ref in ranking)
            for fact in bundle.financial_facts:
                fact_id = str(fact.get("financial_fact_id") or "")
                if fact_id and fact_id not in financial_fact_ids:
                    financial_fact_ids.add(fact_id)
                    financial_facts.append(fact)
            for fact in bundle.event_facts:
                fact_id = str(fact.get("event_fact_id") or "")
                if fact_id and fact_id not in event_fact_ids:
                    event_fact_ids.add(fact_id)
                    event_facts.append(fact)
        evidence = fuse_slot_results(rankings, limit=evidence_limit)
        for ref in evidence:
            ref.locator["analysis_issuer"] = slot.issuer or base.company
            ref.locator["analysis_version_admitted"] = True
        return (
            evidence,
            financial_facts,
            event_facts,
            {
                "variant_ids": list(range(len(rankings))) if variants else [],
                "candidate_count": len(candidate_ids),
                "sparse_ranks": [
                    {"variant_id": variant_id, "rank": rank, "evidence_id": ref.evidence_id}
                    for variant_id, ranking in enumerate(rankings)
                    for rank, ref in enumerate(ranking, start=1)
                ],
                "excluded_prompt_injection_count": 0,
                "wrong_issuer_count": 0,
                "wrong_version_count": 0,
                "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            },
        )

    def search_analysis(self, plan: AnalysisPlan, *, limit: int = 20) -> AnalysisRetrieval:
        """Retrieve each declared analysis slot independently without changing ``search``."""
        if any(has_transaction_action_surface(question) for question in (plan.question, plan.base_plan.question)):
            return AnalysisRetrieval(complete=False, reason_codes=("policy_transaction_ambiguous",))
        if plan.analysis_mode != "judgment":
            return AnalysisRetrieval(complete=False, reason_codes=("analysis_plan_not_judgment",))
        if not self._base_identity_valid():
            return AnalysisRetrieval(complete=False, reason_codes=("base_attestation_failed",))

        total_limit = max(0, min(limit, plan.max_evidence or MAX_TOTAL_EVIDENCE, MAX_TOTAL_EVIDENCE))
        slots: list[SlotRetrieval] = []
        all_refs: list[EvidenceRef] = []
        all_financial_facts: list[dict[str, object]] = []
        all_event_facts: list[dict[str, object]] = []
        admitted_ids: set[str] = set()
        diagnostics_by_slot: dict[str, object] = {}
        reasons: list[str] = []
        for slot in plan.required_evidence_slots:
            variants = build_query_variants(plan, slot)
            financial_facts: list[dict[str, object]] = []
            event_facts: list[dict[str, object]] = []
            if slot.domain == "text":
                refs, diagnostics = self._search_text_slot(plan, slot, variants)
            else:
                refs, financial_facts, event_facts, diagnostics = self._search_structured_slot(plan, slot, variants)
            prompt_excluded = sum(1 for ref in refs if not evidence_text_is_admitted(ref.text))
            wrong_issuer = sum(1 for ref in refs if not self._matches_slot_issuer(ref, slot))
            wrong_version = sum(
                1 for ref in refs
                if ref.lineage_status not in {"root", "resolved"}
                or (ref.is_current is False and not ref.locator.get("analysis_version_admitted"))
            )
            wrong_report = sum(1 for ref in refs if not self._matches_slot_report(ref, slot))
            admitted = [
                ref for ref in refs
                if evidence_text_is_admitted(ref.text)
                and self._matches_slot_issuer(ref, slot)
                and ref.lineage_status in {"root", "resolved"}
                and self._matches_slot_report(ref, slot)
            ]
            fused = fuse_slot_results([admitted], limit=min(slot.max_evidence, MAX_EVIDENCE_PER_SLOT))
            bounded: list[EvidenceRef] = []
            for ref in fused:
                if ref.evidence_id in admitted_ids or len(admitted_ids) < total_limit:
                    bounded.append(ref)
                    admitted_ids.add(ref.evidence_id)
            fused = bounded
            final_slot_ids = {ref.evidence_id for ref in fused}
            final_financial_facts = self._facts_with_final_evidence(financial_facts, final_slot_ids)
            final_event_facts = self._facts_with_final_evidence(event_facts, final_slot_ids)
            period_complete = True
            if slot.domain == "financial" and slot.min_periods > 1:
                periods_by_account: dict[str, set[tuple[object, object, object]]] = {}
                for fact in final_financial_facts:
                    account_id = str(fact.get("account_id") or "")
                    period = (fact.get("period_start"), fact.get("period_end"), fact.get("instant_date"))
                    if account_id and any(value is not None for value in period):
                        periods_by_account.setdefault(account_id, set()).add(period)
                expected_accounts = {
                    resolution.canonical_id
                    for concept in slot.search_concepts
                    for resolution in [resolve_financial_account(
                        concept,
                        catalog=self.financial_account_catalog,
                    )]
                    if resolution.status == "resolved"
                    and resolution.support_level == "structured"
                    and resolution.canonical_id is not None
                }
                period_complete = bool(expected_accounts) and all(
                    len(periods_by_account.get(account_id, set())) >= slot.min_periods
                    for account_id in expected_accounts
                )
            complete = len(fused) >= slot.min_evidence and period_complete
            slot_reasons: tuple[str, ...] = () if complete or not slot.mandatory else (f"required_slot_missing:{slot.slot_id}",)
            reasons.extend(slot_reasons)
            safe_diagnostics = dict(diagnostics)
            safe_diagnostics["excluded_prompt_injection_count"] = int(safe_diagnostics.get("excluded_prompt_injection_count", 0)) + prompt_excluded
            safe_diagnostics["wrong_issuer_count"] = int(safe_diagnostics.get("wrong_issuer_count", 0)) + wrong_issuer
            safe_diagnostics["wrong_version_count"] = int(safe_diagnostics.get("wrong_version_count", 0)) + wrong_version
            safe_diagnostics["wrong_report_type_count"] = wrong_report
            slots.append(SlotRetrieval(slot.slot_id, tuple(fused), complete, slot_reasons, safe_diagnostics))
            diagnostics_by_slot[slot.slot_id] = safe_diagnostics
            all_refs.extend(fused)
            all_financial_facts.extend(final_financial_facts)
            all_event_facts.extend(final_event_facts)

        unique_refs: list[EvidenceRef] = []
        seen_ids: set[str] = set()
        for ref in all_refs:
            if ref.evidence_id not in seen_ids and len(unique_refs) < total_limit:
                seen_ids.add(ref.evidence_id)
                unique_refs.append(ref)
        complete = not reasons and all(slot.complete or not next(item for item in plan.required_evidence_slots if item.slot_id == slot.slot_id).mandatory for slot in slots)
        return AnalysisRetrieval(
            evidence=tuple(unique_refs),
            slots=tuple(slots),
            complete=complete,
            reason_codes=tuple(dict.fromkeys(reasons)),
            retrieval_diagnostics={"slots": diagnostics_by_slot},
            financial_facts=tuple(self._facts_with_final_evidence(all_financial_facts, set(seen_ids))),
            event_facts=tuple(self._facts_with_final_evidence(all_event_facts, set(seen_ids))),
        )

    def _base_identity_valid(self) -> bool:
        if self.overlay_database is not None or self.search_database is not None:
            return self.attestation is not None and verify_fast_identity(self.base_database, self.attestation)
        return self.attestation is None or verify_fast_identity(self.base_database, self.attestation)

    def company_candidates(self) -> list[str]:
        if not self._base_identity_valid():
            self._companies = []
            return []
        if self._companies is None:
            with closing(_readonly_connection(self.base_database)) as connection:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(filing)")}
                sources = [name for name in ("issuer_name", "listed_name", "reporter_name") if name in columns]
                union = " UNION ".join(f"SELECT {name} FROM filing WHERE {name}<>''" for name in sources)
                values = connection.execute(union).fetchall() if union else []
            self._companies = sorted({str(row[0]) for row in values}, key=lambda value: (-len(value), value))
        return list(self._companies)

    def _title_value_refs(
        self,
        plan: QueryPlan,
        *,
        as_of: str | None = None,
        correction_policy: str = "current",
    ) -> list[EvidenceRef]:
        if not plan.company or not plan.filing_date or "제목" not in plan.question:
            return []
        company_fields = ["f.issuer_name=?", "f.listed_name=?", "f.stock_code=?", "f.issuer_corp_code=?"]
        params: list[object] = [plan.company] * len(company_fields)
        params.append(plan.filing_date)
        with closing(_readonly_connection(self.base_database)) as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT value.evidence_id
                       FROM table_cell label
                       JOIN table_cell value
                         ON value.table_id=label.table_id
                        AND value.filing_id=label.filing_id
                        AND value.row_index=label.row_index
                        AND value.column_index>label.column_index
                       JOIN filing f ON f.filing_id=value.filing_id
                      WHERE label.cell_kind='header'
                        AND label.text_normalized LIKE '%제목%'
                        AND value.cell_kind='data'
                        AND ({' OR '.join(company_fields)})
                        AND f.filed_at=?
                      ORDER BY value.table_id,value.row_index,value.column_index,value.evidence_id""",
                params,
            ).fetchall()
        return self._hydrate_ids(
            [str(row[0]) for row in rows],
            as_of=as_of,
            correction_policy=correction_policy,
        )

    def _expand_event_terms(self, terms: Iterable[str]) -> list[str]:
        expanded = list(dict.fromkeys(str(term) for term in terms if str(term)))
        for term in list(expanded):
            for aliases in self.event_predicate_aliases.values():
                if term in aliases:
                    expanded.extend(aliases)
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _select_correction_chain(facts: list[dict[str, object]]) -> list[dict[str, object]]:
        groups: dict[str, list[dict[str, object]]] = {}
        for fact in facts:
            event_id = str(fact.get("event_id") or "")
            if event_id:
                groups.setdefault(event_id, []).append(fact)
        chains = [group for group in groups.values() if len(group) > 1]
        if not chains:
            return facts
        selected = max(chains, key=lambda group: (len(group), str(group[0].get("event_id"))))
        return sorted(selected, key=lambda fact: (str(fact.get("effective_from") or ""), str(fact.get("filing_id") or "")))

    def _reporter_value_refs(
        self,
        plan: QueryPlan,
        *,
        as_of: str | None = None,
        correction_policy: str = "current",
    ) -> list[EvidenceRef]:
        if not plan.company or not plan.filing_date or "보고자" not in plan.question:
            return []
        company_fields = ["f.issuer_name=?", "f.listed_name=?", "f.stock_code=?", "f.issuer_corp_code=?"]
        params: list[object] = [plan.company] * len(company_fields)
        params.append(plan.filing_date)
        with closing(_readonly_connection(self.base_database)) as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT value.evidence_id
                       FROM table_cell label
                       JOIN table_cell value
                         ON value.table_id=label.table_id
                        AND value.filing_id=label.filing_id
                        AND value.row_index=label.row_index
                        AND value.column_index>label.column_index
                       JOIN filing f ON f.filing_id=value.filing_id
                      WHERE label.text_normalized LIKE '%보고자%'
                        AND value.cell_kind='data'
                        AND ({' OR '.join(company_fields)})
                        AND f.filed_at=?
                      ORDER BY value.table_id,value.row_index,value.column_index,value.evidence_id""",
                params,
            ).fetchall()
        refs = self._hydrate_ids(
            [str(row[0]) for row in rows],
            as_of=as_of,
            correction_policy=correction_policy,
        )
        candidates = self.company_candidates()
        preferred = [
            ref for ref in refs
            if any(
                candidate
                and candidate not in plan.company
                and plan.company not in candidate
                and candidate in ref.text
                for candidate in candidates
            )
        ]
        if preferred:
            return [max(preferred, key=lambda ref: (len(ref.text.strip()), ref.text))]
        return refs[:1]

    def search(self, plan: QueryPlan, *, limit: int = 20) -> EvidenceBundle:
        if not self._base_identity_valid():
            plan.reason_codes.append("base_attestation_failed")
            return EvidenceBundle(
                question=plan.question,
                answerable=False,
                reason_codes=list(plan.reason_codes),
            )
        if plan.operation in {"count_above", "list_above", "rank"}:
            return self._search_corpus_financial(plan)
        if plan.account_status in {"ambiguous", "unsupported"} or "financial_account_unknown" in plan.reason_codes:
            reason = (
                "financial_account_clarification_required"
                if plan.account_status == "ambiguous"
                else "financial_account_unsupported"
                if plan.account_status == "unsupported"
                else "financial_account_unknown"
            )
            return EvidenceBundle(
                question=plan.question,
                answerable=False,
                reason_codes=list(dict.fromkeys([*plan.reason_codes, reason])),
            )
        if plan.fact_domain == "financial_derived" or (
            plan.account_support_level == "derived"
            and not (plan.operation == "growth_rate" and len(plan.required_account_ids) == 1)
        ):
            return EvidenceBundle(
                question=plan.question,
                answerable=False,
                reason_codes=list(dict.fromkeys([*plan.reason_codes, "derived_metric_calculation_not_implemented"])),
            )
        refs: list[EvidenceRef] = []
        financial_facts: list[dict[str, object]] = []
        event_facts: list[dict[str, object]] = []
        dense_diagnostics: dict[str, object] = {
            "dense_configured": self.dense_client is not None,
            "dense_used": False,
        }
        account_terms = list(plan.account_terms)
        for term in plan.account_terms:
            account_terms.extend(self.account_aliases.get(term, []))
        account_terms = list(dict.fromkeys(account_terms))
        overlay_attested = True
        version_as_of = plan.as_of if plan.as_of_source == "api" else (
            None if plan.period_start or plan.period_end or plan.instant_date else plan.as_of
        )
        filing_date = plan.filing_date
        structured_domain = plan.fact_domain in {"financial", "event"}
        if self.overlay_database and self.overlay_database.exists() and structured_domain:
            overlay_attested = overlay_matches_base(self.base_database, self.overlay_database, attestation=self.attestation)
            if not overlay_attested:
                plan.reason_codes.append("overlay_base_attestation_failed")
        if self.overlay_database and self.overlay_database.exists() and plan.fact_domain == "financial" and overlay_attested:
            # A year/month in the question is a financial period, not a point-in-time
            # knowledge cutoff. An API-provided as_of remains authoritative; only an
            # inferred cutoff is suppressed when a financial period is present.
            period_end_lte = plan.period_end if plan.operation in {"growth_rate", "difference", "ratio", "sum"} else None
            instant_date = plan.instant_date or (
                plan.period_end if plan.statement_type == "BS" and period_end_lte is None else None
            )
            exact_duration = not period_end_lte and instant_date is None
            lookup_account_id = (
                plan.required_account_ids[0]
                if plan.account_support_level == "derived" and len(plan.required_account_ids) == 1
                else plan.account_id
            )
            financial_facts = fetch_overlay_facts(
                self.base_database,
                self.overlay_database,
                company=plan.company,
                as_of=version_as_of,
                period_start=plan.period_start if exact_duration else None,
                period_end=plan.period_end if exact_duration else None,
                period_end_lte=period_end_lte,
                instant_date=instant_date,
                account_id=lookup_account_id,
                account_terms=[] if lookup_account_id else account_terms,
                statement_type=None if lookup_account_id else plan.statement_type,
                scope=plan.scope,
                correction_policy=plan.correction_policy,
                limit=min(limit, max(plan.latest_period_count, 2 if plan.operation in {"growth_rate", "difference", "ratio"} else 1)),
                attestation=self.attestation,
            )
            financial_facts, structured_refs = self._hydrate_facts(
                financial_facts, as_of=version_as_of, correction_policy=plan.correction_policy,
            )
            refs.extend(structured_refs)
        elif self.overlay_database and self.overlay_database.exists() and plan.fact_domain == "event" and overlay_attested:
            event_facts = fetch_event_facts(
                self.base_database,
                self.overlay_database,
                company=plan.company,
                predicate_terms=self._expand_event_terms(plan.predicate_terms),
                as_of=version_as_of,
                limit=limit,
                correction_policy=plan.correction_policy,
                attestation=self.attestation,
            )
            if plan.correction_policy == "both":
                event_facts = self._select_correction_chain(event_facts)
            event_facts, structured_refs = self._hydrate_facts(
                event_facts, as_of=version_as_of, correction_policy=plan.correction_policy,
            )
            refs.extend(structured_refs)
        # One admitted structured fact already carries exact table-cell evidence.
        # Filling the remaining generic limit would reopen and integrity-scan the
        # entire search index without contributing to the structured claim.
        if len(refs) < limit and not (structured_domain and refs):
            index_used = False
            index_available = False
            if self.search_database is not None:
                if self.search_database.exists():
                    index_available = True
                    try:
                        if self._search_index is None:
                            if self._search_index_error:
                                plan.reason_codes.append(self._search_index_error)
                            raise RuntimeError("validated search index unavailable")
                        rows = self._search_index.search(
                            plan.question,
                            company=plan.company,
                            as_of=version_as_of,
                            filed_at=filing_date,
                            limit=max(1, limit - len(refs)),
                            correction_policy=plan.correction_policy,
                        )
                        # A valid index is authoritative for this retrieval attempt,
                        # including a valid empty result; never silently mix it with
                        # an unsafe partial corpus result.
                        try:
                            retrieval_tokens(plan.question, company_names=[plan.company] if plan.company else None)
                        except ValueError:
                            plan.reason_codes.append("empty_search_query")
                        else:
                            sparse_refs = self._fragment_refs(rows)
                            dense_refs: list[EvidenceRef] = []
                            if not structured_domain:
                                dense_refs, dense_diagnostics = self._search_dense(
                                    plan.question,
                                    company=plan.company,
                                    as_of=version_as_of,
                                    filed_at=filing_date,
                                    correction_policy=plan.correction_policy,
                                )
                            refs.extend(
                                self._fuse_refs([sparse_refs, dense_refs], limit=30)
                                if dense_refs
                                else sparse_refs
                            )
                        index_used = True
                    except ValueError:
                        plan.reason_codes.append("search_index_attestation_mismatch")
                    except (OSError, sqlite3.Error):
                        plan.reason_codes.append("search_index_sqlite_error")
                    except RuntimeError:
                        pass
                if not index_used:
                    plan.reason_codes.append(
                        "search_index_unavailable" if not index_available else "search_index_fallback_to_ssot"
                    )
            if not index_used:
                try:
                    query = compile_retrieval_query(plan.question, company_names=[plan.company] if plan.company else self.company_candidates())
                except ValueError:
                    plan.reason_codes.append("empty_search_query")
                else:
                    rows = query_database(
                        self.base_database,
                        query,
                        company=plan.company,
                        limit=max(1, limit - len(refs)),
                        as_of=version_as_of,
                        filed_at=filing_date,
                        correction_policy=plan.correction_policy,
                    )
                    refs.extend(self._fragment_refs(rows))
        refs = [
            *self._title_value_refs(plan, as_of=version_as_of, correction_policy=plan.correction_policy),
            *self._reporter_value_refs(plan, as_of=version_as_of, correction_policy=plan.correction_policy),
            *refs,
        ]
        unique: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in refs:
            if ref.evidence_id not in seen and ref.lineage_status in {"root", "resolved"} and evidence_text_is_admitted(ref.text):
                seen.add(ref.evidence_id)
                unique.append(ref)
        final_limit = max(0, min(limit, 8))
        retrieval_diagnostics: dict[str, object] = dict(dense_diagnostics)
        ordered = unique
        if self.reranker is not None:
            if structured_domain:
                retrieval_diagnostics.update({
                    "reranker_used_provider": False,
                    "reranker_reason_codes": ["reranker_structured_bypass"],
                })
            else:
                rerank_result = self.reranker.rerank(plan.question, unique[:30], limit=final_limit)
                by_id = {ref.evidence_id: ref for ref in unique}
                reranked = [by_id[evidence_id] for evidence_id in rerank_result.evidence_ids if evidence_id in by_id]
                ordered = reranked or unique
                retrieval_diagnostics.update({
                    "reranker_used_provider": rerank_result.used_provider,
                    "reranker_reason_codes": list(rerank_result.reason_codes),
                })
        final_evidence = ordered[:final_limit]
        final_ids = {ref.evidence_id for ref in final_evidence}
        financial_facts = self._facts_with_final_evidence(financial_facts, final_ids)
        event_facts = self._facts_with_final_evidence(event_facts, final_ids)
        reasons = list(plan.reason_codes)
        if plan.question_type in {"adversarial", "out_of_scope"}:
            reasons.append("answering_disabled_for_question_type")
        if plan.fact_domain == "financial" and not financial_facts:
            reasons.append("validated_financial_fact_required")
        if plan.fact_domain == "event" and not event_facts:
            reasons.append("validated_event_fact_required")
        unresolved_company = plan.company is None or "company_unresolved" in plan.reason_codes
        if unresolved_company and "answering_disabled_for_unresolved_company" not in reasons:
            reasons.append("answering_disabled_for_unresolved_company")
        required_claim_terms = [term for term in ("승인", "완료", "체결", "해지", "변경") if term in plan.question]
        safe_text = [ref.text for ref in final_evidence]
        if required_claim_terms and any(not any(term in text for text in safe_text) for term in required_claim_terms):
            reasons.append("required_claim_term_missing")
        if not final_evidence:
            reasons.append("no_safe_evidence")
        answerable = bool(final_evidence) and plan.question_type not in {"adversarial", "out_of_scope"}
        if plan.fact_domain == "financial":
            answerable = answerable and bool(financial_facts)
        elif plan.fact_domain == "event":
            answerable = answerable and bool(event_facts)
        if unresolved_company or "required_claim_term_missing" in reasons:
            answerable = False
        return EvidenceBundle(
            question=plan.question,
            evidence=final_evidence,
            answerable=answerable,
            reason_codes=reasons,
            financial_facts=financial_facts,
            event_facts=event_facts,
            retrieval_diagnostics=retrieval_diagnostics,
        )

    def _search_corpus_financial(self, plan: QueryPlan) -> EvidenceBundle:
        reasons = list(plan.reason_codes)
        if (
            self.overlay_database is None
            or not self.overlay_database.exists()
            or self.attestation is None
            or not overlay_matches_base(self.base_database, self.overlay_database, attestation=self.attestation)
            or plan.account_id is None
            or (plan.operation in {"count_above", "list_above"} and plan.threshold_value is None)
        ):
            reasons.append("financial_coverage_not_available")
            return EvidenceBundle(question=plan.question, answerable=False, reason_codes=reasons)
        coverage = fetch_financial_coverage(
            self.base_database,
            self.overlay_database,
            account_id=plan.account_id,
            attestation=self.attestation,
        )
        metrics = list(coverage.get("metrics", [])) if coverage else []  # type: ignore[arg-type]
        metric = metrics[0] if len(metrics) == 1 else None
        if not metric or not bool(metric.get("aggregate_eligible")):
            reasons.append("corpus_wide_financial_coverage_incomplete")
            return EvidenceBundle(
                question=plan.question,
                answerable=False,
                reason_codes=reasons,
                coverage=coverage,
            )
        source_count = int(coverage["snapshot"]["source_company_count"])  # type: ignore[index]
        facts = fetch_overlay_facts(
            self.base_database,
            self.overlay_database,
            account_id=plan.account_id,
            limit=max(source_count * 3, source_count),
            attestation=self.attestation,
        )
        latest_by_company: dict[str, dict[str, object]] = {}
        for fact in facts:
            corp_code = str(fact.get("issuer_corp_code") or "")
            if corp_code and corp_code not in latest_by_company:
                latest_by_company[corp_code] = fact
        if len(latest_by_company) != source_count:
            reasons.append("corpus_wide_financial_runtime_count_mismatch")
            return EvidenceBundle(question=plan.question, answerable=False, reason_codes=reasons, coverage=coverage)
        latest = list(latest_by_company.values())
        hydrated_facts, refs = self._hydrate_facts(latest)
        if len(hydrated_facts) != source_count:
            reasons.append("corpus_wide_financial_evidence_incomplete")
            return EvidenceBundle(question=plan.question, answerable=False, reason_codes=reasons, coverage=coverage)
        normalized: list[tuple[dict[str, object], Decimal]] = []
        try:
            for fact in hydrated_facts:
                value = Decimal(str(fact["value_numeric"])) * Decimal(int(fact.get("scale") or 1))
                if not value.is_finite():
                    raise InvalidOperation
                normalized.append((fact, value))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            reasons.append("corpus_wide_financial_value_invalid")
            return EvidenceBundle(question=plan.question, answerable=False, reason_codes=reasons, coverage=coverage)
        threshold = plan.threshold_value
        matches = sorted(
            (
                (fact, value)
                for fact, value in normalized
                if threshold is None
                or value > threshold
                or (plan.threshold_inclusive and value == threshold)
            ),
            key=lambda item: (-item[1], str(item[0].get("listed_name") or item[0].get("issuer_name") or "")),
        )
        selected = matches[:plan.top_n] if plan.operation == "rank" else matches
        unique_refs: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in refs:
            if ref.evidence_id not in seen:
                seen.add(ref.evidence_id)
                unique_refs.append(ref)
        evidence_ids = [ref.evidence_id for ref in unique_refs]
        calculation = None
        if plan.operation == "count_above":
            calculation = CalculationResult(
                operation="count_above",
                value=Decimal(len(matches)),
                unit="개",
                evidence_ids=evidence_ids,
                operands=[value for _, value in normalized],
            )
        aggregate_result = {
            "operation": plan.operation,
            "account_id": plan.account_id,
            "threshold_value": str(plan.threshold_value) if plan.threshold_value is not None else None,
            "company_count": len(selected),
            "companies": [
                {
                    "issuer_corp_code": fact.get("issuer_corp_code"),
                    "listed_name": fact.get("listed_name") or fact.get("issuer_name"),
                    "fiscal_year": fact.get("fiscal_year"),
                    "value_numeric": fact.get("value_numeric"),
                    "scale": fact.get("scale"),
                    "scope": fact.get("scope"),
                    "filing_id": fact.get("filing_id"),
                }
                for fact, _ in selected
            ],
        }
        return EvidenceBundle(
            question=plan.question,
            evidence=unique_refs,
            answerable=True,
            reason_codes=reasons,
            financial_facts=hydrated_facts,
            calculation=calculation,
            coverage=coverage,
            aggregate_result=aggregate_result,
        )

    def _hydrate_facts(
        self,
        facts: Iterable[dict[str, object]],
        *,
        as_of: str | None = None,
        correction_policy: str = "current",
    ) -> tuple[list[dict[str, object]], list[EvidenceRef]]:
        """Admit structured facts only when at least one declared evidence ID hydrates safely."""
        eligible: list[dict[str, object]] = []
        refs: list[EvidenceRef] = []
        for raw_fact in facts:
            fact = dict(raw_fact)
            declared_ids = [str(item) for item in fact.get("evidence_ids", [])]  # type: ignore[union-attr]
            hydrated = self._hydrate_ids(declared_ids, as_of=as_of, correction_policy=correction_policy)
            hydrated = [ref for ref in hydrated if evidence_text_is_admitted(ref.text)]
            if not hydrated:
                continue
            hydrated_ids = {ref.evidence_id for ref in hydrated}
            fact["evidence_ids"] = [item for item in declared_ids if item in hydrated_ids]
            if not fact["evidence_ids"]:
                continue
            eligible.append(fact)
            refs.extend(hydrated)
        return eligible, refs

    @staticmethod
    def _facts_with_final_evidence(
        facts: Iterable[dict[str, object]], final_ids: set[str],
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for raw_fact in facts:
            fact = dict(raw_fact)
            ids = [str(item) for item in fact.get("evidence_ids", [])]  # type: ignore[union-attr]
            ids = [item for item in ids if item in final_ids]
            if ids:
                fact["evidence_ids"] = ids
                result.append(fact)
        return result

    def _financial_refs(self, facts: Iterable[dict[str, object]], *, as_of: str | None = None, correction_policy: str = "current") -> list[EvidenceRef]:
        ids = [str(evidence_id) for fact in facts for evidence_id in fact.get("evidence_ids", [])]  # type: ignore[union-attr]
        return self._hydrate_ids(ids, as_of=as_of, correction_policy=correction_policy)

    def _fragment_refs(self, rows: Iterable[dict[str, object]]) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for row in rows:
            if str(row.get("detected_format") or "") == "pdf" or int(row.get("image_reference_count") or 0) > 0:
                continue
            text = str(row.get("text_normalized") or "")
            if not evidence_text_is_admitted(text):
                continue
            try:
                locator = json.loads(str(row.get("locator_json") or "{}"))
            except json.JSONDecodeError:
                locator = {}
            refs.append(EvidenceRef(
                evidence_id=str(row["evidence_id"]), filing_id=str(row["filing_id"]),
                source_id=str(row.get("source_id") or ""), text=text,
                locator=locator, lineage_status=str(row.get("lineage_status") or ""),
                score=float(row.get("score") or 0.0), source_path=None,
                filed_at=str(row.get("filed_at") or ""), report_name=str(row.get("report_name_raw") or ""),
                is_current=bool(row.get("is_current")) if row.get("is_current") is not None else None,
            ))
        return refs

    def _hydrate_ids(self, evidence_ids: Iterable[str], *, as_of: str | None = None, correction_policy: str = "current") -> list[EvidenceRef]:
        ids = list(dict.fromkeys(str(item) for item in evidence_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        if correction_policy == "original":
            version_sql, version_params = "v.lineage_status='root'", []
            if as_of is not None:
                version_sql += " AND v.effective_from<=? AND (v.effective_to IS NULL OR ? < v.effective_to)"
                version_params = [as_of, as_of]
        elif correction_policy == "both":
            version_sql, version_params = "v.lineage_status IN ('root','resolved')", []
            if as_of is not None:
                version_sql += " AND v.effective_from<=?"
                version_params = [as_of]
        elif as_of is None:
            version_sql, version_params = "v.lineage_status IN ('root','resolved') AND v.is_current=1", []
        else:
            version_sql, version_params = "v.lineage_status IN ('root','resolved') AND v.effective_from<=? AND (v.effective_to IS NULL OR ? < v.effective_to)", [as_of, as_of]
        if correction_policy == "corrected":
            version_sql += " AND f.is_correction=1"
        with closing(_readonly_connection(self.base_database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""SELECT x.evidence_id,x.filing_id,x.source_id,x.text_value,x.locator_json,
                          s.source_path,s.detected_format,s.coverage_json,f.filed_at,f.report_name_raw,v.lineage_status,v.is_current
                     FROM (
                       SELECT evidence_id,filing_id,source_id,text_normalized AS text_value,locator_json
                         FROM fragment WHERE evidence_id IN ({placeholders})
                       UNION ALL
                       SELECT evidence_id,filing_id,source_id,text_normalized AS text_value,locator_json
                         FROM table_cell WHERE evidence_id IN ({placeholders})
                     ) x JOIN source_document s ON s.source_id=x.source_id
                     JOIN filing f ON f.filing_id=x.filing_id JOIN filing_version v ON v.filing_id=x.filing_id
                    WHERE s.parse_status='success' AND {version_sql}""",
                [*ids, *ids, *version_params],
            ).fetchall()
        result: list[EvidenceRef] = []
        for row in rows:
            # Overlay facts are admitted only from textual table cells; PDF evidence
            # is blocked consistently with the generic fragment serving path.
            if str(row["detected_format"] or "") == "pdf":
                continue
            text = str(row["text_value"])
            if not evidence_text_is_admitted(text):
                continue
            try:
                locator = json.loads(str(row["locator_json"] or "{}"))
            except json.JSONDecodeError:
                locator = {}
            result.append(EvidenceRef(
                evidence_id=str(row["evidence_id"]), filing_id=str(row["filing_id"]),
                source_id=str(row["source_id"]), text=text, locator=locator,
                lineage_status=str(row["lineage_status"]), source_path=str(row["source_path"]),
                filed_at=str(row["filed_at"]), report_name=str(row["report_name_raw"]),
                is_current=bool(row["is_current"]),
            ))
        return result
