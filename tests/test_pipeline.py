from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from disclosure_db.identifiers import evidence_id, normalize_report_name
from disclosure_db.pipeline import build_database, query_database


XML = """<?xml version='1.0' encoding='UTF-8'?>
<DOCUMENT><SECTION-1><TITLE>I. 개요</TITLE><P>본문 & 잘못된 엔티티</P>
<TABLE><TR><TH ROWSPAN='2'>항목</TH><TH COLSPAN='2'>금액</TH></TR>
<TR><TH>당기</TH><TH>전기</TH></TR><TR><TD>매출액</TD><TD>100</TD><TD>90</TD></TR></TABLE>
</SECTION-1></DOCUMENT>"""

EXCHANGE_HTML = """<html><body><table><tr><td><span>계약금액(원)</span></td>
<td><span class='xforms_input'>1,000</span></td></tr></table></body></html>"""

TD_HEADER_XML = """<?xml version='1.0' encoding='UTF-8'?>
<DOCUMENT><SECTION-1><TITLE>주요사항</TITLE><P>2025년 12월 31일 현재 (단위 : 백만원)</P>
<TABLE><TR><TD ROWSPAN='2'>매출액</TD><TD>100</TD></TR><TR><TD>90</TD></TR></TABLE>
</SECTION-1></DOCUMENT>"""


class PipelineTests(unittest.TestCase):
    def test_ids_are_deterministic_and_locator_sensitive(self) -> None:
        locator = {"kind": "table_cell", "table": 0, "row": 1, "column": 2}
        first = evidence_id("20240101000001", "a" * 64, "cell", locator)
        second = evidence_id("20240101000001", "a" * 64, "cell", dict(reversed(list(locator.items()))))
        other = evidence_id("20240101000001", "a" * 64, "cell", {**locator, "column": 3})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_report_name_normalization_only_removes_display_prefix(self) -> None:
        self.assertEqual(normalize_report_name("[기재정정] 사업보고서 (2024.12)"), "사업보고서(2024.12)")

    def test_end_to_end_recovery_table_grid_lineage_and_fts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "corpus"
            original_dir = root / "raw" / "periodic" / "테스트" / "20240301000001_annual_2023_12"
            correction_dir = root / "raw" / "periodic" / "테스트" / "20240302000002_annual_2023_12"
            original_dir.mkdir(parents=True)
            correction_dir.mkdir(parents=True)
            (original_dir / "20240301000001.xml").write_text(XML, encoding="utf-8")
            (correction_dir / "20240302000002.xml").write_text(XML.replace("100", "110"), encoding="utf-8")
            rows = []
            for receipt, directory, corrected in [
                ("20240301000001", original_dir, False),
                ("20240302000002", correction_dir, True),
            ]:
                rows.append(
                    {
                        "doc_id": f"periodic_{receipt}", "corp_code": "00000001", "corp_name": "테스트",
                        "listed_name": "테스트", "stock_code": "000001", "industry": "IT", "sector": "테스트",
                        "doc_group": "periodic", "doc_subtype": "annual", "report_nm": ("[기재정정]" if corrected else "") + "사업보고서 (2023.12)",
                        "is_correction": corrected, "rcept_no": receipt, "rcept_dt": receipt[:8], "flr_nm": "테스트",
                        "base_year": 2023, "base_month": 12, "file_path": directory.relative_to(root).as_posix(),
                        "file_format": "xml", "n_files": 1,
                    }
                )
            (root / "manifest.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            database = Path(temp) / "test.sqlite"
            stats = build_database(root, database, progress_every=99)
            self.assertTrue(stats["structure_gate_passed"])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM filing").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT count(*) FROM table_cell").fetchone()[0], 14)
                self.assertEqual(connection.execute("SELECT max(column_count) FROM table_record").fetchone()[0], 3)
                header_path = connection.execute(
                    "SELECT column_header_path_json FROM table_cell WHERE text_normalized='100' LIMIT 1"
                ).fetchone()[0]
                self.assertIn("당기", json.loads(header_path))
                self.assertEqual(connection.execute("SELECT count(*) FROM filing_version WHERE lineage_status='resolved'").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM source_document WHERE strict_xml_ok=0").fetchone()[0], 2)
                self.assertGreater(connection.execute("SELECT count(*) FROM parser_error WHERE line IS NOT NULL").fetchone()[0], 0)
            results = query_database(database, "매출액", company="000001", limit=5)
            self.assertGreaterEqual(len(results), 1)
            self.assertTrue(all(result["issuer_name"] == "테스트" for result in results))
            self.assertEqual(query_database(database, "매출액", company="없는회사", limit=5), [])

    def test_exchange_td_header_is_derived_from_xforms_input(self) -> None:
        from disclosure_db.identifiers import source_id
        from disclosure_db.parsers import parse_markup

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exchange.xml"
            path.write_text(EXCHANGE_HTML, encoding="utf-8")
            result = parse_markup(
                path,
                filing_id="20240101000001",
                source_id=source_id("20240101000001", "b" * 64, "main"),
                source_sha256="b" * 64,
                detected_format="exchange_html",
                issuer_name="테스트",
                doc_group="exchange",
            )
            value = next(cell for cell in result.cells if cell.text_normalized == "1,000")
            self.assertEqual(value.cell_kind, "data")
            self.assertEqual(value.row_header_path, ["계약금액(원)"])
            self.assertEqual([fact.fact_type for fact in result.facts], ["event_kv_candidate"])

    def test_dart_td_label_rowspan_and_unit_are_preserved(self) -> None:
        from disclosure_db.identifiers import source_id
        from disclosure_db.parsers import parse_markup

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "major.xml"
            path.write_text(TD_HEADER_XML, encoding="utf-8")
            result = parse_markup(
                path,
                filing_id="20240101000001",
                source_id=source_id("20240101000001", "c" * 64, "main"),
                source_sha256="c" * 64,
                detected_format="dart_xml",
                issuer_name="테스트",
                doc_group="major",
            )
            label = next(cell for cell in result.cells if cell.text_normalized == "매출액")
            second_row_value = next(cell for cell in result.cells if cell.text_normalized == "90")
            self.assertEqual(label.cell_kind, "header")
            self.assertEqual(second_row_value.row_header_path, ["매출액"])
            self.assertEqual(result.tables[0].unit_text, "백만원")


if __name__ == "__main__":
    unittest.main()
