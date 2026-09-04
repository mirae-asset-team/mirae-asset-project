from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from disclosure_db.financial_universe import build_financial_universe, canonical_manifest_bytes
from disclosure_db.schema import create_schema
from scripts.build_financial_universe import main as build_universe_main


def _insert_filing(
    connection: sqlite3.Connection,
    filing_id: str,
    *,
    corp_code: str,
    stock_code: str,
    issuer_name: str,
    listed_name: str,
    report_name: str,
    filed_at: str,
    base_year: int,
    corrected: bool = False,
) -> None:
    connection.execute(
        """INSERT INTO filing(
               filing_id,doc_id,issuer_corp_code,stock_code,issuer_name,listed_name,reporter_name,
               industry,sector,doc_group,doc_subtype_raw,doc_subtype_normalized,
               report_name_raw,report_name_normalized,filed_at,base_year,base_month,is_correction,
               file_format_declared,source_file_count
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            filing_id,
            f"doc-{filing_id}",
            corp_code,
            stock_code,
            issuer_name,
            listed_name,
            issuer_name,
            "테스트산업",
            "테스트섹터",
            "periodic",
            "annual",
            "annual",
            ("[기재정정]" if corrected else "") + report_name,
            report_name,
            filed_at,
            base_year,
            12,
            int(corrected),
            "xml",
            1,
        ),
    )


def _insert_version(
    connection: sqlite3.Connection,
    filing_id: str,
    *,
    event_id: str,
    version_no: int,
    current: bool,
    status: str = "root",
    parent: str | None = None,
) -> None:
    if version_no == 1:
        corp_code = connection.execute(
            "SELECT issuer_corp_code FROM filing WHERE filing_id=?", (filing_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO filing_event(event_id,issuer_corp_code,doc_group,event_key,lineage_method) VALUES(?,?,?,?,?)",
            (event_id, corp_code, "periodic", event_id, "fixture"),
        )
    connection.execute(
        """INSERT INTO filing_version(
               filing_id,event_id,version_no,parent_filing_id,lineage_status,lineage_confidence,
               effective_from,effective_to,is_current,rationale)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            filing_id,
            event_id,
            version_no,
            parent,
            status,
            "high" if status in {"root", "resolved"} else "none",
            f"202{version_no + 3}-03-01",
            None if current else f"202{version_no + 3}-03-02",
            int(current),
            "fixture",
        ),
    )


def _insert_consolidated_table(connection: sqlite3.Connection, filing_id: str) -> None:
    source_id = f"src-{filing_id}"
    connection.execute(
        """INSERT INTO source_document(
               source_id,filing_id,role,source_path,extension,detected_format,declared_encoding,
               detected_encoding,sha256,byte_size,modified_at,parser_name,parser_version,
               parse_status,strict_xml_ok,parser_error_count,warnings_json,coverage_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source_id,
            filing_id,
            "main",
            f"raw/{filing_id}.xml",
            ".xml",
            "dart_xml",
            "utf-8",
            "utf-8",
            filing_id.ljust(64, "0")[:64],
            10,
            "2025-03-01T00:00:00Z",
            "fixture",
            "1",
            "success",
            1,
            0,
            "[]",
            "{}",
        ),
    )
    connection.execute(
        """INSERT INTO table_record(
               table_id,filing_id,source_id,sequence_no,section_path_json,caption,unit_text,
               row_count,column_count,parse_status,locator_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"tbl-{filing_id}",
            filing_id,
            source_id,
            1,
            json.dumps(["III. 재무에 관한 사항", "2. 연결재무제표"], ensure_ascii=False),
            "연결 손익계산서",
            "단위: 백만원",
            3,
            4,
            "success",
            "{}",
        ),
    )


