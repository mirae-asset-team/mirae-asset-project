from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from disclosure_db.financial_extraction import extract_table_candidates
from disclosure_db.financial_validation import (
    apply_review_decisions,
    canonical_json_bytes,
    validate_financial_candidates,
)
from disclosure_db.schema import create_schema
from tests.test_financial_universe import _insert_consolidated_table, _insert_filing, _insert_version
from scripts.build_financial_validation import main as build_validation_main


def _insert_cell(
    connection: sqlite3.Connection,
    *,
    filing_id: str,
    row: int,
    column: int,
    text: str,
    kind: str,
    headers: list[str] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO table_cell(
               evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,colspan,
               cell_kind,row_header_path_json,column_header_path_json,locator_json,text_raw,
               text_normalized,parser_version)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"ev-{row}-{column}",
            f"tbl-{filing_id}",
            f"src-{filing_id}",
            filing_id,
            row,
            column,
            1,
            1,
            kind,
            "[]",
            json.dumps(headers or [], ensure_ascii=False),
            json.dumps({"kind": "table_cell", "row": row, "column": column}),
            text,
            text,
            "fixture-v1",
        ),
    )


def _fixture() -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, object], list[dict[str, object]]]:
    temp = tempfile.TemporaryDirectory()
    database = Path(temp.name) / "base.sqlite"
    connection = sqlite3.connect(database)
    create_schema(connection)
    filing_id = "20250301000001"
    _insert_filing(
        connection,
        filing_id,
        corp_code="00000001",
        stock_code="000001",
        issuer_name="테스트전자",
        listed_name="테스트전자",
        report_name="사업보고서",
        filed_at="2025-03-01",
        base_year=2024,
    )
    _insert_version(connection, filing_id, event_id="evt-2024", version_no=1, current=True)
    _insert_consolidated_table(connection, filing_id)
    for column, header in enumerate(("", "제 10 기", "제 9 기", "제 8 기")):
        _insert_cell(
            connection,
            filing_id=filing_id,
            row=0,
            column=column,
            text=header,
            kind="header",
        )
    _insert_cell(connection, filing_id=filing_id, row=1, column=0, text="매출액", kind="data")
    for column, (header, value) in enumerate(
        (("제 10 기", "300"), ("제 9 기", "200"), ("제 8 기", "100")),
        start=1,
    ):
        _insert_cell(
            connection,
            filing_id=filing_id,
            row=1,
            column=column,
            text=value,
            kind="data",
            headers=[header],
        )
    connection.commit()
    table = {
        "table_id": f"tbl-{filing_id}",
        "filing_id": filing_id,
        "source_id": f"src-{filing_id}",
        "sequence_no": 1,
        "section_path_json": json.dumps(["III. 재무에 관한 사항", "2. 연결재무제표"], ensure_ascii=False),
        "caption": "연결 손익계산서",
        "unit_text": "단위: 백만원",
        "row_count": 3,
        "column_count": 4,
        "parse_status": "success",
        "locator_json": "{}",
    }
    cells = [
        dict(zip(("evidence_id", "table_id", "source_id", "filing_id", "row_index", "column_index", "rowspan", "colspan", "cell_kind", "row_header_path_json", "column_header_path_json", "locator_json", "text_raw", "text_normalized", "parser_version"), row))
        for row in connection.execute("SELECT * FROM table_cell ORDER BY row_index,column_index")
    ]
    candidates, rejects = extract_table_candidates(table, cells, fiscal_year=2024)
    assert not rejects
    connection.close()
    manifest: dict[str, object] = {
        "schema_version": "financial-universe-v1",
        "source_database_sha256": "a" * 64,
        "source_company_count": 1,
        "company_count": 1,
        "companies": [
            {
                "issuer_corp_code": "00000001",
                "stock_code": "000001",
                "issuer_name": "테스트전자",
                "listed_name": "테스트전자",
                "filing_id": filing_id,
                "fiscal_year": 2024,
                "lineage_status": "root",
            }
        ],
        "rejects": [],
    }
    return temp, database, manifest, candidates


