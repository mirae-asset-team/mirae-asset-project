"""Safe evidence retrieval over the immutable corpus and financial overlay."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from .agent_contracts import EvidenceBundle, EvidenceRef, QueryPlan
from .financial_overlay import fetch_overlay_facts, overlay_matches_base
from .pipeline import query_database
from .retrieval_evaluation import compile_retrieval_query


_PROMPT_INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "system prompt", "developer message",
    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
)


class EvidenceService:
    def __init__(self, base_database: Path, overlay_database: Path | None = None, *, corpus_revision: str = "semantic-v1"):
        self.base_database = Path(base_database)
        self.overlay_database = Path(overlay_database) if overlay_database else None
        self.corpus_revision = corpus_revision
        self._companies: list[str] | None = None
        aliases_path = Path(__file__).resolve().parents[2] / "config" / "financial_account_aliases.json"
        try:
            raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
            self.account_aliases = {str(key): [str(item) for item in values] for key, values in raw_aliases.items()}
        except (OSError, json.JSONDecodeError):
            self.account_aliases = {}

    def company_candidates(self) -> list[str]:
        if self._companies is None:
            with closing(sqlite3.connect(f"file:{self.base_database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(filing)")}
                sources = [name for name in ("issuer_name", "listed_name", "reporter_name") if name in columns]
                union = " UNION ".join(f"SELECT {name} FROM filing WHERE {name}<>''" for name in sources)
                values = connection.execute(union).fetchall() if union else []
            self._companies = sorted({str(row[0]) for row in values}, key=lambda value: (-len(value), value))
        return list(self._companies)

    def search(self, plan: QueryPlan, *, limit: int = 20) -> EvidenceBundle:
        refs: list[EvidenceRef] = []
        financial_facts: list[dict[str, object]] = []
        account_terms = list(plan.account_terms)
        for term in plan.account_terms:
            account_terms.extend(self.account_aliases.get(term, []))
        account_terms = list(dict.fromkeys(account_terms))
        overlay_attested = True
        if self.overlay_database and self.overlay_database.exists() and plan.question_type == "numeric":
            overlay_attested = overlay_matches_base(self.base_database, self.overlay_database)
            if not overlay_attested:
                plan.reason_codes.append("overlay_base_attestation_failed")
        if self.overlay_database and self.overlay_database.exists() and plan.question_type == "numeric" and overlay_attested:
            financial_facts = fetch_overlay_facts(
                self.base_database,
                self.overlay_database,
                company=plan.company,
                as_of=plan.as_of,
                account_terms=account_terms,
                statement_type=plan.statement_type,
                scope=plan.scope,
                correction_policy=plan.correction_policy,
                limit=limit,
            )
            refs.extend(self._financial_refs(financial_facts, as_of=plan.as_of, correction_policy=plan.correction_policy))
        if len(refs) < limit:
            try:
                query = compile_retrieval_query(plan.question, company_names=[plan.company] if plan.company else self.company_candidates())
            except ValueError:
                query = " OR ".join(f'"{term}"' for term in plan.account_terms) or '"공시"'
            rows = query_database(
                self.base_database,
                query,
                company=plan.company,
                limit=max(1, limit - len(refs)),
                as_of=plan.as_of,
            )
            refs.extend(self._fragment_refs(rows))
        unique: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in refs:
            if ref.evidence_id not in seen and ref.lineage_status in {"root", "resolved"} and not any(marker in ref.text.casefold() for marker in _PROMPT_INJECTION_MARKERS):
                seen.add(ref.evidence_id)
                unique.append(ref)
        reasons = list(plan.reason_codes)
        if plan.question_type in {"adversarial", "out_of_scope", "event_numeric"}:
            reasons.append("answering_disabled_for_question_type")
        elif plan.question_type == "numeric" and not financial_facts:
            reasons.append("validated_financial_fact_required")
        if not unique:
            reasons.append("no_safe_evidence")
        answerable = (
            bool(unique) and plan.question_type not in {"numeric", "adversarial", "out_of_scope", "event_numeric"}
        ) or (
            bool(financial_facts) and plan.question_type == "numeric" and bool(unique)
        )
        return EvidenceBundle(
            question=plan.question,
            evidence=unique[:limit],
            answerable=answerable,
            reason_codes=reasons,
            financial_facts=financial_facts,
        )

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
        elif as_of is None:
            version_sql, version_params = "v.lineage_status IN ('root','resolved') AND v.is_current=1", []
        else:
            version_sql, version_params = "v.lineage_status IN ('root','resolved') AND v.effective_from<=? AND (v.effective_to IS NULL OR ? < v.effective_to)", [as_of, as_of]
        if correction_policy == "corrected":
            version_sql += " AND f.is_correction=1"
        with closing(sqlite3.connect(f"file:{self.base_database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""SELECT x.evidence_id,x.filing_id,x.source_id,x.text_value,x.locator_json,
                          s.source_path,f.filed_at,f.report_name_raw,v.lineage_status,v.is_current
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
