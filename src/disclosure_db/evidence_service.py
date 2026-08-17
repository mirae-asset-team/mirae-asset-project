"""Safe evidence retrieval over the immutable corpus and financial overlay."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from .agent_contracts import EvidenceBundle, EvidenceRef, QueryPlan
from .financial_overlay import fetch_overlay_facts
from .pipeline import query_database
from .retrieval_evaluation import compile_retrieval_query


class EvidenceService:
    def __init__(self, base_database: Path, overlay_database: Path | None = None, *, corpus_revision: str = "semantic-v1"):
        self.base_database = Path(base_database)
        self.overlay_database = Path(overlay_database) if overlay_database else None
        self.corpus_revision = corpus_revision
        self._companies: list[str] | None = None

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
        if self.overlay_database and self.overlay_database.exists() and plan.account_terms:
            financial_facts = fetch_overlay_facts(
                self.base_database,
                self.overlay_database,
                company=plan.company,
                as_of=plan.as_of,
                limit=limit,
            )
            refs.extend(self._financial_refs(financial_facts))
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
            if ref.evidence_id not in seen and ref.lineage_status in {"root", "resolved"}:
                seen.add(ref.evidence_id)
                unique.append(ref)
        reasons = list(plan.reason_codes)
        if not unique:
            reasons.append("no_safe_evidence")
        return EvidenceBundle(
            question=plan.question,
            evidence=unique[:limit],
            answerable=bool(unique),
            reason_codes=reasons,
            financial_facts=financial_facts,
        )

    def _financial_refs(self, facts: Iterable[dict[str, object]]) -> list[EvidenceRef]:
        ids = [str(evidence_id) for fact in facts for evidence_id in fact.get("evidence_ids", [])]  # type: ignore[union-attr]
        return self._hydrate_ids(ids)

    def _fragment_refs(self, rows: Iterable[dict[str, object]]) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for row in rows:
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

    def _hydrate_ids(self, evidence_ids: Iterable[str]) -> list[EvidenceRef]:
        ids = list(dict.fromkeys(str(item) for item in evidence_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
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
                    WHERE s.parse_status='success' AND v.lineage_status IN ('root','resolved') AND v.is_current=1""",
                [*ids, *ids],
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
