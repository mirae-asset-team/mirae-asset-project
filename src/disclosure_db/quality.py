from __future__ import annotations

import json
import sqlite3
from collections import Counter

from .identifiers import stable_digest
from .evaluation import (
    RULE_EVENT_CURRENT_CARDINALITY,
    RULE_EVIDENCE_ID_COLLISION,
    RULE_FACT_WITHOUT_EVIDENCE,
    RULE_IMAGE_REVIEW,
    RULE_LINEAGE_UNRESOLVED,
    RULE_MANIFEST_OR_SOURCE_MISSING,
    RULE_MISSING_ORIGINAL,
    RULE_PDF_TABLE_UNVALIDATED,
    RULE_SILENT_PARSE_FAILURE,
)


def add_issue(
    connection: sqlite3.Connection,
    run_id: str,
    severity: str,
    dimension: str,
    entity_type: str,
    entity_id: str,
    rule_id: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    issue_id = f"qi_{stable_digest(run_id, entity_type, entity_id, rule_id, length=28)}"
    connection.execute(
        """INSERT OR IGNORE INTO quality_issue(
               issue_id,run_id,severity,dimension,entity_type,entity_id,rule_id,message,details_json
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (issue_id, run_id, severity, dimension, entity_type, entity_id, rule_id, message, json.dumps(details or {}, ensure_ascii=False)),
    )


def evaluate_quality(connection: sqlite3.Connection, run_id: str, expected_filings: int) -> dict[str, object]:
    filing_count = connection.execute("SELECT count(*) FROM filing").fetchone()[0]
    if filing_count != expected_filings:
        add_issue(connection, run_id, "error", "coverage", "run", run_id, RULE_MANIFEST_OR_SOURCE_MISSING, "Manifest and DB filing counts differ", {"expected": expected_filings, "actual": filing_count})

    for source_id, filing_id, status, chars, warnings, coverage_json in connection.execute(
        """SELECT s.source_id,s.filing_id,s.parse_status,
                  CAST(json_extract(s.coverage_json,'$.extracted_text_chars') AS INTEGER),s.warnings_json,
                  s.coverage_json
           FROM source_document s"""
    ):
        warning_values = json.loads(warnings)
        coverage = json.loads(coverage_json)
        if status == "failed":
            add_issue(connection, run_id, "error", "validity", "source", source_id, RULE_SILENT_PARSE_FAILURE, "Source parsing failed", {"filing_id": filing_id, "warnings": warning_values})
        elif status in {"partial", "unsupported"}:
            add_issue(connection, run_id, "warning", "validity", "source", source_id, "parse_partial", f"Source parsing status is {status}", {"filing_id": filing_id, "warnings": warning_values})
        if not chars and status == "success":
            add_issue(connection, run_id, "warning", "coverage", "source", source_id, "empty_success", "Successful source has no extracted text", {"filing_id": filing_id})
        if "strict_xml_failed_recovery_used" in warning_values:
            add_issue(connection, run_id, "warning", "validity", "source", source_id, "recovery_parser_used", "Strict XML failed and recovery parsing was used", {"filing_id": filing_id})
        if "pdf_table_structure_not_validated" in warning_values:
            add_issue(connection, run_id, "warning", "coverage", "source", source_id, RULE_PDF_TABLE_UNVALIDATED, "PDF text was extracted without validated table structure", {"filing_id": filing_id})
        if int(coverage.get("image_reference_count") or 0) > 0:
            add_issue(
                connection,
                run_id,
                "warning",
                "coverage",
                "source",
                source_id,
                RULE_IMAGE_REVIEW,
                "Source references images; whether the answer depends on an unavailable binary asset is not resolved",
                {"filing_id": filing_id, "reference_count": coverage["image_reference_count"]},
            )

    for filing_id, status in connection.execute("SELECT filing_id,lineage_status FROM filing_version WHERE lineage_status IN ('unresolved','missing_original')"):
        add_issue(
            connection,
            run_id,
            "warning",
            "consistency",
            "filing",
            filing_id,
            RULE_LINEAGE_UNRESOLVED if status == "unresolved" else RULE_MISSING_ORIGINAL,
            f"Correction lineage is {status}",
        )

    orphan_facts = connection.execute(
        """SELECT count(*) FROM fact f
           WHERE NOT EXISTS (SELECT 1 FROM fact_evidence fe WHERE fe.fact_id=f.fact_id)"""
    ).fetchone()[0]
    if orphan_facts:
        add_issue(connection, run_id, "error", "referential_integrity", "run", run_id, RULE_FACT_WITHOUT_EVIDENCE, "Facts without evidence exist", {"count": orphan_facts})

    current_violations = connection.execute(
        """SELECT count(*) FROM (
               SELECT event_id FROM filing_version GROUP BY event_id HAVING sum(is_current) != 1
           )"""
    ).fetchone()[0]
    if current_violations:
        add_issue(connection, run_id, "error", "consistency", "run", run_id, RULE_EVENT_CURRENT_CARDINALITY, "An event must have exactly one current version", {"count": current_violations})

    duplicate_evidence = connection.execute(
        """SELECT count(*) FROM (
               SELECT evidence_id FROM (
                   SELECT evidence_id FROM fragment UNION ALL SELECT evidence_id FROM table_cell
               ) GROUP BY evidence_id HAVING count(*) > 1
           )"""
    ).fetchone()[0]
    if duplicate_evidence:
        add_issue(connection, run_id, "error", "uniqueness", "run", run_id, RULE_EVIDENCE_ID_COLLISION, "Evidence IDs collide across fragments and cells", {"count": duplicate_evidence})

    issue_counts = Counter()
    for severity, count in connection.execute("SELECT severity,count(*) FROM quality_issue GROUP BY severity"):
        issue_counts[severity] = count
    parse_counts = dict(connection.execute("SELECT parse_status,count(*) FROM source_document GROUP BY parse_status"))
    lineage_counts = dict(connection.execute("SELECT lineage_status,count(*) FROM filing_version GROUP BY lineage_status"))
    result = {
        "filings": filing_count,
        "sources": connection.execute("SELECT count(*) FROM source_document").fetchone()[0],
        "fragments": connection.execute("SELECT count(*) FROM fragment").fetchone()[0],
        "tables": connection.execute("SELECT count(*) FROM table_record").fetchone()[0],
        "cells": connection.execute("SELECT count(*) FROM table_cell").fetchone()[0],
        "facts": connection.execute("SELECT count(*) FROM fact").fetchone()[0],
        "parse_status": parse_counts,
        "lineage_status": lineage_counts,
        "quality_issues": dict(issue_counts),
        "structure_gate_passed": issue_counts["error"] == 0,
    }
    return result
