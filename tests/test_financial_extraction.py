from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from disclosure_db.financial_extraction import (
    canonical_account_id,
    classify_statement_table,
    extract_financial_candidate_corpus,
    extract_table_candidates,
    parse_numeric_value,
    parse_unit_text,
    resolve_filing_candidates,
)
from scripts.build_financial_candidates import main as build_candidates_main


def _cell(
    row: int,
    column: int,
    text: str,
    *,
    kind: str = "data",
    column_headers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "evidence_id": f"ev-{row}-{column}",
        "table_id": "table-1",
        "filing_id": "20260310002820",
        "row_index": row,
        "column_index": column,
        "cell_kind": kind,
        "row_header_path_json": "[]",
        "column_header_path_json": json.dumps(column_headers or [], ensure_ascii=False),
        "text_raw": text,
        "text_normalized": text,
    }


def _table(*, caption: str, scope: str = "consolidated", unit: str = "단위 : 백만원") -> dict[str, object]:
    section = "2. 연결재무제표" if scope == "consolidated" else "4. 재무제표"
    return {
        "table_id": "table-1",
        "filing_id": "20260310002820",
        "sequence_no": 10,
        "section_path_json": json.dumps(["III. 재무에 관한 사항", section], ensure_ascii=False),
        "caption": caption,
        "unit_text": unit,
        "row_count": 10,
        "column_count": 4,
        "parse_status": "success",
    }


def _three_period_row(label: str, values: tuple[str, str, str]) -> list[dict[str, object]]:
    return [
        _cell(0, 0, "", kind="header"),
        _cell(0, 1, "제 57 기", kind="header"),
        _cell(0, 2, "제 56 기", kind="header"),
        _cell(0, 3, "제 55 기", kind="header"),
        _cell(1, 0, label),
        _cell(1, 1, values[0], column_headers=["제 57 기"]),
        _cell(1, 2, values[1], column_headers=["제 56 기"]),
        _cell(1, 3, values[2], column_headers=["제 55 기"]),
    ]


