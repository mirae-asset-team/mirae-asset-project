"""Build deterministic, evidence-backed agent-audited Gold candidates."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing, nullcontext
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence


def load_predicate_config(path: Path) -> dict[str, Any]:
    """Load and validate the explicit predicate allowlist."""
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != "0.1.0":
        raise ValueError("agent Gold predicate config must declare version 0.1.0")
    predicates = config.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        raise ValueError("agent Gold predicate config requires predicates")
    seen_ids: set[str] = set()
    seen_values: set[str] = set()
    for item in predicates:
        if not isinstance(item, dict):
            raise ValueError("predicate entries must be objects")
        required = {"id", "question_type", "answer_kind", "allowed_fact_types", "predicate_values", "required_evidence_fields"}
        if required - set(item):
            raise ValueError(f"predicate missing fields: {sorted(required - set(item))}")
        predicate_id = str(item["id"])
        values = item["predicate_values"]
        if not predicate_id or predicate_id in seen_ids or not isinstance(values, list) or not values:
            raise ValueError("predicate ids must be unique and predicate_values must be non-empty")
        if any(not isinstance(value, str) or not value or value == "*" for value in values):
            raise ValueError("predicate_values must contain explicit non-empty strings")
        if not isinstance(item["allowed_fact_types"], list) or not item["allowed_fact_types"]:
            raise ValueError(f"predicate {predicate_id} requires allowed_fact_types")
        if not isinstance(item["required_evidence_fields"], list):
            raise ValueError(f"predicate {predicate_id} requires required_evidence_fields")
        seen_ids.add(predicate_id)
        seen_values.update(values)
    templates = config.get("templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("agent Gold predicate config requires templates")
    if any(not isinstance(item, dict) or not item.get("id") for item in templates):
        raise ValueError("templates must have ids")
    return config


def _readonly_connection(path: Path) -> sqlite3.Connection:
    """Open the immutable semantic corpus without creating or mutating a file."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_format(extension: str | None, detected_format: str | None) -> str:
    marker = f"{extension or ''} {detected_format or ''}".casefold()
    if "pdf" in marker:
        return "pdf"
    if "html" in marker or "htm" in marker:
        return "html"
    if "xml" in marker:
        return "xml"
    return "other"


def extract_existing_gold(gold_path: Path) -> list[dict[str, Any]]:
    """Load existing records without changing their human review metadata."""
    records: list[dict[str, Any]] = []
    for line in gold_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("Gold JSONL records must be objects")
        records.append(dict(record))
    return records


def _predicate_maps(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, set[str]]]:
    value_to_id: dict[str, str] = {}
    id_to_types: dict[str, set[str]] = {}
    for item in config["predicates"]:
        predicate_id = str(item["id"])
        id_to_types[predicate_id] = {str(value) for value in item["allowed_fact_types"]}
        for value in item["predicate_values"]:
            value_to_id[str(value)] = predicate_id
    return value_to_id, id_to_types


