"""Immutable-ledger overlay for human-validated financial statement facts.

The 38 GB semantic corpus remains a read-only source of truth.  This module stores only a
small, auditable set of validated financial facts in a separate SQLite file and joins back to
the ledger for evidence at read time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .serving import version_filter_sql


OVERLAY_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS overlay_revision(
    overlay_revision TEXT PRIMARY KEY,
    source_database_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seed_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_fact(
    financial_fact_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL,
    account_id TEXT,
    account_name_raw TEXT NOT NULL,
    statement_type TEXT NOT NULL CHECK(statement_type IN ('BS','IS','CIS','CF','SCE','UNKNOWN')),
    scope TEXT NOT NULL CHECK(scope IN ('consolidated','separate','unknown')),
    period_type TEXT NOT NULL CHECK(period_type IN ('instant','duration','unknown')),
    period_start TEXT,
    period_end TEXT,
    instant_date TEXT,
    value_numeric TEXT NOT NULL,
    currency TEXT,
    scale INTEGER NOT NULL CHECK(scale > 0),
    unit_raw TEXT,
    extraction_method TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status='validated')
);
CREATE TABLE IF NOT EXISTS financial_fact_evidence(
    financial_fact_id TEXT NOT NULL REFERENCES financial_fact(financial_fact_id),
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(financial_fact_id,evidence_id)
);
CREATE INDEX IF NOT EXISTS overlay_fact_filing_account ON financial_fact(filing_id,account_id,account_name_raw);
"""


@dataclass(slots=True)
class OverlayImportResult:
    imported: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)
    source_database_sha256: str = ""
    seed_sha256: str = ""


class FinancialOverlay:
    """Convenience wrapper used by API/CLI callers."""

    def __init__(self, base_database: Path, overlay_database: Path):
        self.base_database = Path(base_database)
        self.overlay_database = Path(overlay_database)

    def import_seed(self, seed_path: Path) -> OverlayImportResult:
        return import_seed(self.base_database, self.overlay_database, Path(seed_path))

    def fetch(self, **kwargs: Any) -> list[dict[str, object]]:
        return fetch_overlay_facts(self.base_database, self.overlay_database, **kwargs)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _overlay_matches_base_cached(base_name: str, overlay_name: str, base_size: int, base_mtime: int, overlay_size: int, overlay_mtime: int) -> bool:
    try:
        with closing(sqlite3.connect(overlay_name)) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT source_database_sha256 FROM overlay_revision WHERE overlay_revision=?",
                ("semantic-v1-agent-overlay",),
            ).fetchone()
        return row is not None and str(row[0]) == sha256_file(Path(base_name))
    except (OSError, sqlite3.Error):
        return False


def overlay_matches_base(base_database: Path, overlay_database: Path) -> bool:
    """Fail closed if an overlay was built from a different immutable base file."""
    if not Path(overlay_database).exists():
        return False
    try:
        base_stat, overlay_stat = Path(base_database).stat(), Path(overlay_database).stat()
        return _overlay_matches_base_cached(
            str(Path(base_database).resolve()), str(Path(overlay_database).resolve()),
            int(base_stat.st_size), int(base_stat.st_mtime_ns), int(overlay_stat.st_size), int(overlay_stat.st_mtime_ns),
        )
    except OSError:
        return False


def _read_base(path: Path) -> sqlite3.Connection:
    # URI mode=ro is intentional: an importer must never mutate the immutable corpus.
    connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _date_ok(value: object | None) -> bool:
    if value in (None, ""):
        return False
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return False
    return True


def _period_ok(row: dict[str, Any]) -> bool:
    period_type = str(row.get("period_type", ""))
    if period_type == "instant":
        return bool(_date_ok(row.get("instant_date")) and not row.get("period_start") and not row.get("period_end"))
    if period_type == "duration":
        return bool(_date_ok(row.get("period_start")) and _date_ok(row.get("period_end")) and row["period_start"] <= row["period_end"] and not row.get("instant_date"))
    return period_type == "unknown"