class FinancialExtractionTests(unittest.TestCase):
    def test_canonical_aliases_are_strict_and_preserve_finance_revenue(self) -> None:
        self.assertEqual(canonical_account_id("매출액 (주30)"), "revenue")
        self.assertEqual(canonical_account_id("수익(매출액)"), "revenue")
        self.assertEqual(canonical_account_id("영업수익 (주8)"), "revenue")
        self.assertEqual(canonical_account_id("Ⅰ.영업이익"), "operating_income")
        self.assertEqual(canonical_account_id("Ⅵ.당기순이익"), "net_income")
        self.assertEqual(canonical_account_id("연결당기순이익(손실)"), "net_income")
        self.assertEqual(canonical_account_id("자산총계 (주8)"), "total_assets")
        self.assertEqual(canonical_account_id("자산 합계"), "total_assets")
        self.assertEqual(canonical_account_id("부채총계"), "total_liabilities")
        self.assertEqual(canonical_account_id("부채 합계"), "total_liabilities")
        self.assertEqual(canonical_account_id("자본총계"), "total_equity")
        self.assertIsNone(canonical_account_id("매출채권"))
        self.assertIsNone(canonical_account_id("기타의영업수익 (주36)"))
        self.assertIsNone(canonical_account_id("법인세비용차감전순이익"))

    def test_units_and_parenthesized_numbers_are_parsed_without_guessing(self) -> None:
        self.assertEqual(parse_unit_text("단위 : 원"), ("원", 1))
        self.assertEqual(parse_unit_text("(단위: 천원)"), ("천원", 1_000))
        self.assertEqual(parse_unit_text("단위 : 백만원"), ("백만원", 1_000_000))
        self.assertEqual(parse_unit_text("단위: 억원"), ("억원", 100_000_000))
        self.assertEqual(parse_numeric_value("333,605,938"), "333605938")
        self.assertEqual(parse_numeric_value("(2,325,993)"), "-2325993")
        self.assertEqual(parse_numeric_value("△ 12.50"), "-12.50")
        self.assertIsNone(parse_numeric_value("-"))
        self.assertIsNone(parse_numeric_value("해당 없음"))

    def test_only_exact_main_statement_sections_are_eligible(self) -> None:
        consolidated = classify_statement_table(_table(caption="2-2. 연결 손익계산서"))
        self.assertEqual(consolidated, {"scope": "consolidated", "statement_type": "IS", "priority": 0, "unit_raw": "백만원", "scale": 1_000_000})
        separate = classify_statement_table(_table(caption="4-1. 재무상태표", scope="separate"))
        self.assertEqual(separate["scope"], "separate")
        notes = _table(caption="4-1. 재무상태표", scope="separate")
        notes["section_path_json"] = json.dumps(["3. 연결재무제표 주석", "12. 재무제표"], ensure_ascii=False)
        self.assertIsNone(classify_statement_table(notes))
        partial = _table(caption="2-2. 연결 손익계산서")
        partial["parse_status"] = "partial"
        self.assertIsNone(classify_statement_table(partial))

    def test_extracts_three_annual_duration_periods_with_value_cell_evidence(self) -> None:
        facts, rejects = extract_table_candidates(
            _table(caption="2-2. 연결 손익계산서"),
            _three_period_row("매출액 (주30)", ("333,605,938", "300,870,903", "258,935,494")),
            fiscal_year=2025,
        )

        self.assertEqual(rejects, [])
        self.assertEqual([fact["fiscal_year"] for fact in facts], [2025, 2024, 2023])
        self.assertEqual([fact["value_numeric"] for fact in facts], ["333605938", "300870903", "258935494"])
        self.assertEqual(facts[0]["period_start"], "2025-01-01")
        self.assertEqual(facts[0]["period_end"], "2025-12-31")
        self.assertEqual(facts[0]["scope"], "consolidated")
        self.assertEqual(facts[0]["account_name_raw"], "매출액 (주30)")
        self.assertEqual(facts[0]["evidence_ids"], ["ev-1-1"])
        self.assertEqual(facts[0]["account_evidence_id"], "ev-1-0")

    def test_balance_sheet_uses_instant_periods(self) -> None:
        facts, rejects = extract_table_candidates(
            _table(caption="2-1. 연결 재무상태표"),
            _three_period_row("자산총계", ("566,942,110", "514,531,948", "455,905,980")),
            fiscal_year=2025,
        )

        self.assertEqual(rejects, [])
        self.assertEqual(facts[0]["statement_type"], "BS")
        self.assertEqual(facts[0]["period_type"], "instant")
        self.assertEqual(facts[0]["instant_date"], "2025-12-31")
        self.assertIsNone(facts[0]["period_start"])
        self.assertIsNone(facts[0]["period_end"])

    def test_prefers_consolidated_and_ordinary_income_statement(self) -> None:
        consolidated_is, _ = extract_table_candidates(
            _table(caption="2-2. 연결 손익계산서"),
            _three_period_row("당기순이익", ("30", "20", "10")),
            fiscal_year=2025,
        )
        comprehensive_table = _table(caption="2-3. 연결 포괄손익계산서")
        comprehensive_table["table_id"] = "table-2"
        consolidated_cis, _ = extract_table_candidates(
            comprehensive_table,
            _three_period_row("당기순이익", ("30", "20", "10")),
            fiscal_year=2025,
        )
        separate, _ = extract_table_candidates(
            _table(caption="4-2. 손익계산서", scope="separate"),
            _three_period_row("당기순이익", ("300", "200", "100")),
            fiscal_year=2025,
        )

        admitted, rejects, scope = resolve_filing_candidates(
            [*separate, *consolidated_cis, *consolidated_is]
        )

        self.assertEqual(scope, "consolidated")
        self.assertEqual(rejects, [])
        self.assertEqual([fact["value_numeric"] for fact in admitted], ["30", "20", "10"])
        self.assertTrue(all(fact["statement_priority"] == 0 for fact in admitted))

    def test_uses_separate_only_when_no_consolidated_statement_exists(self) -> None:
        separate, _ = extract_table_candidates(
            _table(caption="4-2. 손익계산서", scope="separate"),
            _three_period_row("영업이익", ("3", "2", "1")),
            fiscal_year=2025,
        )

        admitted, rejects, scope = resolve_filing_candidates(separate)

        self.assertEqual(rejects, [])
        self.assertEqual(scope, "separate")
        self.assertEqual(len(admitted), 3)

    def test_conflicting_same_priority_rows_are_rejected(self) -> None:
        first, _ = extract_table_candidates(
            _table(caption="2-2. 연결 손익계산서"),
            _three_period_row("영업이익", ("3", "2", "1")),
            fiscal_year=2025,
        )
        second = [dict(item, table_id="table-conflict", value_numeric="999") for item in first]

        admitted, rejects, scope = resolve_filing_candidates([*first, *second])

        self.assertEqual(scope, "consolidated")
        self.assertEqual(admitted, [])
        self.assertEqual({item["reason_code"] for item in rejects}, {"conflicting_metric_grain"})

    def test_corpus_extraction_is_read_only_deterministic_and_reports_missing_grains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "base.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE table_record(
                    table_id TEXT PRIMARY KEY, filing_id TEXT, source_id TEXT, sequence_no INTEGER,
                    section_path_json TEXT, caption TEXT, unit_text TEXT, row_count INTEGER,
                    column_count INTEGER, parse_status TEXT, locator_json TEXT
                );
                CREATE TABLE table_cell(
                    evidence_id TEXT PRIMARY KEY, table_id TEXT, source_id TEXT, filing_id TEXT,
                    row_index INTEGER, column_index INTEGER, rowspan INTEGER, colspan INTEGER,
                    cell_kind TEXT, row_header_path_json TEXT, column_header_path_json TEXT,
                    locator_json TEXT, text_raw TEXT, text_normalized TEXT, parser_version TEXT
                );
                """
            )
            statement = _table(caption="2-2. 연결 손익계산서")
            connection.execute(
                "INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    statement["table_id"], statement["filing_id"], "src-1", 10,
                    statement["section_path_json"], statement["caption"], statement["unit_text"],
                    10, 4, "success", "{}",
                ),
            )
            rows = [
                *_three_period_row("매출액", ("333,605,938", "300,870,903", "258,935,494")),
                _cell(2, 0, "영업이익"),
                _cell(2, 1, "43,601,051", column_headers=["제 57 기"]),
                _cell(2, 2, "32,725,961", column_headers=["제 56 기"]),
                _cell(2, 3, "6,566,976", column_headers=["제 55 기"]),
            ]
            for cell in rows:
                cell = dict(cell)
                cell["evidence_id"] = f"{cell['evidence_id']}-{cell['text_raw']}"
                connection.execute(
                    "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cell["evidence_id"], "table-1", "src-1", "20260310002820",
                        cell["row_index"], cell["column_index"], 1, 1, cell["cell_kind"],
                        cell["row_header_path_json"], cell["column_header_path_json"], "{}",
                        cell["text_raw"], cell["text_normalized"], "fixture-v1",
                    ),
                )
            connection.commit()
            connection.close()
            before = database.stat()
            manifest = {
                "schema_version": "financial-universe-v1",
                "source_database_sha256": "a" * 64,
                "companies": [
                    {
                        "issuer_corp_code": "00126380",
                        "stock_code": "005930",
                        "issuer_name": "삼성전자",
                        "listed_name": "삼성전자",
                        "filing_id": "20260310002820",
                        "fiscal_year": 2025,
                    }
                ],
            }

            first = extract_financial_candidate_corpus(database, manifest)
            second = extract_financial_candidate_corpus(database, manifest)

            after = database.stat()
            self.assertEqual(first, second)
            self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
            self.assertEqual(first["summary"]["candidate_count"], 6)
            self.assertEqual(first["summary"]["expected_grain_count"], 18)
            self.assertEqual(first["summary"]["missing_grain_count"], 12)
            self.assertEqual(first["summary"]["scope_counts"], {"consolidated": 1})
            revenue = [fact for fact in first["candidates"] if fact["account_id"] == "revenue"]
            self.assertEqual(revenue[0]["value_numeric"], "333605938")
            self.assertEqual(revenue[0]["issuer_corp_code"], "00126380")
            self.assertEqual({item["reason_code"] for item in first["rejects"]}, {"metric_period_missing"})

    def test_cli_attests_source_and_writes_three_outputs_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE table_record(
                    table_id TEXT, filing_id TEXT, source_id TEXT, sequence_no INTEGER,
                    section_path_json TEXT, caption TEXT, unit_text TEXT, row_count INTEGER,
                    column_count INTEGER, parse_status TEXT, locator_json TEXT
                );
                CREATE TABLE table_cell(
                    evidence_id TEXT, table_id TEXT, source_id TEXT, filing_id TEXT,
                    row_index INTEGER, column_index INTEGER, rowspan INTEGER, colspan INTEGER,
                    cell_kind TEXT, row_header_path_json TEXT, column_header_path_json TEXT,
                    locator_json TEXT, text_raw TEXT, text_normalized TEXT, parser_version TEXT
                );
                """
            )
            connection.commit()
            connection.close()
            stat = database.stat()
            attestation = root / "attestation.json"
            attestation.write_text(
                json.dumps(
                    {
                        "database_version": "fixture-v1",
                        "database": {
                            "uncompressed_sha256": "b" * 64,
                            "uncompressed_bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "financial-universe-v1",
                        "source_database_sha256": "b" * 64,
                        "companies": [],
                    }
                ),
                encoding="utf-8",
            )
            candidates = root / "candidates.jsonl"
            rejects = root / "rejects.jsonl"
            summary = root / "summary.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                build_candidates_main(
                    [
                        "--database", str(database),
                        "--attestation", str(attestation),
                        "--manifest", str(manifest),
                        "--candidates", str(candidates),
                        "--rejects", str(rejects),
                        "--summary", str(summary),
                    ]
                )

            self.assertEqual(candidates.read_bytes(), b"")
            self.assertEqual(rejects.read_bytes(), b"")
            self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["candidate_count"], 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")

            bad_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            bad_manifest["source_database_sha256"] = "c" * 64
            manifest.write_text(json.dumps(bad_manifest), encoding="utf-8")
            refused = root / "refused.jsonl"
            with self.assertRaisesRegex(ValueError, "manifest source"):
                build_candidates_main(
                    [
                        "--database", str(database),
                        "--attestation", str(attestation),
                        "--manifest", str(manifest),
                        "--candidates", str(refused),
                        "--rejects", str(root / "refused-rejects.jsonl"),
                        "--summary", str(root / "refused-summary.json"),
                    ]
                )
            self.assertFalse(refused.exists())


if __name__ == "__main__":
    unittest.main()
