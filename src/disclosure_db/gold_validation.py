from __future__ import annotations

import json
import datetime as _datetime
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .evaluation import REQUIRED_GOLD_RECORD_FIELDS


NUMERIC_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "config" / "gold_annotation_schema.json"
try:
    _RECORD_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
except Exception:  # pragma: no cover - a missing repository contract is itself a deployment error
    _RECORD_SCHEMA = {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_issue(issues: list[dict[str, str]], question_id: str, rule_id: str, message: str) -> None:
    issues.append({"question_id": question_id, "rule_id": rule_id, "message": message})


def _schema_type(value: Any, expected: str) -> bool:
    # bool is not an integer/number in the gold contract even though Python's
    # type hierarchy makes it one.
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            return False
    if expected == "null":
        return value is None
    return True


def _schema_check(value: Any, schema: dict[str, Any], path: str, issues: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    if "const" in schema and value != schema["const"]:
        issues.append(f"{path}:const")
        return
    if "enum" in schema:
        try:
            in_enum = value in schema["enum"]
        except (TypeError, ValueError):
            in_enum = False
        if not in_enum:
            issues.append(f"{path}:enum")
            return
    if "oneOf" in schema:
        branches: list[list[str]] = []
        for branch in schema.get("oneOf", []):
            branch_issues: list[str] = []
            _schema_check(value, branch, path, branch_issues)
            if not branch_issues:
                return
            branches.append(branch_issues)
        issues.append(f"{path}:oneOf")
        for branch_issues in branches:
            issues.extend(branch_issues)
        return
    expected_types = schema.get("type")
    if expected_types is not None:
        types = expected_types if isinstance(expected_types, list) else [expected_types]
        if not any(_schema_type(value, expected) for expected in types):
            issues.append(f"{path}:type")
            return
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            issues.append(f"{path}:minLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            issues.append(f"{path}:pattern")
        if schema.get("format") == "date":
            try:
                _datetime.date.fromisoformat(value)
            except ValueError:
                issues.append(f"{path}:date")
        if schema.get("format") == "date-time":
            try:
                _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                issues.append(f"{path}:date-time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(f"{path}:minimum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            issues.append(f"{path}:minItems")
        if schema.get("uniqueItems"):
            try:
                if len({json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value}) != len(value):
                    issues.append(f"{path}:uniqueItems")
            except (TypeError, ValueError):
                issues.append(f"{path}:uniqueItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _schema_check(item, item_schema, f"{path}[{index}]", issues)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                issues.append(f"{path}.{required}:required")
        if schema.get("additionalProperties") is False:
            for key in sorted(set(value) - set(properties)):
                issues.append(f"{path}.{key}:unknown")
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                _schema_check(value[key], child, f"{path}.{key}", issues)


def validate_record_schema(record: Any) -> list[dict[str, str]]:
    """Validate the canonical gold JSON schema without a third-party package.

    This intentionally handles the repository's draft-2020-12 subset, including
    nested additional-properties and bool-safe numeric checks. Semantic lineage
    rules remain in :func:`validate_record_contract`.
    """
    if not isinstance(record, dict):
        return [{"question_id": "<missing>", "rule_id": "schema_type", "message": "record must be an object"}]
    raw: list[str] = []
    _schema_check(record, _RECORD_SCHEMA, "$", raw)
    question_id = str(record.get("question_id") or "<missing>")
    return [{"question_id": question_id, "rule_id": "canonical_schema", "message": item} for item in raw]


def validate_record_contract(record: Any) -> list[dict[str, str]]:
    """Validate a canonical record and fail closed for malformed JSON shapes."""
    question_id = str(record.get("question_id") or "<missing>") if isinstance(record, dict) else "<missing>"
    try:
        return _validate_record_contract(record)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        return [{
            "question_id": question_id,
            "rule_id": "canonical_schema_exception",
            "message": type(exc).__name__,
        }]


def _validate_record_contract(record: Any) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        return validate_record_schema(record)
    question_id = str(record.get("question_id") or "<missing>")
    issues: list[dict[str, str]] = []
    for schema_issue in validate_record_schema(record):
        add_issue(issues, question_id, "canonical_schema", schema_issue["message"])
    missing = REQUIRED_GOLD_RECORD_FIELDS - set(record)
    if missing:
        add_issue(issues, question_id, "required_fields_missing", ", ".join(sorted(missing)))
        return issues

    review = record["review"]
    origin = record["answer_origin"]
    if not isinstance(review, dict):
        return issues
    status = review.get("status")
    if origin == "model_generated" and status not in {"candidate", "agent_audited"}:
        add_issue(issues, question_id, "model_answer_not_candidate", "model_generated must remain candidate or agent_audited")
    if status == "agent_audited" and origin != "model_generated":
        add_issue(issues, question_id, "agent_audited_not_model_origin", "agent_audited must remain model_generated")
    if status == "approved":
        if origin != "human_verified":
            add_issue(issues, question_id, "approved_answer_not_human", "approved requires human_verified")
        if not review.get("reviewer"):
            add_issue(issues, question_id, "approved_without_reviewer", "approved requires reviewer")
        if review.get("annotator") == review.get("reviewer"):
            add_issue(issues, question_id, "self_approved", "annotator and reviewer must differ")
        if not review.get("reviewed_at"):
            add_issue(issues, question_id, "approved_without_timestamp", "approved requires reviewed_at")

    answer = record["answer"]
    if not isinstance(answer, dict):
        return issues
    answer_kind = answer.get("kind")
    answerable = record["answerability"] == "answerable"
    if answerable == (answer_kind == "unanswerable"):
        add_issue(issues, question_id, "answerability_kind_mismatch", "answerability and answer.kind disagree")
    evidence = record["evidence"]
    version_evidence = record["version_evidence"]
    if not isinstance(evidence, list) or not isinstance(version_evidence, list):
        return issues
    if answerable and not evidence and not version_evidence:
        add_issue(issues, question_id, "answerable_without_evidence", "answerable requires content or version evidence")
    if record["question_type"] == "correction_aware" and not version_evidence:
        add_issue(issues, question_id, "correction_without_version_evidence", "correction_aware requires version evidence")

    if answer_kind == "numeric" and not NUMERIC_PATTERN.fullmatch(str(answer.get("value", ""))):
        add_issue(issues, question_id, "invalid_numeric_answer", "numeric value must be unformatted")
    if answer_kind == "multi_numeric":
        evidence_ids = {item.get("evidence_id") for item in evidence if isinstance(item, dict)}
        values = answer.get("values")
        if not isinstance(values, list):
            return issues
        for item in values:
            if not isinstance(item, dict):
                continue
            if not NUMERIC_PATTERN.fullmatch(str(item.get("value", ""))):
                add_issue(issues, question_id, "invalid_multi_numeric_value", str(item.get("value")))
            evidence_ids_for_item = item.get("evidence_ids", [])
            if not isinstance(evidence_ids_for_item, list):
                continue
            if not set(evidence_ids_for_item) <= evidence_ids:
                add_issue(issues, question_id, "multi_numeric_evidence_missing", str(item.get("label")))

    as_of = record.get("as_of")
    basis = record.get("version_basis")
    if basis == "as_of" and not as_of:
        add_issue(issues, question_id, "as_of_missing", "version_basis=as_of requires as_of")
    if as_of and basis != "as_of":
        add_issue(issues, question_id, "as_of_basis_mismatch", "non-null as_of requires version_basis=as_of")

    if not record["candidate_filing_ids"]:
        add_issue(issues, question_id, "candidate_filing_missing", "at least one candidate filing is required")
    source_evidence = record["source_evidence"]
    if not isinstance(source_evidence, list):
        return issues
    source_filing_ids = {item.get("filing_id") for item in source_evidence if isinstance(item, dict)}
    if not set(record["candidate_filing_ids"]) <= source_filing_ids:
        add_issue(issues, question_id, "candidate_without_source_evidence", "candidate filing lacks source evidence")

    if status == "approved":
        for item in evidence:
            locator = item.get("locator", {}) if isinstance(item, dict) else {}
            if isinstance(locator, dict) and locator.get("kind") == "table_cell" and item.get("table_structure_status") != "human_validated":
                add_issue(issues, question_id, "approved_table_not_human_validated", str(item.get("evidence_id")))
    if answerable and any(
        isinstance(item, dict) and item.get("detected_format") == "pdf" and item.get("table_structure_status") == "unvalidated"
        for item in source_evidence
    ) and record["question_type"] == "table_cell":
        add_issue(issues, question_id, "unvalidated_pdf_table_answered", "PDF table answer requires human validation")
    return issues


def validate_gold_dataset(
    *, database: Path, gold_path: Path, candidates_path: Path
) -> dict[str, Any]:
    records = load_jsonl(gold_path)
    candidates = load_jsonl(candidates_path)
    candidate_ids = {str(item["filing_id"]) for item in candidates}
    issues: list[dict[str, str]] = []
    question_ids: set[str] = set()
    covered_candidates: set[str] = set()

    database_uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for record in records:
            question_id = str(record.get("question_id") or "<missing>") if isinstance(record, dict) else "<missing>"
            if question_id in question_ids:
                add_issue(issues, question_id, "duplicate_question_id", question_id)
            question_ids.add(question_id)
            issues.extend(validate_record_contract(record))

            candidate_filing_ids = record.get("candidate_filing_ids", []) if isinstance(record, dict) else []
            if not isinstance(candidate_filing_ids, list):
                candidate_filing_ids = []
            for filing_id in candidate_filing_ids:
                if not isinstance(filing_id, str):
                    add_issue(issues, question_id, "unknown_candidate_filing", str(filing_id))
                    continue
                if filing_id not in candidate_ids:
                    add_issue(issues, question_id, "unknown_candidate_filing", filing_id)
                else:
                    covered_candidates.add(filing_id)

            evidence_rows = record.get("evidence", []) if isinstance(record, dict) else []
            if not isinstance(evidence_rows, list):
                evidence_rows = []
            for item in evidence_rows:
                if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str):
                    continue
                evidence_id = item["evidence_id"]
                row = connection.execute(
                    """SELECT tc.filing_id,sd.sha256,fv.lineage_status,fv.is_current,fv.effective_from,fv.effective_to
                       FROM table_cell tc JOIN source_document sd ON sd.source_id=tc.source_id
                       JOIN filing_version fv ON fv.filing_id=tc.filing_id WHERE tc.evidence_id=?
                       UNION ALL
                       SELECT fr.filing_id,sd.sha256,fv.lineage_status,fv.is_current,fv.effective_from,fv.effective_to
                       FROM fragment fr JOIN source_document sd ON sd.source_id=fr.source_id
                       JOIN filing_version fv ON fv.filing_id=fr.filing_id WHERE fr.evidence_id=?""",
                    (evidence_id, evidence_id),
                ).fetchone()
                if row is None:
                    add_issue(issues, question_id, "evidence_not_found", evidence_id)
                    continue
                expected = {
                    "filing_id": row[0], "source_sha256": row[1], "lineage_status": row[2],
                    "is_current": bool(row[3]), "effective_from": row[4], "effective_to": row[5],
                }
                for key, value in expected.items():
                    if item.get(key) != value:
                        add_issue(issues, question_id, "evidence_metadata_mismatch", f"{evidence_id}:{key}")

            version_rows = record.get("version_evidence", []) if isinstance(record, dict) else []
            if not isinstance(version_rows, list):
                version_rows = []
            for item in version_rows:
                if not isinstance(item, dict) or not isinstance(item.get("filing_id"), str):
                    continue
                row = connection.execute(
                    """SELECT event_id,parent_filing_id,lineage_status,lineage_confidence,is_current,
                              effective_from,effective_to,rationale FROM filing_version WHERE filing_id=?""",
                    (item["filing_id"],),
                ).fetchone()
                if row is None:
                    add_issue(issues, question_id, "version_evidence_not_found", item["filing_id"])
                    continue
                keys = ["event_id", "parent_filing_id", "lineage_status", "lineage_confidence", "is_current", "effective_from", "effective_to", "rationale"]
                values = [row[0], row[1], row[2], row[3], bool(row[4]), row[5], row[6], row[7]]
                for key, value in zip(keys, values):
                    if item.get(key) != value:
                        add_issue(issues, question_id, "version_metadata_mismatch", f"{item['filing_id']}:{key}")

            source_rows = record.get("source_evidence", []) if isinstance(record, dict) else []
            if not isinstance(source_rows, list):
                source_rows = []
            for item in source_rows:
                if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
                    continue
                row = connection.execute(
                    """SELECT s.filing_id,s.sha256,s.detected_format,s.parse_status,
                              (SELECT count(*) FROM fragment fr WHERE fr.filing_id=? AND fr.source_id=s.source_id),
                              (SELECT count(*) FROM table_record tr WHERE tr.filing_id=? AND tr.source_id=s.source_id),
                              (SELECT count(*) FROM table_cell tc WHERE tc.filing_id=? AND tc.source_id=s.source_id)
                       FROM source_document s WHERE s.source_id=?""",
                    (item["filing_id"], item["filing_id"], item["filing_id"], item["source_id"]),
                ).fetchone()
                if row is None:
                    add_issue(issues, question_id, "source_evidence_not_found", item["source_id"])
                    continue
                keys = ["filing_id", "sha256", "detected_format", "parse_status", "fragment_count", "table_count", "cell_count"]
                for key, value in zip(keys, row):
                    if item.get(key) != value:
                        add_issue(issues, question_id, "source_metadata_mismatch", f"{item['source_id']}:{key}")

    missing_candidates = sorted(candidate_ids - covered_candidates)
    if missing_candidates:
        for filing_id in missing_candidates:
            add_issue(issues, "<dataset>", "candidate_not_covered", filing_id)

    status_counts: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    for record in records:
        review = record.get("review") if isinstance(record, dict) else None
        status = str(review.get("status")) if isinstance(review, dict) else "<invalid>"
        status_counts[status] = status_counts.get(status, 0) + 1
        origin = str(record.get("answer_origin")) if isinstance(record, dict) else "<invalid>"
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    approved = status_counts.get("approved", 0)
    structural_passed = not issues
    return {
        "records": len(records),
        "candidate_documents": len(candidate_ids),
        "covered_candidate_documents": len(covered_candidates),
        "missing_candidate_filings": missing_candidates,
        "status_counts": status_counts,
        "answer_origin_counts": origin_counts,
        "issue_count": len(issues),
        "issues": issues,
        "candidate_review_ready": structural_passed and len(covered_candidates) == len(candidate_ids),
        "gold_release_gate_passed": structural_passed and approved == len(records) and approved > 0,
    }
