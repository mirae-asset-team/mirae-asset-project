"""Build deterministic, evidence-backed agent-audited Gold candidates."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predicate-config", type=Path, default=Path("config/agent_gold_predicates.json"))
    args = parser.parse_args()
    loaded = load_predicate_config(args.predicate_config)
    print(json.dumps({"version": loaded["version"], "predicate_count": len(loaded["predicates"])}, ensure_ascii=False))
