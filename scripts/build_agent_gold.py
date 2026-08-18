"""Build deterministic, evidence-backed agent-audited Gold candidates."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


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
    candidate: dict[str, Any], base: Path, *, source_sha256: str
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
    with closing(_readonly_connection(base)) as connection:
        for item in record.get("evidence", []):
            if not isinstance(item, dict):
                reasons.add("schema_invalid")
                continue
            evidence_id = str(item.get("evidence_id") or "")
            evidence_ids.append(evidence_id)
            actual = _record_evidence_row(connection, evidence_id) if evidence_id else None
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
            actual_source = _record_source_row(connection, str(item.get("source_id") or ""))
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predicate-config", type=Path, default=Path("config/agent_gold_predicates.json"))
    args = parser.parse_args()
    loaded = load_predicate_config(args.predicate_config)
    print(json.dumps({"version": loaded["version"], "predicate_count": len(loaded["predicates"])}, ensure_ascii=False))
