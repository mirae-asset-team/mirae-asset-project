"""Immutable-ledger overlay for human-validated financial statement facts.

The 38 GB semantic corpus remains a read-only source of truth.  This module stores only a
small, auditable set of validated financial facts in a separate SQLite file and joins back to
the ledger for evidence at read time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .attestation import CorpusAttestation, verify_fast_identity
from .serving import version_filter_sql


_DECIMAL_CELL = re.compile(
    r"^[+-]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?$|^[+-]?[0-9]+(?:\.[0-9]+)?$"
)


OVERLAY_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE overlay_revision(
    overlay_revision TEXT PRIMARY KEY,
    source_database_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seed_sha256 TEXT NOT NULL
);
CREATE TABLE financial_fact(
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
    validation_status TEXT NOT NULL CHECK(validation_status='validated'),
    trust_tier TEXT NOT NULL CHECK(trust_tier IN ('human_verified','agent_audited'))
);
CREATE TABLE financial_fact_evidence(
    financial_fact_id TEXT NOT NULL REFERENCES financial_fact(financial_fact_id),
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(financial_fact_id,evidence_id)
);
CREATE INDEX overlay_fact_filing_account ON financial_fact(filing_id,account_id,account_name_raw);
CREATE TABLE event_fact(
    event_fact_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL,
    predicate_id TEXT NOT NULL,
    predicate_raw TEXT NOT NULL,
    answer_kind TEXT NOT NULL CHECK(answer_kind IN ('numeric','text')),
    value_raw TEXT NOT NULL,
    value_numeric TEXT,
    unit TEXT,
    scale INTEGER,
    extraction_method TEXT NOT NULL,
    trust_tier TEXT NOT NULL CHECK(trust_tier IN ('human_verified','agent_audited'))
);
CREATE TABLE event_fact_evidence(
    event_fact_id TEXT NOT NULL REFERENCES event_fact(event_fact_id),
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(event_fact_id,evidence_id)
);
CREATE TABLE build_reject(
    build_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    details_json TEXT NOT NULL,
    PRIMARY KEY(build_id,candidate_id,reason_code)
);
CREATE INDEX overlay_event_predicate ON event_fact(predicate_id,filing_id);
"""


@dataclass(slots=True)
class OverlayImportResult:
    imported: int = 0
    financial_imported: int = 0
    event_imported: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)
    source_database_sha256: str = ""
    seed_sha256: str = ""
    quick_check: str = ""


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


def overlay_matches_base(
    base_database: Path,
    overlay_database: Path,
    *,
    attestation: CorpusAttestation | None = None,
) -> bool:
    """Fail closed if an overlay was built from a different immutable base file."""
    if not Path(overlay_database).exists():
        return False
    try:
        base_stat, overlay_stat = Path(base_database).stat(), Path(overlay_database).stat()
        if attestation is not None:
            if not verify_fast_identity(Path(base_database), attestation):
                return False
            with closing(sqlite3.connect(str(Path(overlay_database)))) as connection:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(
                    "SELECT source_database_sha256 FROM overlay_revision WHERE overlay_revision=?",
                    ("semantic-v1-agent-overlay",),
                ).fetchone()
            return row is not None and str(row[0]).lower() == attestation.sha256
        return _overlay_matches_base_cached(
            str(Path(base_database).resolve()), str(Path(overlay_database).resolve()),
            int(base_stat.st_size), int(base_stat.st_mtime_ns), int(overlay_stat.st_size), int(overlay_stat.st_mtime_ns),
        )
    except (OSError, sqlite3.Error):
        return False


