from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.attestation import load_distribution_attestation
from disclosure_db.schema import create_schema
from disclosure_db.financial_overlay import FinancialOverlay, import_seed, fetch_overlay_facts, overlay_matches_base
from disclosure_db.agent import AgentSettings, DisclosureAgent


def seed_base(path: Path) -> None:
    connection = sqlite3.connect(path)
    create_schema(connection)
    connection.execute(
        """INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("f1", "doc_f1", "00000001", "000001", "테스트", "테스트", "제출인", "IT", "IT",
         "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-03-01", 2023, 12, 0, "xml", 1),
    )
    connection.execute(
        """INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("s1", "f1", "main", "f1.xml", ".xml", "dart_xml", "utf-8", "utf-8", "a" * 64, 10,
         "2024-03-01T00:00:00Z", "test", "1", "success", 1, 0, "[]", "{}"),
    )
    connection.execute(
        """INSERT INTO filing_event VALUES(?,?,?,?,?)""",
        ("e1", "00000001", "periodic", "f1", "test"),
    )
    connection.execute(
        """INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("f1", "e1", 1, None, "root", "high", "2024-03-01", None, 1, "test"),
    )
    connection.execute(
        """INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("t1", "f1", "s1", 0, "[]", "손익계산서", "원", 1, 1, "success", "{}"),
    )
    connection.execute(
        """INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("c1", "t1", "s1", "f1", 0, 0, 1, 1, "data", "[]", "[]", "{}", "매출액 1,000", "매출액 1,000", "1"),
    )
    connection.commit()
    connection.close()


class FinancialOverlayTests(unittest.TestCase):
    def test_import_validates_against_read_only_base_and_fetches_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay, seed = root / "base.sqlite", root / "overlay.sqlite", root / "seed.jsonl"
            seed_base(base)
            seed.write_text(json.dumps({
                "financial_fact_id": "ff1", "filing_id": "f1", "account_id": "revenue",
                "account_name_raw": "매출액", "statement_type": "IS", "scope": "consolidated",
                "period_type": "duration", "period_start": "2023-01-01", "period_end": "2023-12-31",
                "value_numeric": "1000", "currency": "KRW", "scale": 1, "unit_raw": "원",
                "extraction_method": "human_validated", "validation_status": "validated",
                "evidence_ids": ["c1"],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            before = hashlib.sha256(base.read_bytes()).hexdigest()
            result = import_seed(base, overlay, seed)
            self.assertEqual(result.imported, 1)
            self.assertEqual(result.rejected, 0)
            self.assertEqual(before, hashlib.sha256(base.read_bytes()).hexdigest())
            rows = fetch_overlay_facts(base, overlay, filing_id="f1")
            self.assertEqual(rows[0]["value_numeric"], "1000")
            self.assertEqual(rows[0]["evidence_ids"], ["c1"])
            self.assertEqual(rows[0]["evidence_texts"], ["매출액 1,000"])
            answer = DisclosureAgent(AgentSettings(base, overlay)).answer("테스트 매출액은 얼마인가?", company="테스트")
            self.assertTrue(answer.verified)
            self.assertEqual(answer.numeric_values, ["1000"])

    def test_cross_filing_or_candidate_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay, seed = root / "base.sqlite", root / "overlay.sqlite", root / "seed.jsonl"
            seed_base(base)
            seed.write_text(json.dumps({
                "financial_fact_id": "bad", "filing_id": "f1", "account_name_raw": "매출액",
                "statement_type": "IS", "scope": "consolidated", "period_type": "duration",
                "period_start": "2023-01-01", "period_end": "2023-12-31", "value_numeric": "not-a-number",
                "scale": 1, "extraction_method": "model", "validation_status": "candidate",
                "evidence_ids": ["missing"],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            result = import_seed(base, overlay, seed)
            self.assertEqual(result.imported, 0)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(fetch_overlay_facts(base, overlay), [])

    def test_overlay_rejects_a_different_immutable_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, other, overlay, seed = root / "base.sqlite", root / "other.sqlite", root / "overlay.sqlite", root / "seed.jsonl"
            seed_base(base)
            seed_base(other)
            with other.open("ab") as stream:
                stream.write(b"different-base")
            seed.write_text("", encoding="utf-8")
            import_seed(base, overlay, seed)
            with self.assertRaisesRegex(ValueError, "does not match"):
                import_seed(other, overlay, seed)

    def test_overlay_blocks_pdf_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay, seed = root / "base.sqlite", root / "overlay.sqlite", root / "seed.jsonl"
            seed_base(base)
            connection = sqlite3.connect(base)
            connection.execute("UPDATE source_document SET detected_format='pdf' WHERE source_id='s1'")
            connection.commit()
            connection.close()
            seed.write_text(json.dumps({
                "financial_fact_id": "pdf-fact", "filing_id": "f1", "account_name_raw": "매출액",
                "statement_type": "IS", "scope": "consolidated", "period_type": "duration",
                "period_start": "2023-01-01", "period_end": "2023-12-31", "value_numeric": "1000",
                "scale": 1, "extraction_method": "human_validated", "validation_status": "validated",
                "evidence_ids": ["c1"],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            result = import_seed(base, overlay, seed)
            self.assertEqual(result.imported, 0)
            self.assertIn("visual evidence is blocked", result.reasons[0])

    def test_attested_overlay_match_never_hashes_request_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay, seed, manifest = (root / name for name in ("base.sqlite", "overlay.sqlite", "seed.jsonl", "manifest.json"))
            seed_base(base)
            seed.write_text("", encoding="utf-8")
            import_seed(base, overlay, seed)
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {
                    "uncompressed_bytes": base.stat().st_size,
                    "uncompressed_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                },
            }), encoding="utf-8")
            attestation = load_distribution_attestation(manifest, database=base)
            with patch("disclosure_db.financial_overlay.sha256_file", side_effect=AssertionError("request hash")):
                self.assertTrue(overlay_matches_base(base, overlay, attestation=attestation))


if __name__ == "__main__":
    unittest.main()
