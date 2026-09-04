from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from disclosure_db.pipeline import query_database
from disclosure_db.evaluation import load_evaluation_contract
from disclosure_db.gold_validation import validate_gold_dataset
from disclosure_db.migration import portable_path, schema_sha256
from disclosure_db.retrieval_evaluation import evaluate_retrieval


SANITY_QUERIES = [
    {"text": "계약금액", "company": "삼성전자"},
    {"text": "감사의견", "company": "005930"},
    {"text": "자기주식취득결정", "company": None},
    {"text": "보유주식등의 수", "company": None},
    {"text": "매출액", "company": "현대자동차"},
]
DEFAULT_EVALUATION_CONTRACT = Path(__file__).resolve().parents[1] / "config" / "evaluation_contract.json"
DEFAULT_GOLD = Path(__file__).resolve().parents[1] / "data" / "derived" / "gold_qa.jsonl"


def scalar(connection: sqlite3.Connection, sql: str) -> object:
    return connection.execute(sql).fetchone()[0]


def validate(
    database: Path,
    evaluation_contract: Path = DEFAULT_EVALUATION_CONTRACT,
    *,
    expected_filings: int | None = None,
    expected_sources: int | None = None,
    gold_path: Path | None = None,
    require_semantic_v1: bool = False,
    integrity_mode: str = "quick",
    integrity_attestation: Path | None = None,
) -> dict[str, object]:
    if integrity_mode not in {"quick", "full", "attested"}:
        raise ValueError("integrity_mode must be 'quick', 'full', or 'attested'")
    if integrity_mode == "attested" and integrity_attestation is None:
        raise ValueError("attested integrity mode requires integrity_attestation")
    contract = load_evaluation_contract(evaluation_contract)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        integrity_attestation_result: dict[str, object] | None = None
        if integrity_mode == "attested":
            assert integrity_attestation is not None
            attestation = json.loads(integrity_attestation.read_text(encoding="utf-8"))
            attested_database = Path(str(attestation["output_database"]))
            if not attested_database.is_absolute():
                attested_database = Path.cwd() / attested_database
            attested_database = attested_database.resolve()
            attested_schema = str(attestation.get("after", {}).get("schema_sha256", ""))
            current_schema = schema_sha256(connection)
            integrity_attestation_result = {
                "path": portable_path(integrity_attestation),
                "database_path_matches": attested_database == database.resolve(),
                "database_bytes_match": int(attestation.get("output_bytes", -1)) == database.stat().st_size,
                "schema_sha256_match": bool(attested_schema) and attested_schema == current_schema,
                "attested_quick_check": attestation.get("quick_check"),
                "attested_foreign_key_violations": attestation.get("foreign_key_violations"),
            }
            attested_fk = integrity_attestation_result["attested_foreign_key_violations"]
            attestation_passed = (
                all(
                    bool(integrity_attestation_result[key])
                    for key in ("database_path_matches", "database_bytes_match", "schema_sha256_match")
                )
                and integrity_attestation_result["attested_quick_check"] == "ok"
                and attested_fk is not None
                and int(attested_fk) == 0
            )
            integrity = "ok" if attestation_passed else "attestation_failed"
            foreign_key_violations = 0 if attestation_passed else (-1 if attested_fk is None else int(attested_fk))
        else:
            integrity = scalar(
                connection,
                "PRAGMA integrity_check" if integrity_mode == "full" else "PRAGMA quick_check",
            )
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        table_names = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
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
        for optional_table in ("schema_migration", "financial_fact", "financial_fact_evidence"):
            counts[optional_table] = scalar(connection, f"SELECT count(*) FROM {optional_table}") if optional_table in table_names else 0
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
        financial_fact_status = (
            {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT validation_status,count(*) FROM financial_fact GROUP BY validation_status"
                )
            }
            if "financial_fact" in table_names else {}
        )
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
        fts_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='fragment_fts'"
        ).fetchone()
        fts_sql = str(fts_sql_row[0]) if fts_sql_row else ""
        trigger_names = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        semantic_schema_invariants = {
            "required_tables_missing": len(
                {"schema_migration", "financial_fact", "financial_fact_evidence"} - table_names
            ),
            "semantic_migration_record_missing": (
                0 if counts["schema_migration"] > 0 else 1
            ),
            "legacy_fact_type_rows": scalar(
                connection, "SELECT count(*) FROM fact WHERE fact_type='table_field_candidate'"
            ),
            "fts_not_external_content": int("content='fragment'" not in fts_sql.replace(" ", "")),
            "fts_sync_triggers_missing": len(
                {"fragment_fts_ai", "fragment_fts_ad", "fragment_fts_au"} - trigger_names
            ),
            "fact_evidence_filing_mismatch": scalar(
                connection,
                """SELECT count(*) FROM fact_evidence fe
                   JOIN fact f ON f.fact_id=fe.fact_id
                   JOIN table_cell tc ON tc.evidence_id=fe.evidence_id
                   WHERE f.filing_id<>tc.filing_id""",
            ),
            "validated_fact_without_evidence": scalar(
                connection,
                """SELECT count(*) FROM fact f WHERE f.validation_status='validated'
                   AND NOT EXISTS (SELECT 1 FROM fact_evidence fe WHERE fe.fact_id=f.fact_id)""",
            ),
            "legacy_quality_rule_alias_rows": scalar(
                connection,
                "SELECT count(*) FROM quality_issue WHERE rule_id IN ('lineage_unresolved','lineage_missing_original')",
            ),
        }
        if {"financial_fact", "financial_fact_evidence"} <= table_names:
            semantic_schema_invariants["financial_evidence_filing_mismatch"] = scalar(
                connection,
                """SELECT count(*) FROM financial_fact_evidence ffe
                   JOIN financial_fact ff ON ff.financial_fact_id=ffe.financial_fact_id
                   JOIN table_cell tc ON tc.evidence_id=ffe.evidence_id
                   WHERE ff.filing_id<>tc.filing_id""",
            )
            semantic_schema_invariants["validated_financial_fact_without_evidence"] = scalar(
                connection,
                """SELECT count(*) FROM financial_fact ff WHERE ff.validation_status='validated'
                   AND NOT EXISTS (
                       SELECT 1 FROM financial_fact_evidence ffe
                       WHERE ffe.financial_fact_id=ff.financial_fact_id
                   )""",
            )
        else:
            semantic_schema_invariants["financial_evidence_filing_mismatch"] = 0
            semantic_schema_invariants["validated_financial_fact_without_evidence"] = 0
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
        "human_gold_status": "not_provided",
        "validated_financial_facts": financial_fact_status.get("validated", 0),
    }
    gold_result: dict[str, object] | None = None
    retrieval_result: dict[str, object] | None = None
    retrieval_status = "not_evaluated_without_human_gold"
    semantic_status = "not_evaluated_without_human_gold_and_canonical_facts"
    if gold_path is not None:
        candidates_path = gold_path.with_name("gold_candidates.jsonl")
        gold_result = validate_gold_dataset(
            database=database,
            gold_path=gold_path,
            candidates_path=candidates_path,
        )
        semantic_blockers["human_gold_approved"] = gold_result["status_counts"].get("approved", 0)
        semantic_blockers["human_gold_status"] = (
            "released" if gold_result["gold_release_gate_passed"] else "not_released"
        )
        if gold_result["gold_release_gate_passed"]:
            retrieval_result = evaluate_retrieval(database=database, gold_path=gold_path, limit=20)
            threshold = float(contract.get("retrieval_acceptance", {}).get("minimum_target_recall_at_20", 1.0))
            retrieval_status = (
                "passed_on_evidence_addressable_gold_subset"
                if float(retrieval_result.get("target_recall_at_k") or 0.0) >= threshold
                else "failed_on_evidence_addressable_gold_subset"
            )
            semantic_status = (
                "human_gold_released_financial_semantics_pending"
                if financial_fact_status.get("validated", 0) == 0
                else "human_gold_released_answer_generation_not_evaluated"
            )
        else:
            retrieval_status = "blocked_by_gold_release_gate"
            semantic_status = "blocked_by_gold_release_gate"
    return {
        "database": portable_path(database),
        "database_bytes": database.stat().st_size,
        "integrity_check": integrity,
        "integrity_check_mode": integrity_mode,
        "integrity_attestation": integrity_attestation_result,
        "foreign_key_violations": foreign_key_violations,
        "counts": counts,
        "filings_by_group": by_group,
        "parse_status": parse_status,
        "detected_formats": detected_formats,
        "lineage_status": lineage_status,
        "fact_validation_status": fact_status,
        "financial_fact_validation_status": financial_fact_status,
        "quality_issue_rules": issue_rules,
        "invariants": invariants,
        "expected_counts": {
            "filings": expected_filings,
            "sources": expected_sources,
            "mismatches": expected_count_mismatches,
            "policy": "optional external contract; internal source_file_count reconciliation is always enforced",
        },
        "retrieval_projection_invariants": retrieval_projection,
        "semantic_schema_invariants": semantic_schema_invariants,
        "semantic_schema_gate_passed": all(
            int(value) == 0 for value in semantic_schema_invariants.values()
        ),
        "semantic_schema_gate_required": require_semantic_v1,
        "query_sanity": query_results,
        "structure_gate_passed": not structure_fail,
        "retrieval_smoke_gate_passed": retrieval_smoke_passed,
        "retrieval_relevance_gate_status": retrieval_status,
        "semantic_answer_gate_status": semantic_status,
        "semantic_blockers": semantic_blockers,
        "gold_validation": gold_result,
        "retrieval_gold_evaluation": retrieval_result,
        "evaluation_contract": {
            "path": portable_path(evaluation_contract),
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
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--without-gold", action="store_true")
    parser.add_argument("--require-semantic-v1", action="store_true")
    parser.add_argument("--integrity-mode", choices=("quick", "full", "attested"), default="quick")
    parser.add_argument("--integrity-attestation", type=Path)
    args = parser.parse_args()
    result = validate(
        args.database.resolve(),
        args.evaluation_contract.resolve(),
        expected_filings=args.expected_filings,
        expected_sources=args.expected_sources,
        gold_path=None if args.without_gold else args.gold.resolve(),
        require_semantic_v1=args.require_semantic_v1,
        integrity_mode=args.integrity_mode,
        integrity_attestation=(
            args.integrity_attestation.resolve() if args.integrity_attestation is not None else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["structure_gate_passed"]:
        raise SystemExit(1)
    if args.require_semantic_v1 and not result["semantic_schema_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