def _base_evidence(connection: sqlite3.Connection, filing_id: str, evidence_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT c.evidence_id,c.filing_id,c.source_id,c.text_raw,c.locator_json,
                  s.parse_status,s.detected_format,tr.parse_status AS table_parse_status,v.lineage_status
           FROM table_cell c
           JOIN table_record tr ON tr.table_id=c.table_id
           JOIN source_document s ON s.source_id=c.source_id
           JOIN filing_version v ON v.filing_id=c.filing_id
          WHERE c.evidence_id=? AND c.filing_id=?""",
        (evidence_id, filing_id),
    ).fetchone()


def import_seed(base_database: Path, overlay_database: Path, seed_path: Path) -> OverlayImportResult:
    base_database, overlay_database, seed_path = map(Path, (base_database, overlay_database, seed_path))
    source_sha = sha256_file(base_database)
    seed_sha = sha256_file(seed_path)
    result = OverlayImportResult(source_database_sha256=source_sha, seed_sha256=seed_sha)
    validated_rows: list[dict[str, Any]] = []
    with closing(_read_base(base_database)) as base:
        for line_no, line in enumerate(seed_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row is not an object")
                _validate_seed_row(base, row)
                validated_rows.append(row)
            except (ValueError, KeyError, TypeError, InvalidOperation, sqlite3.Error) as exc:
                result.rejected += 1
                result.reasons.append(f"line {line_no}: {exc}")
    overlay_database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(overlay_database)) as overlay:
        overlay.execute("PRAGMA foreign_keys=ON")
        overlay.executescript(OVERLAY_SCHEMA)
        try:
            overlay.execute("BEGIN IMMEDIATE")
            existing = overlay.execute(
                "SELECT source_database_sha256,seed_sha256 FROM overlay_revision WHERE overlay_revision=?",
                ("semantic-v1-agent-overlay",),
            ).fetchone()
            if existing is not None and str(existing[0]) != source_sha:
                raise ValueError("overlay source_database_sha256 does not match immutable base")
            if existing is not None and str(existing[1]) != seed_sha:
                overlay.execute("DELETE FROM financial_fact_evidence")
                overlay.execute("DELETE FROM financial_fact")
            columns = (
                "financial_fact_id", "filing_id", "account_id", "account_name_raw", "statement_type",
                "scope", "period_type", "period_start", "period_end", "instant_date", "value_numeric",
                "currency", "scale", "unit_raw", "extraction_method", "validation_status",
            )
            for row in validated_rows:
                overlay.execute(
                    f"INSERT OR REPLACE INTO financial_fact({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    tuple(row.get(column) for column in columns),
                )
                overlay.execute("DELETE FROM financial_fact_evidence WHERE financial_fact_id=?", (row["financial_fact_id"],))
                overlay.executemany(
                    "INSERT INTO financial_fact_evidence(financial_fact_id,evidence_id) VALUES(?,?)",
                    [(row["financial_fact_id"], evidence_id) for evidence_id in row["evidence_ids"]],
                )
            overlay.execute(
                "INSERT OR REPLACE INTO overlay_revision VALUES(?,?,datetime('now'),?)",
                ("semantic-v1-agent-overlay", source_sha, seed_sha),
            )
            overlay.commit()
            result.imported = len(validated_rows)
        except Exception:
            overlay.rollback()
            raise
    return result


def _validate_seed_row(base: sqlite3.Connection, row: dict[str, Any]) -> None:
    required = ("financial_fact_id", "filing_id", "account_name_raw", "statement_type", "scope", "period_type", "value_numeric", "scale", "extraction_method", "validation_status", "evidence_ids")
    missing = [key for key in required if key not in row]
    if missing:
        raise ValueError(f"missing fields: {missing}")
    if row["validation_status"] != "validated":
        raise ValueError("only validated rows may enter overlay")
    filing = base.execute("SELECT filing_id FROM filing WHERE filing_id=?", (row["filing_id"],)).fetchone()
    if filing is None:
        raise ValueError("filing does not exist")
    version = base.execute("SELECT lineage_status FROM filing_version WHERE filing_id=?", (row["filing_id"],)).fetchone()
    if version is None or version[0] not in ("root", "resolved"):
        raise ValueError("filing lineage is not answer-safe")
    if not row["evidence_ids"] or not isinstance(row["evidence_ids"], list):
        raise ValueError("validated fact needs evidence_ids")
    for evidence_id in row["evidence_ids"]:
        evidence = _base_evidence(base, str(row["filing_id"]), str(evidence_id))
        if evidence is None:
            raise ValueError(f"evidence not found or cross-filing: {evidence_id}")
        if evidence["parse_status"] != "success":
            raise ValueError(f"evidence source is not parse-success: {evidence_id}")
        if evidence["table_parse_status"] != "success":
            raise ValueError(f"evidence table is not parse-success: {evidence_id}")
        if evidence["detected_format"] == "pdf" and str(row.get("extraction_method")) != "human_validated":
            raise ValueError(f"PDF evidence requires human_validated extraction: {evidence_id}")
    try:
        value = Decimal(str(row["value_numeric"]))
    except InvalidOperation as exc:
        raise ValueError("value_numeric is not Decimal-parseable") from exc
    if not value.is_finite():
        raise ValueError("value_numeric must be finite")
    if int(row["scale"]) <= 0:
        raise ValueError("scale must be positive")
    if str(row["statement_type"]) not in {"BS", "IS", "CIS", "CF", "SCE", "UNKNOWN"}:
        raise ValueError("invalid statement_type")
    if str(row["scope"]) not in {"consolidated", "separate", "unknown"}:
        raise ValueError("invalid scope")
    if not _period_ok(row):
        raise ValueError("invalid period fields")


def fetch_overlay_facts(
    base_database: Path,
    overlay_database: Path,
    *,
    filing_id: str | None = None,
    company: str | None = None,
    account_id: str | None = None,
    account_terms: Iterable[str] = (),
    statement_type: str | None = None,
    scope: str | None = None,
    correction_policy: str = "current",
    as_of: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    if limit <= 0 or not Path(overlay_database).exists():
        return []
    if not overlay_matches_base(Path(base_database), Path(overlay_database)):
        return []
    version_sql, version_params = version_filter_sql(alias="v", as_of=as_of)
    if correction_policy == "original":
        version_sql = "v.lineage_status='root'"
        version_params = []
        if as_of is not None:
            version_sql += " AND v.effective_from<=? AND (v.effective_to IS NULL OR ? < v.effective_to)"
            version_params = [as_of, as_of]
    where = ["ff.validation_status='validated'", "s.parse_status='success'", version_sql]
    params: list[object] = list(version_params)
    with closing(_read_base(Path(base_database))) as schema_connection:
        filing_columns = {str(row[1]) for row in schema_connection.execute("PRAGMA table_info(filing)")}
    reporter_select = "f.reporter_name" if "reporter_name" in filing_columns else "NULL AS reporter_name"
    if filing_id is not None:
        where.append("ff.filing_id=?")
        params.append(filing_id)
    if company is not None:
        company_fields = ["f.issuer_name=?", "f.listed_name=?", "f.stock_code=?", "f.issuer_corp_code=?"]
        if "reporter_name" in filing_columns:
            company_fields.insert(2, "f.reporter_name=?")
        where.append(f"({' OR '.join(company_fields)})")
        params.extend([company] * len(company_fields))
    if account_id is not None:
        where.append("ff.account_id=?")
        params.append(account_id)
    terms = [str(term) for term in account_terms if str(term)]
    if terms:
        where.append("(" + " OR ".join("ff.account_name_raw LIKE ?" for _ in terms) + ")")
        params.extend([f"%{term}%" for term in terms])
    if statement_type is not None:
        where.append("ff.statement_type=?")
        params.append(statement_type)
    if scope is not None:
        where.append("ff.scope=?")
        params.append(scope)
    if correction_policy == "corrected":
        where.append("f.is_correction=1")
    params.append(limit)
    with closing(_read_base(Path(base_database))) as connection:
        # Plain resolved Windows paths are more portable for ATTACH than URI filenames. The
        # connection is query-only, so the attached overlay cannot be mutated by this read.
        connection.execute("PRAGMA query_only=ON")
        connection.execute("ATTACH DATABASE ? AS overlay", (str(Path(overlay_database).resolve()),))
        rows = connection.execute(
            f"""SELECT ff.*,f.issuer_name,{reporter_select},f.report_name_raw,f.filed_at,
                       v.lineage_status,v.is_current,group_concat(DISTINCT ffe.evidence_id) evidence_ids,
                       group_concat(DISTINCT c.text_raw) evidence_texts
                  FROM overlay.financial_fact ff
                  JOIN main.filing f ON f.filing_id=ff.filing_id
                  JOIN main.filing_version v ON v.filing_id=ff.filing_id
                  JOIN overlay.financial_fact_evidence ffe ON ffe.financial_fact_id=ff.financial_fact_id
                  JOIN main.table_cell c ON c.evidence_id=ffe.evidence_id
                  JOIN main.source_document s ON s.source_id=c.source_id
                 WHERE {' AND '.join(where)}
                 GROUP BY ff.financial_fact_id ORDER BY ff.financial_fact_id LIMIT ?""",
            params,
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["evidence_ids"] = str(item.get("evidence_ids") or "").split(",") if item.get("evidence_ids") else []
        item["evidence_texts"] = str(item.get("evidence_texts") or "").split(",") if item.get("evidence_texts") else []
        result.append(item)
    return result
