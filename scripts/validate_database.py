from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from disclosure_db.pipeline import query_database
from disclosure_db.evaluation import load_evaluation_contract


SANITY_QUERIES = [
    {"text": "계약금액", "company": "삼성전자"},
    {"text": "감사의견", "company": "005930"},
    {"text": "자기주식취득결정", "company": None},
    {"text": "보유주식등의 수", "company": None},
    {"text": "매출액", "company": "현대자동차"},
]
DEFAULT_EVALUATION_CONTRACT = Path(__file__).resolve().parents[1] / "config" / "evaluation_contract.json"


def scalar(connection: sqlite3.Connection, sql: str) -> object:
    return connection.execute(sql).fetchone()[0]


def validate(
    database: Path,
    evaluation_contract: Path = DEFAULT_EVALUATION_CONTRACT,
    *,
    expected_filings: int | None = None,
    expected_sources: int | None = None,
) -> dict[str, object]:
    contract = load_evaluation_contract(evaluation_contract)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        integrity = scalar(connection, "PRAGMA integrity_check")
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        counts = {
            table: scalar(connection, f"SELECT count(*) FROM {table}")
            for table in (
                "filing",
                "source_document",
                "parser_error",
                "fragment",
                "table_record",
                "table_cell",
                "fact",
                "fact_evidence",
                "filing_event",
                "filing_version",
                "quality_issue",
                "fragment_fts",
            )
        }
        by_group = {
            row[0]: row[1]
            for row in connection.execute("SELECT doc_group,count(*) FROM filing GROUP BY doc_group")
        }
        parse_status = {
            row[0]: row[1]
            for row in connection.execute("SELECT parse_status,count(*) FROM source_document GROUP BY parse_status")
        }
        detected_formats = {
            row[0]: row[1]
            for row in connection.execute("SELECT detected_format,count(*) FROM source_document GROUP BY detected_format")
        }
        lineage_status = {
            row[0]: row[1]
            for row in connection.execute("SELECT lineage_status,count(*) FROM filing_version GROUP BY lineage_status")
        }
        fact_status = {
            row[0]: row[1]
            for row in connection.execute("SELECT validation_status,count(*) FROM fact GROUP BY validation_status")
        }
        issue_rules = [
            dict(row)
            for row in connection.execute(
                """SELECT severity,dimension,rule_id,count(*) AS count
                   FROM quality_issue GROUP BY severity,dimension,rule_id
                   ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,count DESC"""
            )
        ]
        invariants = {
            "source_file_count_mismatch": abs(
                int(scalar(connection, "SELECT coalesce(sum(source_file_count),0) FROM filing"))
                - int(counts["source_document"])
            ),
            "facts_without_evidence": scalar(
                connection,
                "SELECT count(*) FROM fact f WHERE NOT EXISTS (SELECT 1 FROM fact_evidence fe WHERE fe.fact_id=f.fact_id)",
            ),
            "events_without_one_current": scalar(
                connection,
                "SELECT count(*) FROM (SELECT event_id FROM filing_version GROUP BY event_id HAVING sum(is_current) != 1)",
            ),
            "fragment_cell_evidence_collisions": scalar(
                connection,
                """SELECT count(*) FROM (
                       SELECT evidence_id FROM (
                           SELECT evidence_id FROM fragment UNION ALL SELECT evidence_id FROM table_cell
                       ) GROUP BY evidence_id HAVING count(*) > 1
                   )""",
            ),
            "filings_without_source": scalar(
                connection,
                "SELECT count(*) FROM filing f WHERE NOT EXISTS (SELECT 1 FROM source_document s WHERE s.filing_id=f.filing_id)",
            ),
            "successful_sources_without_fragment": scalar(
                connection,
                """SELECT count(*) FROM source_document s
                   WHERE s.parse_status='success'
                     AND NOT EXISTS (SELECT 1 FROM fragment fr WHERE fr.source_id=s.source_id)""",
            ),
        }
        retrieval_projection = {
            "fts_fragment_count_difference": abs(counts["fragment"] - counts["fragment_fts"]),
            "fts_rowid_orphans": scalar(
                connection,
                """SELECT count(*) FROM fragment_fts ft
                   LEFT JOIN fragment fr ON fr.rowid=ft.rowid
                   WHERE fr.rowid IS NULL""",
            ),
        }
    query_results = []
    for item in SANITY_QUERIES:
        started = time.perf_counter()
        results = query_database(database, item["text"], company=item["company"], limit=10)
        elapsed_ms = (time.perf_counter() - started) * 1000
        query_results.append(
            {
                **item,
                "latency_ms": round(elapsed_ms, 3),
                "result_count": len(results),
                "top_evidence_id": results[0]["evidence_id"] if results else None,
                "top_filing_id": results[0]["filing_id"] if results else None,
            }
        )
    expected_count_mismatches = {
        "filings": 0 if expected_filings is None else abs(counts["filing"] - expected_filings),
        "sources": 0 if expected_sources is None else abs(counts["source_document"] - expected_sources),
    }
    structure_fail = (
        integrity != "ok"
        or foreign_key_violations != 0
        or any(int(value) != 0 for value in invariants.values())
        or parse_status.get("failed", 0) != 0
        or detected_formats.get("unknown", 0) != 0
        or any(value != 0 for value in expected_count_mismatches.values())
    )
    retrieval_smoke_passed = (
        all(int(value) == 0 for value in retrieval_projection.values())
        and all(item["result_count"] > 0 for item in query_results)
    )
    semantic_blockers = {
        "candidate_facts_not_answerable": fact_status.get("candidate", 0),
        "unresolved_lineage": lineage_status.get("unresolved", 0),
        "missing_original": lineage_status.get("missing_original", 0),
        "human_gold_approved": None,
        "human_gold_status": "not_connected_to_database_validator",
    }
    return {
        "database": str(database.resolve()),
        "database_bytes": database.stat().st_size,
        "integrity_check": integrity,
        "foreign_key_violations": foreign_key_violations,
        "counts": counts,
        "filings_by_group": by_group,
        "parse_status": parse_status,
        "detected_formats": detected_formats,
        "lineage_status": lineage_status,
        "fact_validation_status": fact_status,
        "quality_issue_rules": issue_rules,
        "invariants": invariants,
        "expected_counts": {
            "filings": expected_filings,
            "sources": expected_sources,
            "mismatches": expected_count_mismatches,
            "policy": "optional external contract; internal source_file_count reconciliation is always enforced",
        },
        "retrieval_projection_invariants": retrieval_projection,
        "query_sanity": query_results,
        "structure_gate_passed": not structure_fail,
        "retrieval_smoke_gate_passed": retrieval_smoke_passed,
        "retrieval_relevance_gate_status": "not_evaluated_without_human_gold",
        "semantic_answer_gate_status": "not_evaluated_without_human_gold_and_canonical_facts",
        "semantic_blockers": semantic_blockers,
        "evaluation_contract": {
            "path": str(evaluation_contract.resolve()),
            "version": contract["version"],
            "status": contract["status"],
            "question_type_count": len(contract["question_types"]),
            "gate_groups": sorted(contract["quality_gates"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-contract", type=Path, default=DEFAULT_EVALUATION_CONTRACT)
    parser.add_argument("--expected-filings", type=int)
    parser.add_argument("--expected-sources", type=int)
    args = parser.parse_args()
    result = validate(
        args.database.resolve(),
        args.evaluation_contract.resolve(),
        expected_filings=args.expected_filings,
        expected_sources=args.expected_sources,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["structure_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
