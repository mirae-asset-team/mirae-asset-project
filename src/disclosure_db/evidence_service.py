"""Safe evidence retrieval over the immutable corpus and financial overlay."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .attestation import CorpusAttestation, verify_fast_identity
from .agent_contracts import CalculationResult, EvidenceBundle, EvidenceRef, QueryPlan
from .financial_overlay import fetch_event_facts, fetch_financial_coverage, fetch_overlay_facts, overlay_matches_base
from .pipeline import query_database
from .retrieval_evaluation import compile_retrieval_query, retrieval_tokens
from .reranker import ClovaReranker


_PROMPT_INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "system prompt", "developer message",
    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
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
    ):
        self.base_database = Path(base_database)
        self.overlay_database = Path(overlay_database) if overlay_database else None
        self.corpus_revision = corpus_revision
        self.attestation = attestation
        self.search_database = Path(search_database) if search_database else None
        self.reranker = reranker
        if (self.overlay_database is not None or self.search_database is not None) and attestation is None:
            raise ValueError("attestation is required when runtime overlay/search is configured")
        self._companies: list[str] | None = None
        config_directory = Path(
            os.environ.get(
                "DISCLOSURE_CONFIG_DIR",
                Path(__file__).resolve().parents[2] / "config",
            )
        )
        aliases_path = config_directory / "financial_account_aliases.json"
        try:
            raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
            self.account_aliases = {str(key): [str(item) for item in values] for key, values in raw_aliases.items()}
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
        refs: list[EvidenceRef] = []
        financial_facts: list[dict[str, object]] = []
        event_facts: list[dict[str, object]] = []
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
            financial_facts = fetch_overlay_facts(
                self.base_database,
                self.overlay_database,
                company=plan.company,
                as_of=version_as_of,
                period_start=plan.period_start if exact_duration else None,
                period_end=plan.period_end if exact_duration else None,
                period_end_lte=period_end_lte,
                instant_date=instant_date,
                account_id=plan.account_id,
                account_terms=[] if plan.account_id else account_terms,
                statement_type=None if plan.account_id else plan.statement_type,
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
                        if self.attestation is None:
                            raise ValueError("search index requires base attestation")
                        from .search_index import SafeSearchIndex
                        rows = SafeSearchIndex(
                            self.search_database,
                            base_sha256=self.attestation.sha256,
                            expected_base_size=self.attestation.size_bytes,
                        ).search(
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
                            refs.extend(self._fragment_refs(rows))
                        index_used = True
                    except ValueError:
                        plan.reason_codes.append("search_index_attestation_mismatch")
                    except (OSError, sqlite3.Error):
                        plan.reason_codes.append("search_index_sqlite_error")
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
            if ref.evidence_id not in seen and ref.lineage_status in {"root", "resolved"} and not any(marker in ref.text.casefold() for marker in _PROMPT_INJECTION_MARKERS):
                seen.add(ref.evidence_id)
                unique.append(ref)
        final_limit = max(0, min(limit, 8))
        retrieval_diagnostics: dict[str, object] = {}
        ordered = unique
        if self.reranker is not None:
            if structured_domain:
                retrieval_diagnostics = {
                    "reranker_used_provider": False,
                    "reranker_reason_codes": ["reranker_structured_bypass"],
                }
            else:
                rerank_result = self.reranker.rerank(plan.question, unique[:30], limit=final_limit)
                by_id = {ref.evidence_id: ref for ref in unique}
                reranked = [by_id[evidence_id] for evidence_id in rerank_result.evidence_ids if evidence_id in by_id]
                ordered = reranked or unique
                retrieval_diagnostics = {
                    "reranker_used_provider": rerank_result.used_provider,
                    "reranker_reason_codes": list(rerank_result.reason_codes),
                }
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
            try:
                locator = json.loads(str(row.get("locator_json") or "{}"))
            except json.JSONDecodeError:
                locator = {}
            refs.append(EvidenceRef(
                evidence_id=str(row["evidence_id"]), filing_id=str(row["filing_id"]),
                source_id=str(row.get("source_id") or ""), text=str(row.get("text_normalized") or ""),
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
            try:
                locator = json.loads(str(row["locator_json"] or "{}"))
            except json.JSONDecodeError:
                locator = {}
            result.append(EvidenceRef(
                evidence_id=str(row["evidence_id"]), filing_id=str(row["filing_id"]),
                source_id=str(row["source_id"]), text=str(row["text_value"]), locator=locator,
                lineage_status=str(row["lineage_status"]), source_path=str(row["source_path"]),
                filed_at=str(row["filed_at"]), report_name=str(row["report_name_raw"]),
                is_current=bool(row["is_current"]),
            ))
        return result
