from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .evaluation import REQUIRED_GOLD_RECORD_FIELDS


NUMERIC_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_issue(issues: list[dict[str, str]], question_id: str, rule_id: str, message: str) -> None:
    issues.append({"question_id": question_id, "rule_id": rule_id, "message": message})


def validate_record_contract(record: dict[str, Any]) -> list[dict[str, str]]:
    question_id = str(record.get("question_id") or "<missing>")
    issues: list[dict[str, str]] = []
    missing = REQUIRED_GOLD_RECORD_FIELDS - set(record)
    if missing:
        add_issue(issues, question_id, "required_fields_missing", ", ".join(sorted(missing)))
        return issues

    review = record["review"]
    origin = record["answer_origin"]
    status = review.get("status")
    if origin == "model_generated" and status != "candidate":
        add_issue(issues, question_id, "model_answer_not_candidate", "model_generated must remain candidate")
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
    answer_kind = answer.get("kind")
    answerable = record["answerability"] == "answerable"
    if answerable == (answer_kind == "unanswerable"):
        add_issue(issues, question_id, "answerability_kind_mismatch", "answerability and answer.kind disagree")
    if answerable and not record["evidence"] and not record["version_evidence"]:
        add_issue(issues, question_id, "answerable_without_evidence", "answerable requires content or version evidence")
    if record["question_type"] == "correction_aware" and not record["version_evidence"]:
        add_issue(issues, question_id, "correction_without_version_evidence", "correction_aware requires version evidence")

    if answer_kind == "numeric" and not NUMERIC_PATTERN.fullmatch(str(answer.get("value", ""))):
        add_issue(issues, question_id, "invalid_numeric_answer", "numeric value must be unformatted")
    if answer_kind == "multi_numeric":
        evidence_ids = {item.get("evidence_id") for item in record["evidence"]}
        for item in answer.get("values", []):
            if not NUMERIC_PATTERN.fullmatch(str(item.get("value", ""))):
                add_issue(issues, question_id, "invalid_multi_numeric_value", str(item.get("value")))
            if not set(item.get("evidence_ids", [])) <= evidence_ids:
                add_issue(issues, question_id, "multi_numeric_evidence_missing", str(item.get("label")))

    as_of = record.get("as_of")
    basis = record.get("version_basis")
    if basis == "as_of" and not as_of:
        add_issue(issues, question_id, "as_of_missing", "version_basis=as_of requires as_of")
    if as_of and basis != "as_of":
        add_issue(issues, question_id, "as_of_basis_mismatch", "non-null as_of requires version_basis=as_of")

    if not record["candidate_filing_ids"]:
        add_issue(issues, question_id, "candidate_filing_missing", "at least one candidate filing is required")
    source_filing_ids = {item.get("filing_id") for item in record["source_evidence"]}
    if not set(record["candidate_filing_ids"]) <= source_filing_ids:
        add_issue(issues, question_id, "candidate_without_source_evidence", "candidate filing lacks source evidence")

    if status == "approved":
        for item in record["evidence"]:
            if item.get("locator", {}).get("kind") == "table_cell" and item.get("table_structure_status") != "human_validated":
                add_issue(issues, question_id, "approved_table_not_human_validated", str(item.get("evidence_id")))
    if answerable and any(
        item.get("detected_format") == "pdf" and item.get("table_structure_status") == "unvalidated"
        for item in record["source_evidence"]
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
            question_id = str(record.get("question_id") or "<missing>")
            if question_id in question_ids:
                add_issue(issues, question_id, "duplicate_question_id", question_id)
            question_ids.add(question_id)
            issues.extend(validate_record_contract(record))

            for filing_id in record.get("candidate_filing_ids", []):
                if filing_id not in candidate_ids:
                    add_issue(issues, question_id, "unknown_candidate_filing", filing_id)
                else:
                    covered_candidates.add(filing_id)

            for item in record.get("evidence", []):
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

            for item in record.get("version_evidence", []):
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

            for item in record.get("source_evidence", []):
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
        status = str(record["review"]["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        origin = str(record["answer_origin"])
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