def extract_fact_candidates(
    base: Path, config: dict[str, Any], limit: int | None = None
) -> list[dict[str, Any]]:
    """Extract explicitly allowlisted generic facts through read-only joins."""
    value_to_id, id_to_types = _predicate_maps(config)
    predicates = sorted(value_to_id)
    if not predicates:
        return []
    placeholders = ",".join("?" for _ in predicates)
    sql = f"""
        SELECT f.fact_id, f.filing_id, f.fact_type, f.subject, f.predicate,
               f.value_raw, f.unit, f.validation_status,
               fe.evidence_id, tc.filing_id AS evidence_filing_id,
               tc.table_id, tc.source_id, tc.text_raw AS evidence_text,
               tc.locator_json AS cell_locator_json,
               tr.parse_status AS table_parse_status,
               tr.locator_json AS table_locator_json,
               sd.sha256 AS source_sha256, sd.extension, sd.detected_format,
               sd.parse_status AS source_parse_status,
               (SELECT count(*) FROM fragment AS fr WHERE fr.source_id = sd.source_id) AS fragment_count,
               (SELECT count(*) FROM table_record AS tr2 WHERE tr2.source_id = sd.source_id) AS table_count,
               (SELECT count(*) FROM table_cell AS tc2 WHERE tc2.source_id = sd.source_id) AS cell_count,
               fv.lineage_status, fv.lineage_confidence, fv.is_current,
               fv.event_id, fv.parent_filing_id, fv.effective_from, fv.effective_to,
               fv.rationale, f.filing_id AS fact_filing_id,
               fi.issuer_name, fi.issuer_corp_code, fi.stock_code, fi.filed_at
        FROM fact AS f
        JOIN fact_evidence AS fe ON fe.fact_id = f.fact_id
        JOIN table_cell AS tc ON tc.evidence_id = fe.evidence_id
        JOIN table_record AS tr ON tr.table_id = tc.table_id
        JOIN source_document AS sd ON sd.source_id = tc.source_id
        JOIN filing_version AS fv ON fv.filing_id = f.filing_id
        JOIN filing AS fi ON fi.filing_id = f.filing_id
        WHERE f.validation_status = 'candidate'
          AND f.predicate IN ({placeholders})
        ORDER BY f.fact_id, fe.evidence_id
    """
    rows: list[dict[str, Any]] = []
    with closing(_readonly_connection(base)) as connection:
        for row in connection.execute(sql, predicates):
            predicate_id = value_to_id.get(str(row["predicate"]))
            if predicate_id is None or str(row["fact_type"]) not in id_to_types[predicate_id]:
                continue
            candidate = dict(row)
            candidate.update(
                {
                    "_candidate_source": "fact",
                    "_predicate_id": predicate_id,
                    "source_format": _source_format(row["extension"], row["detected_format"]),
                    "cell_locator": _json_object(row["cell_locator_json"]),
                    "table_locator": _json_object(row["table_locator_json"]),
                    "table_structure_status": "parsed_unreviewed",
                }
            )
            rows.append(candidate)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def extract_overlay_candidates(seed_path: Path) -> list[dict[str, Any]]:
    """Load validated financial overlay seed rows as immutable candidates."""
    rows: list[dict[str, Any]] = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("overlay seed JSONL records must be objects")
        candidate = dict(row)
        candidate["_candidate_source"] = "overlay"
        rows.append(candidate)
    return rows


@dataclass(frozen=True)
class AuditResult:
    status: str
    record: dict[str, Any] | None
    reason_codes: list[str]


def write_jsonl_atomic(path: Path, rows: Any) -> None:
    """Write stable JSONL through a sibling temporary path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _record_evidence_row(connection: sqlite3.Connection, evidence_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT tc.evidence_id, tc.filing_id, tc.source_id, tc.table_id,
               tc.locator_json, tc.row_index, tc.column_index,
               tr.parse_status AS table_parse_status,
               sd.sha256 AS source_sha256, sd.extension, sd.detected_format,
               sd.parse_status AS source_parse_status,
               fv.lineage_status, fv.lineage_confidence, fv.is_current,
               fv.event_id, fv.parent_filing_id, fv.effective_from, fv.effective_to,
               fv.rationale
        FROM table_cell AS tc
        JOIN table_record AS tr ON tr.table_id = tc.table_id
        JOIN source_document AS sd ON sd.source_id = tc.source_id
        JOIN filing_version AS fv ON fv.filing_id = tc.filing_id
        WHERE tc.evidence_id = ?
        UNION ALL
        SELECT fr.evidence_id, fr.filing_id, fr.source_id, fr.table_id,
               fr.locator_json, fr.sequence_no AS row_index, NULL AS column_index,
               NULL AS table_parse_status,
               sd.sha256 AS source_sha256, sd.extension, sd.detected_format,
               sd.parse_status AS source_parse_status,
               fv.lineage_status, fv.lineage_confidence, fv.is_current,
               fv.event_id, fv.parent_filing_id, fv.effective_from, fv.effective_to,
               fv.rationale
        FROM fragment AS fr
        JOIN source_document AS sd ON sd.source_id = fr.source_id
        JOIN filing_version AS fv ON fv.filing_id = fr.filing_id
        WHERE fr.evidence_id = ?
        LIMIT 1
        """,
        (evidence_id, evidence_id),
    ).fetchone()