def _read_base(path: Path) -> sqlite3.Connection:
    # URI mode=ro is intentional: an importer must never mutate the immutable corpus.
    connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA busy_timeout=5000")
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
                  s.parse_status,s.detected_format,s.coverage_json,tr.parse_status AS table_parse_status,v.lineage_status
           FROM table_cell c
           JOIN table_record tr ON tr.table_id=c.table_id
           JOIN source_document s ON s.source_id=c.source_id
           JOIN filing_version v ON v.filing_id=c.filing_id
          WHERE c.evidence_id=? AND c.filing_id=?""",
        (evidence_id, filing_id),
    ).fetchone()


def _existing_overlay_source(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(path)) as connection:
            row = connection.execute(
                "SELECT source_database_sha256 FROM overlay_revision WHERE overlay_revision=?",
                ("semantic-v1-agent-overlay",),
            ).fetchone()
            return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def _write_overlay_atomically(
    base_database: Path,
    overlay_database: Path,
    source_sha: str,
    seed_sha: str,
    financial_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    rejects: list[tuple[str, str, dict[str, Any]]],
) -> tuple[int, str]:
    overlay_database.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{overlay_database.name}.", suffix=".tmp", dir=str(overlay_database.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    build_id = uuid4().hex
    try:
        with closing(sqlite3.connect(temporary)) as overlay:
            overlay.execute("PRAGMA foreign_keys=ON")
            overlay.executescript(OVERLAY_SCHEMA)
            columns = (
                "financial_fact_id", "filing_id", "account_id", "account_name_raw", "statement_type",
                "scope", "period_type", "period_start", "period_end", "instant_date", "value_numeric",
                "currency", "scale", "unit_raw", "extraction_method", "validation_status", "trust_tier",
            )
            overlay.executemany(
                f"INSERT INTO financial_fact({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                [tuple(row.get(column) for column in columns) for row in financial_rows],
            )
            for row in financial_rows:
                overlay.executemany(
                    "INSERT INTO financial_fact_evidence(financial_fact_id,evidence_id) VALUES(?,?)",
                    [(row["financial_fact_id"], evidence_id) for evidence_id in row["evidence_ids"]],
                )
            overlay.executemany(
                """INSERT INTO event_fact(
                    event_fact_id,filing_id,predicate_id,predicate_raw,answer_kind,value_raw,
                    value_numeric,unit,scale,extraction_method,trust_tier
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [(
                    row["event_fact_id"], row["filing_id"], row["predicate_id"], row["predicate_raw"],
                    row["answer_kind"], row["value_raw"], row.get("value_numeric"), row.get("unit"),
                    row.get("scale"), row["extraction_method"], "agent_audited",
                ) for row in event_rows],
            )
            for row in event_rows:
                overlay.executemany(
                    "INSERT INTO event_fact_evidence(event_fact_id,evidence_id) VALUES(?,?)",
                    [(row["event_fact_id"], evidence_id) for evidence_id in row["evidence_ids"]],
                )
            overlay.executemany(
                "INSERT INTO build_reject(build_id,candidate_id,reason_code,details_json) VALUES(?,?,?,?)",
                [(build_id, candidate_id, reason, json.dumps(details, ensure_ascii=False, sort_keys=True))
                 for candidate_id, reason, details in rejects],
            )
            overlay.execute(
                "INSERT INTO overlay_revision VALUES(?,?,datetime('now'),?)",
                ("semantic-v1-agent-overlay", source_sha, seed_sha),
            )
            overlay.commit()
            quick_check = str(overlay.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise ValueError(f"overlay quick_check failed: {quick_check}")
        os.replace(temporary, overlay_database)
        return len(financial_rows) + len(event_rows), quick_check
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_financial_seed(base: sqlite3.Connection, seed_path: Path, result: OverlayImportResult) -> list[dict[str, Any]]:
    validated_rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(seed_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            _validate_seed_row(base, row)
            row["trust_tier"] = "human_verified"
            validated_rows.append(row)
        except (ValueError, KeyError, TypeError, InvalidOperation, sqlite3.Error) as exc:
            result.rejected += 1
            result.reasons.append(f"line {line_no}: {exc}")
    return validated_rows


def import_seed(base_database: Path, overlay_database: Path, seed_path: Path) -> OverlayImportResult:
    base_database, overlay_database, seed_path = map(Path, (base_database, overlay_database, seed_path))
    source_sha = sha256_file(base_database)
    seed_sha = sha256_file(seed_path)
    existing_source = _existing_overlay_source(overlay_database)
    if existing_source is not None and existing_source != source_sha:
        raise ValueError("overlay source_database_sha256 does not match immutable base")
    result = OverlayImportResult(source_database_sha256=source_sha, seed_sha256=seed_sha)
    with closing(_read_base(base_database)) as base:
        financial_rows = _load_financial_seed(base, seed_path, result)
    _, result.quick_check = _write_overlay_atomically(
        base_database, overlay_database, source_sha, seed_sha, financial_rows, [], []
    )
    result.financial_imported = len(financial_rows)
    result.imported = result.financial_imported
    return result


def _load_predicate_allowlist(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    allowlist: dict[tuple[str, str], tuple[str, str]] = {}
    for predicate in payload.get("predicates", []):
        predicate_id = str(predicate["id"])
        answer_kind = str(predicate["answer_kind"])
        if answer_kind not in {"numeric", "text"}:
            continue
        for fact_type in predicate.get("allowed_fact_types", []):
            for predicate_raw in predicate.get("predicate_values", []):
                allowlist[(str(fact_type), str(predicate_raw))] = (predicate_id, answer_kind)
    return allowlist


def _event_candidates(base: sqlite3.Connection, allowlist: dict[tuple[str, str], tuple[str, str]]) -> list[sqlite3.Row]:
    if not allowlist:
        return []
    clauses = " OR ".join("(f.fact_type=? AND f.predicate=?)" for _ in allowlist)
    params = [value for pair in allowlist for value in pair]
    base.row_factory = sqlite3.Row
    return base.execute(
        f"""SELECT f.fact_id,f.filing_id,f.fact_type,f.subject,f.predicate,f.value_raw,f.unit,
                   f.extraction_method,f.validation_status,
                   MIN(CASE WHEN fe.evidence_id IS NULL THEN 0 ELSE 1 END) evidence_present,
                   MIN(CASE WHEN c.evidence_id IS NOT NULL AND c.filing_id=f.filing_id
                            AND tr.filing_id=c.filing_id AND s.filing_id=c.filing_id THEN 1 ELSE 0 END) same_filing,
                   MIN(CASE WHEN c.evidence_id IS NOT NULL AND s.parse_status='success'
                            AND tr.parse_status='success' THEN 1 ELSE 0 END) parse_success,
                   MAX(CASE WHEN s.detected_format='pdf' THEN 1 ELSE 0 END) has_pdf,
                   MAX(CASE WHEN v.lineage_status IN ('root','resolved') THEN 1 ELSE 0 END) lineage_safe,
                   GROUP_CONCAT(DISTINCT fe.evidence_id) evidence_ids
              FROM fact f
              LEFT JOIN fact_evidence fe ON fe.fact_id=f.fact_id
              LEFT JOIN table_cell c ON c.evidence_id=fe.evidence_id
              LEFT JOIN table_record tr ON tr.table_id=c.table_id
              LEFT JOIN source_document s ON s.source_id=c.source_id
              LEFT JOIN filing_version v ON v.filing_id=f.filing_id
             WHERE {clauses}
             GROUP BY f.fact_id
             ORDER BY f.fact_id""",
        params,
    ).fetchall()


def _clear_unit(unit: object) -> bool:
    normalized = str(unit or "").strip().casefold()
    return bool(normalized) and normalized not in {"unknown", "ambiguous", "n/a", "na", "none", "null", "-"} and not any(
        marker in normalized for marker in ("/", "|", ",", ";", "·", " 또는 ", " or ")
    )


def _decimal_cell(text: str) -> Decimal | None:
    compact = text.strip()
    if not _DECIMAL_CELL.fullmatch(compact):
        return None
    try:
        value = Decimal(compact.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _compact_label(value: object) -> str:
    return re.sub(r"[\s\u00a0]+", "", str(value or "")).casefold()


def _json_labels(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _header_matches_predicate(text: str, predicate_values: Iterable[str]) -> bool:
    compact = _compact_label(text)
    return any(_compact_label(predicate) and _compact_label(predicate) in compact for predicate in predicate_values)


def _predicate_suffix_units(header_texts: Iterable[str], predicate_values: Iterable[str]) -> set[str]:
    units: set[str] = set()
    for header in header_texts:
        if not _header_matches_predicate(header, predicate_values):
            continue
        match = re.search(r"\(([^()]+)\)\s*$", header.strip())
        if match and _clear_unit(match.group(1)):
            units.add(match.group(1).strip())
    return units


def _resolve_numeric_event_cell(
    base: sqlite3.Connection,
    fact_id: str,
    predicate_values: Iterable[str],
) -> dict[str, object]:
    """Resolve a composite event value to one labelled numeric table cell."""
    base.row_factory = sqlite3.Row
    linked = base.execute(
        """SELECT f.unit,fe.evidence_id,c.table_id,c.row_index
             FROM fact f
             JOIN fact_evidence fe ON fe.fact_id=f.fact_id
             JOIN table_cell c ON c.evidence_id=fe.evidence_id
            WHERE f.fact_id=?
            ORDER BY c.table_id,c.row_index,c.column_index,fe.evidence_id""",
        (fact_id,),
    ).fetchall()
    if not linked:
        raise ValueError("event_numeric_cell_missing")

    table_ids = sorted({str(row["table_id"]) for row in linked})
    placeholders = ",".join("?" for _ in table_ids)
    cells = base.execute(
        f"""SELECT c.evidence_id,c.table_id,c.row_index,c.column_index,c.cell_kind,
                         c.text_normalized,c.row_header_path_json,c.column_header_path_json,
                         t.unit_text
                    FROM table_cell c
                    JOIN table_record t ON t.table_id=c.table_id
                   WHERE c.table_id IN ({placeholders})
                   ORDER BY c.table_id,c.row_index,c.column_index,c.evidence_id""",
        table_ids,
    ).fetchall()
    predicate_values = list(predicate_values)
    labels = [str(cell["text_normalized"] or "").strip() for cell in cells]
    matching_labels = [label for label in labels if _header_matches_predicate(label, predicate_values)]
    header_match = bool(matching_labels)
    row_keys = {(str(row["table_id"]), int(row["row_index"])) for row in linked}
    numeric_cells: list[tuple[sqlite3.Row, Decimal]] = []
    for cell in cells:
        key = (str(cell["table_id"]), int(cell["row_index"]))
        if key not in row_keys or str(cell["cell_kind"]) != "data":
            continue
        labels = _json_labels(cell["row_header_path_json"]) + _json_labels(cell["column_header_path_json"])
        cell_matches = header_match or any(_header_matches_predicate(label, predicate_values) for label in labels)
        if not cell_matches:
            continue
        numeric = _decimal_cell(str(cell["text_normalized"] or ""))
        if numeric is not None:
            numeric_cells.append((cell, numeric))
    if not numeric_cells:
        raise ValueError("event_numeric_cell_missing")
    if len(numeric_cells) != 1:
        raise ValueError("event_numeric_cell_ambiguous")

    cell, numeric = numeric_cells[0]
    candidate_units = {str(row["unit"]).strip() for row in linked if row["unit"] is not None and str(row["unit"]).strip()}
    if candidate_units:
        if len(candidate_units) != 1 or not all(_clear_unit(unit) for unit in candidate_units):
            raise ValueError("event_numeric_unit_ambiguous")
        unit = next(iter(candidate_units))
    else:
        suffix_units = _predicate_suffix_units(matching_labels, predicate_values)
        table_units = {str(cell["unit_text"]).strip() for cell in cells if cell["unit_text"] is not None and str(cell["unit_text"]).strip()}
        unit_candidates = suffix_units or table_units
        if len(unit_candidates) != 1 or not all(_clear_unit(item) for item in unit_candidates):
            raise ValueError("event_numeric_unit_missing" if not unit_candidates else "event_numeric_unit_ambiguous")
        unit = next(iter(unit_candidates))
    return {
        "value_raw": str(cell["text_normalized"] or "").strip(),
        "value_numeric": format(numeric, "f"),
        "unit": unit,
        "scale": 1,
        "evidence_ids": [str(cell["evidence_id"])],
    }


def _validate_event_candidate(
    base: sqlite3.Connection,
    row: sqlite3.Row,
    predicate_id: str,
    answer_kind: str,
    predicate_values: Iterable[str],
) -> dict[str, Any]:
    candidate_id = str(row["fact_id"])
    if str(row["validation_status"] or "") != "candidate":
        raise ValueError("event_validation_status_invalid")
    if not row["evidence_present"]:
        raise ValueError("event_evidence_missing")
    if not row["same_filing"]:
        raise ValueError("event_cross_filing_evidence")
    if not row["lineage_safe"]:
        raise ValueError("event_unsafe_lineage")
    if not row["parse_success"]:
        raise ValueError("event_parse_failure")
    if row["has_pdf"]:
        raise ValueError("event_pdf_evidence")
    value_raw = str(row["value_raw"] or "").strip()
    if not value_raw:
        raise ValueError("event_value_missing")
    unit = str(row["unit"]).strip() if row["unit"] is not None else None
    value_numeric: str | None = None
    scale: int | None = None
    if answer_kind == "numeric":
        try:
            numeric = Decimal(value_raw.replace(",", ""))
        except InvalidOperation:
            if "|" not in value_raw:
                raise ValueError("event_numeric_not_decimal")
            resolved = _resolve_numeric_event_cell(base, candidate_id, predicate_values)
            value_raw = str(resolved["value_raw"])
            value_numeric = str(resolved["value_numeric"])
            unit = str(resolved["unit"])
            scale = int(resolved["scale"])
            evidence_ids = list(resolved["evidence_ids"])
        else:
            if not numeric.is_finite():
                raise ValueError("event_numeric_not_finite")
            if not _clear_unit(unit):
                raise ValueError("event_numeric_unit_missing" if not str(unit or "").strip() else "event_numeric_unit_ambiguous")
            value_numeric = format(numeric, "f")
            scale = 1
            evidence_ids = sorted({value for value in str(row["evidence_ids"] or "").split(",") if value})
    else:
        linked_cells = base.execute(
            """SELECT fe.evidence_id,c.cell_kind,c.text_normalized
                 FROM fact_evidence fe
                 JOIN table_cell c ON c.evidence_id=fe.evidence_id
                WHERE fe.fact_id=?
                ORDER BY c.table_id,c.row_index,c.column_index,fe.evidence_id""",
            (candidate_id,),
        ).fetchall()
        value_evidence_ids = [
            str(cell[0]) for cell in linked_cells
            if str(cell[1]) == "data" and str(cell[2] or "").strip() == value_raw
        ]
        if not value_evidence_ids:
            value_cells = [
                cell for cell in linked_cells
                if str(cell[1]) == "data"
                and not _header_matches_predicate(str(cell[2] or "").strip(), predicate_values)
            ]
            if len(value_cells) == 1:
                value_evidence_ids = [str(value_cells[0][0])]
        if value_evidence_ids:
            value_raw = next(
                str(cell[2] or "").strip()
                for cell in linked_cells
                if str(cell[0]) == value_evidence_ids[0]
            )
        evidence_ids = value_evidence_ids or sorted({value for value in str(row["evidence_ids"] or "").split(",") if value})
    return {
        "event_fact_id": candidate_id,
        "filing_id": str(row["filing_id"]),
        "predicate_id": predicate_id,
        "predicate_raw": str(row["predicate"]),
        "answer_kind": answer_kind,
        "value_raw": value_raw,
        "value_numeric": value_numeric,
        "unit": unit,
        "scale": scale,
        "extraction_method": str(row["extraction_method"] or "unknown"),
        "evidence_ids": evidence_ids,
    }


def build_agent_overlay(
    base_database: Path,
    overlay_database: Path,
    financial_seed: Path,
    predicate_config: Path,
    *,
    attestation: CorpusAttestation | None = None,
) -> OverlayImportResult:
    base_database, overlay_database = Path(base_database), Path(overlay_database)
    financial_seed, predicate_config = Path(financial_seed), Path(predicate_config)
    if attestation is not None:
        if not verify_fast_identity(base_database, attestation):
            raise ValueError("database does not match corpus attestation")
        source_sha = attestation.sha256
    else:
        source_sha = sha256_file(base_database)
    seed_sha = sha256_file(financial_seed)
    existing_source = _existing_overlay_source(overlay_database)
    if existing_source is not None and existing_source != source_sha:
        raise ValueError("overlay source_database_sha256 does not match immutable base")
    result = OverlayImportResult(source_database_sha256=source_sha, seed_sha256=seed_sha)
    rejects: list[tuple[str, str, dict[str, Any]]] = []
    with closing(_read_base(base_database)) as base:
        financial_rows = _load_financial_seed(base, financial_seed, result)
        allowlist = _load_predicate_allowlist(predicate_config)
        candidates: list[dict[str, Any]] = []
        for row in _event_candidates(base, allowlist):
            predicate_id, answer_kind = allowlist[(str(row["fact_type"]), str(row["predicate"]))]
            predicate_values = [
                raw for (fact_type, raw), (configured_id, _) in allowlist.items()
                if fact_type == str(row["fact_type"]) and configured_id == predicate_id
            ]
            try:
                candidates.append(_validate_event_candidate(base, row, predicate_id, answer_kind, predicate_values))
            except ValueError as exc:
                reason = str(exc)
                rejects.append((str(row["fact_id"]), reason, {"predicate": row["predicate"], "value_raw": row["value_raw"], "unit": row["unit"]}))
                result.reasons.append(f"{row['fact_id']}: {reason}")
                result.rejected += 1
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in candidates:
            grouped.setdefault((row["filing_id"], row["predicate_id"]), []).append(row)
        event_rows: list[dict[str, Any]] = []
        for key, group in grouped.items():
            if len({(row["value_raw"], row.get("unit")) for row in group}) > 1:
                for row in group:
                    rejects.append((row["event_fact_id"], "event_fact_conflict", {"filing_id": key[0], "predicate_id": key[1]}))
                    result.reasons.append(f"{row['event_fact_id']}: event_fact_conflict")
                    result.rejected += 1
                continue
            event_rows.extend(group)
    _, result.quick_check = _write_overlay_atomically(
        base_database, overlay_database, source_sha, seed_sha, financial_rows, event_rows, rejects
    )
    result.financial_imported = len(financial_rows)
    result.event_imported = len(event_rows)
    result.imported = result.financial_imported + result.event_imported
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
        # The overlay is restricted to textual table cells. A PDF table is blocked;
        # an XML source may contain unrelated image references, which do not make this
        # specific textual cell visual evidence.
        if evidence["detected_format"] == "pdf":
            raise ValueError(f"visual evidence is blocked for overlay facts: {evidence_id}")
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
    period_start: str | None = None,
    period_end: str | None = None,
    period_end_lte: str | None = None,
    instant_date: str | None = None,
    limit: int = 100,
    attestation: CorpusAttestation | None = None,
) -> list[dict[str, object]]:
    if limit <= 0 or not Path(overlay_database).exists():
        return []
    if not overlay_matches_base(Path(base_database), Path(overlay_database), attestation=attestation):
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
    if period_start is not None:
        where.append("ff.period_start=?")
        params.append(period_start)
    if period_end is not None:
        where.append("ff.period_end=?")
        params.append(period_end)
    if period_end_lte is not None:
        where.append("ff.period_end<=?")
        params.append(period_end_lte)
    if instant_date is not None:
        where.append("ff.instant_date=?")
        params.append(instant_date)
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
                       v.lineage_status,v.is_current,s.detected_format,s.coverage_json,
                       group_concat(DISTINCT ffe.evidence_id) evidence_ids,
                       json_group_array(DISTINCT c.text_raw) evidence_texts
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
        if str(item.get("detected_format") or "") == "pdf":
            continue
        item["evidence_ids"] = sorted(str(item.get("evidence_ids") or "").split(",")) if item.get("evidence_ids") else []
        try:
            item["evidence_texts"] = json.loads(str(item.get("evidence_texts") or "[]"))
        except json.JSONDecodeError:
            item["evidence_texts"] = []
        result.append(item)
    return result


def fetch_event_facts(
    base_database: Path,
    overlay_database: Path,
    *,
    company: str | None = None,
    predicate_terms: Iterable[str] = (),
    as_of: str | None = None,
    limit: int = 100,
    correction_policy: str = "current",
    attestation: CorpusAttestation | None = None,
) -> list[dict[str, object]]:
    if limit <= 0 or not Path(overlay_database).exists():
        return []
    if not overlay_matches_base(Path(base_database), Path(overlay_database), attestation=attestation):
        return []
    version_sql, version_params = version_filter_sql(alias="v", as_of=as_of)
    if correction_policy == "original":
        version_sql = "v.lineage_status='root'"
        version_params = []
        if as_of is not None:
            version_sql += " AND v.effective_from<=? AND (v.effective_to IS NULL OR ? < v.effective_to)"
            version_params = [as_of, as_of]
    elif correction_policy == "both":
        version_sql = "v.lineage_status IN ('root','resolved')"
        version_params = []
        if as_of is not None:
            version_sql += " AND v.effective_from<=?"
            version_params = [as_of]
    elif correction_policy == "corrected":
        version_sql += " AND f.is_correction=1"
    where = [
        "c.filing_id=ef.filing_id", "s.filing_id=c.filing_id",
        "s.parse_status='success'", "s.detected_format<>'pdf'", version_sql,
    ]
    params: list[object] = list(version_params)
    with closing(_read_base(Path(base_database))) as schema_connection:
        filing_columns = {str(row[1]) for row in schema_connection.execute("PRAGMA table_info(filing)")}
    reporter_select = "f.reporter_name" if "reporter_name" in filing_columns else "NULL AS reporter_name"
    if company is not None:
        company_fields = ["f.issuer_name=?", "f.listed_name=?", "f.stock_code=?", "f.issuer_corp_code=?"]
        if "reporter_name" in filing_columns:
            company_fields.insert(2, "f.reporter_name=?")
        where.append(f"({' OR '.join(company_fields)})")
        params.extend([company] * len(company_fields))
    terms = [str(term) for term in predicate_terms if str(term)]
    if terms:
        where.append("(" + " OR ".join(["ef.predicate_id LIKE ? OR ef.predicate_raw LIKE ?"] * len(terms)) + ")")
        for term in terms:
            params.extend([f"%{term}%", f"%{term}%"])
    params.append(limit)
    with closing(_read_base(Path(base_database))) as connection:
        connection.execute("PRAGMA query_only=ON")
        try:
            connection.execute("ATTACH DATABASE ? AS overlay", (str(Path(overlay_database).resolve()),))
            rows = connection.execute(
                f"""SELECT ef.*,f.issuer_name,{reporter_select},f.report_name_raw,f.filed_at,
                           v.event_id,v.effective_from,v.effective_to,v.lineage_status,v.is_current,
                           group_concat(DISTINCT efe.evidence_id) evidence_ids,
                           json_group_array(DISTINCT c.text_raw) evidence_texts
                      FROM overlay.event_fact ef
                      JOIN main.filing f ON f.filing_id=ef.filing_id
                      JOIN main.filing_version v ON v.filing_id=ef.filing_id
                      JOIN overlay.event_fact_evidence efe ON efe.event_fact_id=ef.event_fact_id
                      JOIN main.table_cell c ON c.evidence_id=efe.evidence_id
                      JOIN main.source_document s ON s.source_id=c.source_id
                     WHERE {' AND '.join(where)}
                     GROUP BY ef.event_fact_id ORDER BY ef.event_fact_id LIMIT ?""",
                params,
            ).fetchall()
        except sqlite3.Error:
            # Old or partially written overlays fail closed for the new event surface.
            return []
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["evidence_ids"] = sorted(str(item.get("evidence_ids") or "").split(",")) if item.get("evidence_ids") else []
        try:
            item["evidence_texts"] = json.loads(str(item.get("evidence_texts") or "[]"))
        except json.JSONDecodeError:
            item["evidence_texts"] = []
        result.append(item)
    return result