class FinancialUniverseTests(unittest.TestCase):
    def test_selects_latest_current_correction_and_preserves_alias_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "base.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            _insert_filing(
                connection,
                "20240301000001",
                corp_code="00000001",
                stock_code="000001",
                issuer_name="테스트전자 주식회사",
                listed_name="테스트전자",
                report_name="사업보고서",
                filed_at="2024-03-01",
                base_year=2023,
            )
            _insert_version(connection, "20240301000001", event_id="evt-2023", version_no=1, current=True)
            _insert_filing(
                connection,
                "20250301000001",
                corp_code="00000001",
                stock_code="000001",
                issuer_name="테스트전자 주식회사",
                listed_name="테스트전자",
                report_name="사업보고서",
                filed_at="2025-03-01",
                base_year=2024,
            )
            _insert_version(connection, "20250301000001", event_id="evt-2024", version_no=1, current=False)
            _insert_filing(
                connection,
                "20250302000001",
                corp_code="00000001",
                stock_code="000001",
                issuer_name="테스트전자 주식회사",
                listed_name="테스트전자",
                report_name="사업보고서",
                filed_at="2025-03-02",
                base_year=2024,
                corrected=True,
            )
            _insert_version(
                connection,
                "20250302000001",
                event_id="evt-2024",
                version_no=2,
                current=True,
                status="resolved",
                parent="20250301000001",
            )
            _insert_consolidated_table(connection, "20250302000001")
            connection.commit()
            connection.close()

            manifest = build_financial_universe(database, source_database_sha256="a" * 64)

            self.assertEqual(manifest["company_count"], 1)
            self.assertEqual(manifest["searchable_alias_count"], 2)
            company = manifest["companies"][0]
            self.assertEqual(company["filing_id"], "20250302000001")
            self.assertEqual(company["original_filing_id"], "20250301000001")
            self.assertEqual(company["fiscal_year"], 2024)
            self.assertEqual(company["lineage_status"], "resolved")
            self.assertIsNone(company["consolidated_statement_present"])
            self.assertEqual(company["statement_scope_status"], "pending_extraction")
            self.assertEqual(company["aliases"], ["테스트전자", "테스트전자 주식회사"])

    def test_rejects_company_without_answer_safe_current_annual_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "base.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            _insert_filing(
                connection,
                "20250301000002",
                corp_code="00000002",
                stock_code="000002",
                issuer_name="불확실기업",
                listed_name="불확실기업",
                report_name="사업보고서",
                filed_at="2025-03-01",
                base_year=2024,
            )
            _insert_version(
                connection,
                "20250301000002",
                event_id="evt-unsafe",
                version_no=1,
                current=True,
                status="unresolved",
            )
            connection.commit()
            connection.close()

            manifest = build_financial_universe(database, source_database_sha256="b" * 64)

            self.assertEqual(manifest["company_count"], 0)
            self.assertEqual(manifest["source_company_count"], 1)
            self.assertEqual(
                manifest["rejects"],
                [{"issuer_corp_code": "00000002", "reason_code": "answer_safe_annual_report_missing"}],
            )

    def test_manifest_bytes_are_canonical_and_order_independent(self) -> None:
        payload = {
            "schema_version": "financial-universe-v1",
            "companies": [{"issuer_corp_code": "2"}, {"issuer_corp_code": "1"}],
            "rejects": [
                {"issuer_corp_code": "4", "reason_code": "z"},
                {"issuer_corp_code": "3", "reason_code": "a"},
            ],
        }
        first = canonical_manifest_bytes(payload)
        reversed_payload = dict(json.loads(first))
        reversed_payload["companies"] = list(reversed(reversed_payload["companies"]))
        reversed_payload["rejects"] = list(reversed(reversed_payload["rejects"]))
        second = canonical_manifest_bytes(reversed_payload)
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

    def test_cli_writes_manifest_and_digest_only_for_matching_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            connection.commit()
            connection.close()
            stat = database.stat()
            attestation = root / "attestation.json"
            attestation.write_text(
                json.dumps(
                    {
                        "database_version": "fixture-v1",
                        "database": {
                            "uncompressed_sha256": "c" * 64,
                            "uncompressed_bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = root / "universe.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                build_universe_main(
                    ["--database", str(database), "--attestation", str(attestation), "--output", str(output)]
                )

            payload = json.loads(output.read_text(encoding="utf-8"))
            summary = json.loads(stdout.getvalue())
            self.assertEqual(payload["source_database_sha256"], "c" * 64)
            self.assertEqual(summary["company_count"], 0)
            self.assertRegex(summary["manifest_sha256"], r"^[0-9a-f]{64}$")

            attestation_payload = json.loads(attestation.read_text(encoding="utf-8"))
            attestation_payload["database"]["mtime_ns"] += 1
            attestation.write_text(json.dumps(attestation_payload), encoding="utf-8")
            refused = root / "refused.json"
            with self.assertRaisesRegex(ValueError, "attestation"):
                build_universe_main(
                    ["--database", str(database), "--attestation", str(attestation), "--output", str(refused)]
                )
            self.assertFalse(refused.exists())

    def test_universe_build_does_not_scan_table_records_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "base.sqlite"
            setup = sqlite3.connect(database)
            create_schema(setup)
            for number in (1, 2):
                filing_id = f"2025030100000{number}"
                _insert_filing(
                    setup,
                    filing_id,
                    corp_code=f"{number:08d}",
                    stock_code=f"{number:06d}",
                    issuer_name=f"테스트기업{number}",
                    listed_name=f"테스트기업{number}",
                    report_name="사업보고서",
                    filed_at="2025-03-01",
                    base_year=2024,
                )
                _insert_version(setup, filing_id, event_id=f"evt-{number}", version_no=1, current=True)
                _insert_consolidated_table(setup, filing_id)
            setup.commit()
            setup.close()

            traced: list[str] = []
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            connection.set_trace_callback(traced.append)
            with patch("disclosure_db.financial_universe._readonly_connection", return_value=connection):
                manifest = build_financial_universe(database, source_database_sha256="d" * 64)

            table_record_queries = [statement for statement in traced if "FROM table_record" in statement]
            self.assertEqual(manifest["company_count"], 2)
            self.assertEqual(table_record_queries, [])


if __name__ == "__main__":
    unittest.main()