def _record_source_row(connection: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT s.source_id, s.filing_id, s.sha256, s.detected_format, s.parse_status,
               (SELECT count(*) FROM fragment fr WHERE fr.source_id=s.source_id),
               (SELECT count(*) FROM table_record tr WHERE tr.source_id=s.source_id),
               (SELECT count(*) FROM table_cell tc WHERE tc.source_id=s.source_id)
        FROM source_document AS s WHERE s.source_id=?
        """,
        (source_id,),
    ).fetchone()


def _chunks(values: Iterable[str], size: int = 800) -> Iterable[list[str]]:
    batch: list[str] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _build_audit_cache(
    connection: sqlite3.Connection, evidence_ids: Iterable[str], source_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Hydrate evidence/source metadata in bounded batches to avoid per-row disk seeks."""
    evidence_cache: dict[str, Any] = {}
    for batch in _chunks(sorted(set(evidence_ids))):
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""
            SELECT tc.evidence_id, tc.filing_id, tc.source_id, tc.table_id,
                   tc.locator_json, tc.row_index, tc.column_index,
                   tr.parse_status AS table_parse_status,
                   sd.sha256 AS source_sha256, sd.extension, sd.detected_format,
                   sd.parse_status AS source_parse_status,
                   fv.lineage_status, fv.lineage_confidence, fv.is_current,
                   fv.event_id, fv.parent_filing_id, fv.effective_from, fv.effective_to,
                   fv.rationale
            FROM table_cell AS tc
            JOIN table_record AS tr ON tr.table_id = tc.table_id
            JOIN source_document AS sd ON sd.source_id = tc.source_id
            JOIN filing_version AS fv ON fv.filing_id = tc.filing_id
            WHERE tc.evidence_id IN ({placeholders})
            UNION ALL
            SELECT fr.evidence_id, fr.filing_id, fr.source_id, fr.table_id,
                   fr.locator_json, fr.sequence_no AS row_index, NULL AS column_index,
                   NULL AS table_parse_status,
                   sd.sha256 AS source_sha256, sd.extension, sd.detected_format,
                   sd.parse_status AS source_parse_status,
                   fv.lineage_status, fv.lineage_confidence, fv.is_current,
                   fv.event_id, fv.parent_filing_id, fv.effective_from, fv.effective_to,
                   fv.rationale
            FROM fragment AS fr
            JOIN source_document AS sd ON sd.source_id = fr.source_id
            JOIN filing_version AS fv ON fv.filing_id = fr.filing_id
            WHERE fr.evidence_id IN ({placeholders})
            """,
            batch + batch,
        )
        for row in rows:
            evidence_cache[str(row["evidence_id"])] = row
    source_cache: dict[str, Any] = {}
    for batch in _chunks(sorted(set(source_ids))):
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""
            SELECT s.source_id, s.filing_id, s.sha256, s.detected_format, s.parse_status
            FROM source_document AS s WHERE s.source_id IN ({placeholders})
            """,
            batch,
        )
        for row in rows:
            source_cache[str(row["source_id"])] = {
                "source_id": str(row["source_id"]), "filing_id": str(row["filing_id"]),
                "sha256": str(row["sha256"]), "detected_format": str(row["detected_format"]),
                "parse_status": str(row["parse_status"]), "fragment_count": 0,
                "table_count": 0, "cell_count": 0,
            }
        for table_name, count_key in (("fragment", "fragment_count"), ("table_record", "table_count"), ("table_cell", "cell_count")):
            count_rows = connection.execute(
                f"SELECT source_id, count(*) AS count_value FROM {table_name} WHERE source_id IN ({placeholders}) GROUP BY source_id",
                batch,
            )
            for count_row in count_rows:
                source_cache.setdefault(str(count_row["source_id"]), {})[count_key] = int(count_row["count_value"])
    return {"evidence": evidence_cache, "source": source_cache}