class FinancialValidationTests(unittest.TestCase):
    def test_validates_exact_reconstructed_cells_as_agent_audited(self) -> None:
        temp, database, manifest, candidates = _fixture()
        self.addCleanup(temp.cleanup)

        result = validate_financial_candidates(
            database,
            manifest,
            candidates,
            extraction_rejects=[],
            review_decisions=[],
        )

        self.assertEqual(len(result["seed"]), 3)
        self.assertEqual(result["validator_rejects"], [])
        fact = result["seed"][0]
        self.assertEqual(fact["validation_status"], "validated")
        self.assertEqual(fact["trust_tier"], "agent_audited")
        self.assertEqual(fact["extraction_method"], "agent_audited_annual_statement_rule_v1")
        self.assertEqual(fact["evidence_ids"], ["ev-1-1"])
        self.assertNotIn("human_verified", json.dumps(fact))
        self.assertEqual(result["coverage"]["validated_grain_count"], 3)
        self.assertEqual(result["coverage"]["expected_grain_count"], 18)
        self.assertTrue(result["coverage"]["metrics"]["revenue"]["aggregate_eligible"])
        self.assertFalse(result["coverage"]["metrics"]["operating_income"]["aggregate_eligible"])

    def test_rejects_cross_filing_and_value_tampering(self) -> None:
        temp, database, manifest, candidates = _fixture()
        self.addCleanup(temp.cleanup)
        cross_filing = dict(candidates[0], filing_id="20990101000001")
        tampered = dict(candidates[1], value_numeric="999")

        result = validate_financial_candidates(
            database,
            manifest,
            [cross_filing, tampered],
            extraction_rejects=[],
            review_decisions=[],
        )

        self.assertEqual(result["seed"], [])
        self.assertEqual(
            {item["reason_code"] for item in result["validator_rejects"]},
            {"candidate_filing_not_selected", "candidate_does_not_match_source"},
        )

    def test_duplicate_review_requires_equal_values_and_never_claims_human_review(self) -> None:
        duplicate_rows = [
            {
                "candidate_id": "a",
                "filing_id": "f1",
                "account_id": "revenue",
                "account_name_raw": "영업수익",
                "fiscal_year": 2025,
                "value_numeric": "10",
                "reason_code": "duplicate_metric_grain",
            },
            {
                "candidate_id": "b",
                "filing_id": "f1",
                "account_id": "revenue",
                "account_name_raw": "매출액",
                "fiscal_year": 2025,
                "value_numeric": "10",
                "reason_code": "duplicate_metric_grain",
            },
        ]
        decisions = [
            {
                "filing_id": "f1",
                "account_id": "revenue",
                "action": "prefer_raw_label",
                "account_name_raw": "영업수익",
                "review_status": "agent_audited",
                "human_confirmed": False,
            }
        ]

        admitted, remaining, audit = apply_review_decisions([], duplicate_rows, decisions)

        self.assertEqual([item["candidate_id"] for item in admitted], ["a"])
        self.assertEqual(remaining, [])
        self.assertEqual(audit[0]["status"], "agent_audited")
        self.assertFalse(audit[0]["human_confirmed"])

        conflicting = [dict(duplicate_rows[0]), dict(duplicate_rows[1], value_numeric="11")]
        admitted, remaining, audit = apply_review_decisions([], conflicting, decisions)
        self.assertEqual(admitted, [])
        self.assertEqual({item["reason_code"] for item in remaining}, {"review_value_conflict"})
        self.assertEqual(audit[0]["status"], "unresolved")

    def test_canonical_json_is_order_independent_for_coverage_mappings(self) -> None:
        first = canonical_json_bytes({"metrics": {"revenue": 1, "net_income": 2}, "companies": [2, 1]})
        second = canonical_json_bytes({"companies": [2, 1], "metrics": {"net_income": 2, "revenue": 1}})
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

    def test_cli_writes_seed_coverage_review_queue_and_report(self) -> None:
        temp, database, manifest_payload, candidates_payload = _fixture()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        stat = database.stat()
        attestation = root / "attestation.json"
        attestation.write_text(
            json.dumps(
                {
                    "database_version": "fixture-v1",
                    "database": {
                        "uncompressed_sha256": "a" * 64,
                        "uncompressed_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    },
                }
            ),
            encoding="utf-8",
        )
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
        candidates = root / "candidates.jsonl"
        candidates.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates_payload),
            encoding="utf-8",
        )
        extraction_rejects = root / "extract-rejects.jsonl"
        extraction_rejects.write_bytes(b"")
        decisions = root / "decisions.json"
        decisions.write_text(
            json.dumps({"schema_version": "financial-review-decisions-v1", "decisions": []}),
            encoding="utf-8",
        )
        seed = root / "seed.jsonl"
        coverage = root / "coverage.json"
        review_queue = root / "review.jsonl"
        report = root / "coverage.md"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            build_validation_main(
                [
                    "--database", str(database),
                    "--attestation", str(attestation),
                    "--manifest", str(manifest),
                    "--candidates", str(candidates),
                    "--extraction-rejects", str(extraction_rejects),
                    "--review-decisions", str(decisions),
                    "--seed", str(seed),
                    "--coverage", str(coverage),
                    "--review-queue", str(review_queue),
                    "--report", str(report),
                ]
            )

        self.assertEqual(len(seed.read_text(encoding="utf-8").splitlines()), 3)
        self.assertEqual(json.loads(coverage.read_text(encoding="utf-8"))["validated_grain_count"], 3)
        self.assertEqual(review_queue.read_bytes(), b"")
        self.assertIn("검증 재무 데이터 Coverage", report.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")


if __name__ == "__main__":
    unittest.main()