def _numeric_reasons(record: dict[str, Any]) -> list[str]:
    answer = record.get("answer")
    if not isinstance(answer, dict):
        return []
    values: list[Any] = []
    if answer.get("kind") == "numeric":
        values.append(answer.get("value"))
    elif answer.get("kind") == "multi_numeric":
        values.extend(item.get("value") for item in answer.get("values", []) if isinstance(item, dict))
    reasons: list[str] = []
    for value in values:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            reasons.append("numeric_not_decimal")
            continue
        if not decimal_value.is_finite():
            reasons.append("numeric_not_decimal")
    return reasons


def _copy_without_internal_keys(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not str(key).startswith("_")}


def audit_candidate(
    candidate: dict[str, Any], base: Path, *, source_sha256: str, connection: sqlite3.Connection | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
) -> AuditResult:
    """Apply deterministic contract, evidence, lineage, and value gates."""
    record = _copy_without_internal_keys(candidate)
    reasons: set[str] = set()
    review = record.setdefault(
        "review",
        {"status": "candidate", "annotator": "deterministic_agent_gold_v1", "reviewer": None, "reviewed_at": None, "notes": ""},
    )
    if isinstance(review, dict) and review.get("status") not in {"candidate", "agent_audited"}:
        review["status"] = "candidate"
    if record.get("answer_origin") != "model_generated":
        reasons.add("schema_invalid")
    try:
        from disclosure_db.gold_validation import validate_record_contract

        if validate_record_contract(record):
            reasons.add("schema_invalid")
    except (KeyError, TypeError, ValueError):
        reasons.add("schema_invalid")

    evidence_ids: list[str] = []
    declared_filing_ids = {str(value) for value in record.get("candidate_filing_ids", [])}
    connection_scope = closing(_readonly_connection(base)) if connection is None else nullcontext(connection)
    with connection_scope as connection:
        for item in record.get("evidence", []):
            if not isinstance(item, dict):
                reasons.add("schema_invalid")
                continue
            evidence_id = str(item.get("evidence_id") or "")
            evidence_ids.append(evidence_id)
            actual = (cache or {}).get("evidence", {}).get(evidence_id) if evidence_id else None
            if actual is None and evidence_id:
                actual = _record_evidence_row(connection, evidence_id)
            if actual is None:
                reasons.add("evidence_not_found")
                continue
            if str(actual["filing_id"]) != str(item.get("filing_id")) or str(actual["filing_id"]) not in declared_filing_ids:
                reasons.add("cross_filing_evidence")
            if str(actual["source_sha256"]) != str(item.get("source_sha256")):
                reasons.add("citation_mismatch")
            if str(actual["source_parse_status"]) != "success":
                reasons.add("source_parse_failed")
            if actual["table_parse_status"] is not None and str(actual["table_parse_status"]) != "success":
                reasons.add("table_parse_failed")
            if _source_format(actual["extension"], actual["detected_format"]) == "pdf":
                reasons.add("visual_evidence_blocked")
            if str(actual["lineage_status"]) not in {"root", "resolved"}:
                reasons.add("lineage_not_answer_safe")
        for item in record.get("source_evidence", []):
            if not isinstance(item, dict):
                reasons.add("schema_invalid")
                continue
            source_id = str(item.get("source_id") or "")
            actual_source = (cache or {}).get("source", {}).get(source_id)
            if actual_source is None:
                actual_source = _record_source_row(connection, source_id)
            if actual_source is None:
                reasons.add("evidence_not_found")
                continue
            if str(actual_source["filing_id"]) != str(item.get("filing_id")):
                reasons.add("cross_filing_evidence")
            if str(actual_source["sha256"]) != str(item.get("sha256")):
                reasons.add("citation_mismatch")
            if str(actual_source["parse_status"]) != "success":
                reasons.add("source_parse_failed")
            if _source_format(None, actual_source["detected_format"]) == "pdf":
                reasons.add("visual_evidence_blocked")

    if len(evidence_ids) != len(set(evidence_ids)):
        reasons.add("duplicate_candidate")
    seen_keys = candidate.get("_seen_keys")
    if isinstance(seen_keys, set):
        key = (str(record.get("question_id")), tuple(sorted(evidence_ids)))
        if key in seen_keys:
            reasons.add("duplicate_candidate")
        else:
            seen_keys.add(key)
    reasons.update(_numeric_reasons(record))
    citation_ids = candidate.get("_runtime_citation_ids")
    if citation_ids is not None and not set(map(str, citation_ids)) <= set(evidence_ids):
        reasons.add("citation_mismatch")

    if reasons:
        return AuditResult(status="rejected", record=None, reason_codes=sorted(reasons))

    record["answer_origin"] = "model_generated"
    record["review"] = {
        "status": "agent_audited",
        "annotator": "deterministic_agent_gold_v1",
        "reviewer": None,
        "reviewed_at": None,
        "notes": "Deterministic schema, evidence, lineage, value, and citation gates passed; human promotion remains required.",
    }
    record["audit"] = {
        "state": "agent_audited",
        "generator": "deterministic_agent_gold_v1",
        "base_sha256": source_sha256,
        "gold_input_sha256": candidate.get("_gold_input_sha256"),
        "seed_input_sha256": candidate.get("_seed_input_sha256"),
    }
    return AuditResult(status="agent_audited", record=record, reason_codes=[])


EXPECTED_BASE_SHA256 = "b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563"
NUMERIC_VALUE_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _date(value: Any) -> str | None:
    text = str(value or "")
    return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text) else None


def _normalise_numeric(value: Any) -> str:
    text = str(value or "").strip().replace(",", "").replace("−", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1].strip()
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    return format(parsed, "f") if parsed.is_finite() else text


def _predicate_by_id(config: dict[str, Any], predicate_id: str) -> dict[str, Any]:
    for item in config["predicates"]:
        if item["id"] == predicate_id:
            return item
    raise KeyError(predicate_id)


def _version_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "filing_id": str(row["filing_id"]),
        "event_id": str(row["event_id"]),
        "parent_filing_id": row["parent_filing_id"],
        "lineage_status": str(row["lineage_status"]),
        "lineage_confidence": str(row["lineage_confidence"]),
        "is_current": bool(row["is_current"]),
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "rationale": str(row["rationale"]),
    }


def _source_payload(connection: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    row = _record_source_row(connection, source_id)
    if row is None:
        return None
    return {
        "source_id": str(row["source_id"]),
        "filing_id": str(row["filing_id"]),
        "sha256": str(row["sha256"]),
        "detected_format": str(row["detected_format"]),
        "parse_status": str(row["parse_status"]),
        "fragment_count": int(row[5]),
        "table_count": int(row[6]),
        "cell_count": int(row[7]),
        "table_structure_status": "parsed_unreviewed",
    }


def _evidence_payload(connection: sqlite3.Connection, evidence_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    row = _record_evidence_row(connection, evidence_id)
    if row is None:
        return None
    locator = _json_object(row["locator_json"])
    locator.setdefault("kind", "table_cell" if row["table_id"] else "fragment")
    evidence = {
        "evidence_id": str(row["evidence_id"]),
        "filing_id": str(row["filing_id"]),
        "source_sha256": str(row["source_sha256"]),
        "locator": locator,
        "role": "support",
        "lineage_status": str(row["lineage_status"]),
        "is_current": bool(row["is_current"]),
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "source_format": _source_format(row["extension"], row["detected_format"]),
        "table_structure_status": "parsed_unreviewed" if row["table_id"] else "not_applicable",
        "table_id": row["table_id"],
        "cell_evidence_id": str(row["evidence_id"]) if row["table_id"] else None,
        "unit": None,
        "scale": None,
    }
    version = _version_payload(row)
    source = _source_payload(connection, str(row["source_id"]))
    if source is None:
        return None
    return evidence, version, source


def _base_record(
    *, question_id: str, question_type: str, question: str, answer: dict[str, Any],
    filing_id: str, issuer_name: str, corp_code: str, stock_code: str,
    period: dict[str, Any], as_of: str | None, version_basis: str,
    evidence: list[dict[str, Any]], versions: list[dict[str, Any]], sources: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "question_id": question_id,
        "question_type": question_type,
        "question": question,
        "answerability": "answerable",
        "answer": answer,
        "answer_origin": "model_generated",
        "candidate_filing_ids": [filing_id],
        "company_resolution": {"query_name": issuer_name, "issuer_name": issuer_name, "corp_code": corp_code, "stock_code": stock_code},
        "period": period,
        "scope": "not_applicable",
        "as_of": as_of,
        "version_basis": version_basis,
        "formula": None,
        "attack_label": None,
        "evidence": evidence,
        "version_evidence": versions,
        "source_evidence": sources,
        "review": {"status": "candidate", "annotator": "deterministic_agent_gold_v1", "reviewer": None, "reviewed_at": None, "notes": ""},
    }


def _fact_record(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    predicate = _predicate_by_id(config, str(row["_predicate_id"]))
    evidence = {
        "evidence_id": str(row["evidence_id"]),
        "filing_id": str(row["filing_id"]),
        "source_sha256": str(row["source_sha256"]),
        "locator": {**row.get("cell_locator", {}), "kind": "table_cell", "table_id": str(row["table_id"])},
        "role": "support",
        "lineage_status": str(row["lineage_status"]),
        "is_current": bool(row["is_current"]),
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "source_format": str(row["source_format"]),
        "table_structure_status": str(row["table_structure_status"]),
        "table_id": str(row["table_id"]),
        "cell_evidence_id": str(row["evidence_id"]),
        "unit": row["unit"],
        "scale": 1,
    }
    answer: dict[str, Any]
    if predicate["answer_kind"] == "numeric":
        answer = {"kind": "numeric", "value": _normalise_numeric(row["value_raw"]), "unit": str(row["unit"] or "원"), "scale": 1}
    else:
        answer = {"kind": "text", "text": str(row["value_raw"]).strip()}
    as_of = _date(row["filed_at"])
    question = str(predicate["question_template"]).format(company=row["issuer_name"], as_of=as_of or "해당일")
    source = {
        "source_id": str(row["source_id"]), "filing_id": str(row["filing_id"]), "sha256": str(row["source_sha256"]),
        "detected_format": str(row["detected_format"]), "parse_status": str(row["source_parse_status"]),
        "fragment_count": int(row["fragment_count"]), "table_count": int(row["table_count"]), "cell_count": int(row["cell_count"]),
        "table_structure_status": "parsed_unreviewed",
    }
    version = {
        "filing_id": str(row["filing_id"]), "event_id": str(row["event_id"]), "parent_filing_id": row["parent_filing_id"],
        "lineage_status": str(row["lineage_status"]), "lineage_confidence": str(row["lineage_confidence"]),
        "is_current": bool(row["is_current"]), "effective_from": row["effective_from"], "effective_to": row["effective_to"], "rationale": str(row["rationale"]),
    }
    return _base_record(
        question_id=f"agent_{row['_predicate_id']}_{row['fact_id']}_{row['evidence_id']}", question_type=str(predicate["question_type"]),
        question=question, answer=answer, filing_id=str(row["filing_id"]), issuer_name=str(row["issuer_name"]),
        corp_code=str(row["issuer_corp_code"]), stock_code=str(row["stock_code"]),
        period={"period_type": "event_date", "start_date": None, "end_date": None, "instant_date": as_of},
        as_of=as_of, version_basis="as_of", evidence=[evidence], versions=[version], sources=[source],
    )


def _overlay_records(base: Path, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with closing(_readonly_connection(base)) as connection:
        for row in rows:
            filing_id = str(row["filing_id"])
            filing = connection.execute("SELECT issuer_name,issuer_corp_code,stock_code FROM filing WHERE filing_id=?", (filing_id,)).fetchone()
            if filing is None:
                continue
            evidence: list[dict[str, Any]] = []
            versions: list[dict[str, Any]] = []
            sources: dict[str, dict[str, Any]] = {}
            for evidence_id in row.get("evidence_ids", []):
                payload = _evidence_payload(connection, str(evidence_id))
                if payload is None:
                    continue
                item, version, source = payload
                item["unit"] = row.get("unit_raw")
                item["scale"] = int(row.get("scale") or 1)
                evidence.append(item)
                versions.append(version)
                sources[str(source["source_id"])] = source
            period_type = str(row.get("period_type") or "unknown")
            if period_type == "duration":
                period = {"period_type": "duration", "start_date": row.get("period_start"), "end_date": row.get("period_end"), "instant_date": None}
            elif period_type == "instant":
                period = {"period_type": "instant", "start_date": None, "end_date": None, "instant_date": row.get("instant_date")}
            else:
                period = {"period_type": "not_applicable", "start_date": None, "end_date": None, "instant_date": None}
            account = str(row.get("account_name_raw") or row.get("account_id") or "재무 수치")
            end = row.get("period_end") or row.get("instant_date") or ""
            record = _base_record(
                question_id=f"agent_financial_{row['financial_fact_id']}", question_type="table_cell",
                question=f"{filing['issuer_name']}의 {end} 연결 {account}은 얼마인가?",
                answer={"kind": "numeric", "value": _normalise_numeric(row.get("value_numeric")), "unit": str(row.get("unit_raw") or row.get("currency") or "원"), "scale": int(row.get("scale") or 1)},
                filing_id=filing_id, issuer_name=str(filing["issuer_name"]), corp_code=str(filing["issuer_corp_code"]), stock_code=str(filing["stock_code"]),
                period=period, as_of=None, version_basis="not_applicable", evidence=evidence, versions=versions, sources=list(sources.values()),
            )
            record["_candidate_source"] = "overlay"
            record["_financial_fact_id"] = row.get("financial_fact_id")
            records.append(record)
    return records


def _reject_row(candidate: dict[str, Any], reasons: list[str], *, base_sha256: str, gold_hash: str, seed_hash: str) -> dict[str, Any]:
    return {
        "candidate_id": str(candidate.get("question_id") or candidate.get("fact_id") or candidate.get("financial_fact_id") or "unknown"),
        "candidate_source": str(candidate.get("_candidate_source") or "unknown"),
        "question_id": candidate.get("question_id"),
        "filing_id": candidate.get("filing_id") or (candidate.get("candidate_filing_ids") or [None])[0],
        "evidence_ids": [item.get("evidence_id") for item in candidate.get("evidence", []) if isinstance(item, dict)],
        "reason_codes": sorted(set(reasons)),
        "base_sha256": base_sha256,
        "gold_input_sha256": gold_hash,
        "seed_input_sha256": seed_hash,
    }


def generate_gold(
    *, database: Path, gold_path: Path, overlay_seed: Path, output: Path, summary: Path, rejects: Path,
    predicate_config: Path = Path("config/agent_gold_predicates.json"), expected_base_sha256: str = EXPECTED_BASE_SHA256,
) -> dict[str, Any]:
    config = load_predicate_config(predicate_config)
    base_hash = _sha256(database)
    gold_hash = _sha256(gold_path)
    seed_hash = _sha256(overlay_seed)
    existing = extract_existing_gold(gold_path)
    output_rows: list[dict[str, Any]] = []
    for record in existing:
        copied = dict(record)
        copied["audit"] = {
            "state": "human_verified" if copied.get("answer_origin") == "human_verified" else "candidate",
            "generator": "existing_gold_passthrough",
            "base_sha256": base_hash,
            "gold_input_sha256": gold_hash,
            "seed_input_sha256": seed_hash,
        }
        output_rows.append(copied)

    rejected_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()
    generated_candidates: list[dict[str, Any]] = []
    base_valid = not expected_base_sha256 or base_hash == expected_base_sha256
    if base_valid:
        for row in extract_fact_candidates(database, config):
            record = _fact_record(row, config)
            record["_candidate_source"] = "fact"
            record["_seen_keys"] = seen_keys
            generated_candidates.append(record)
        generated_candidates.extend(_overlay_records(database, extract_overlay_candidates(overlay_seed)))

    with closing(_readonly_connection(database)) as audit_connection:
        all_evidence_ids = [
            str(item.get("evidence_id"))
            for candidate in generated_candidates
            for item in candidate.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        ]
        all_source_ids = [
            str(item.get("source_id"))
            for candidate in generated_candidates
            for item in candidate.get("source_evidence", [])
            if isinstance(item, dict) and item.get("source_id")
        ]
        audit_cache = _build_audit_cache(audit_connection, all_evidence_ids, all_source_ids)
        for candidate in generated_candidates:
            candidate["_gold_input_sha256"] = gold_hash
            candidate["_seed_input_sha256"] = seed_hash
            if candidate.get("_candidate_source") == "overlay":
                candidate["_seen_keys"] = seen_keys
            result = audit_candidate(candidate, database, source_sha256=base_hash, connection=audit_connection, cache=audit_cache)
            if result.status == "agent_audited" and result.record is not None:
                output_rows.append(result.record)
            else:
                rejected_rows.append(_reject_row(candidate, result.reason_codes, base_sha256=base_hash, gold_hash=gold_hash, seed_hash=seed_hash))

    output_rows.sort(key=lambda row: (str(row.get("question_id")), str((row.get("evidence") or [{}])[0].get("evidence_id"))))
    rejected_rows.sort(key=lambda row: (str(row.get("candidate_id")), str(row.get("evidence_ids"))))
    write_jsonl_atomic(output, output_rows)
    write_jsonl_atomic(rejects, rejected_rows)
    reason_counts = Counter(code for row in rejected_rows for code in row["reason_codes"])
    summary_row = {
        "status": "ok" if base_valid else "blocked",
        "expected_base_sha256": expected_base_sha256,
        "base_sha256": base_hash,
        "gold_input_sha256": gold_hash,
        "seed_input_sha256": seed_hash,
        "candidate_count": len(existing) + len(generated_candidates),
        "audited_count": sum(row.get("review", {}).get("status") == "agent_audited" for row in output_rows),
        "human_passthrough_count": sum(row.get("answer_origin") == "human_verified" for row in output_rows),
        "rejected_count": len(rejected_rows),
        "reason_counts": dict(sorted(reason_counts.items())),
        "output_count": len(output_rows),
        "generator": "deterministic_agent_gold_v1",
    }
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(summary_row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary_row


def main(argv: Sequence[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--overlay-seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--rejects", type=Path, required=True)
    parser.add_argument("--predicate-config", type=Path, default=Path("config/agent_gold_predicates.json"))
    parser.add_argument("--expected-base-sha256", default=EXPECTED_BASE_SHA256)
    args = parser.parse_args(argv)
    result = generate_gold(
        database=args.database, gold_path=args.gold, overlay_seed=args.overlay_seed,
        output=args.output, summary=args.summary, rejects=args.rejects,
        predicate_config=args.predicate_config, expected_base_sha256=args.expected_base_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
