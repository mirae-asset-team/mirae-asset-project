from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_agent_gold import load_predicate_config
from scripts.build_agent_gold import extract_fact_candidates
from disclosure_db.schema import create_schema


SAFE_CONFIG = load_predicate_config(Path("config/agent_gold_predicates.json"))


def build_fixture_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    create_schema(connection)
    filing_id = "20240101000001"
    connection.execute(
        "INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (filing_id, "doc_fixture", "00000001", "000001", "테스트회사", "테스트회사", "테스트제출인", "IT", "IT",
         "exchange", "공급계약", "공급계약", "공급계약", "공급계약", "2024-01-01", None, None, 0, "xml", 1),
    )
    connection.execute(
        "INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("src_fixture", filing_id, "main", "fixture.xml", ".xml", "dart_xml", "utf-8", "utf-8", "a" * 64, 10,
         "2024-01-01T00:00:00Z", "fixture", "1", "success", 1, 0, "[]", "{}"),
    )
    connection.execute(
        "INSERT INTO filing_event VALUES(?,?,?,?,?)",
        ("evt_fixture", "00000001", "exchange", "fixture", "test"),
    )
    connection.execute(
        "INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)",
        (filing_id, "evt_fixture", 1, None, "root", "high", "2024-01-01", None, 1, "fixture"),
    )
    connection.execute(
        "INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("tbl_fixture", filing_id, "src_fixture", 0, "[]", "계약", "원", 1, 1, "success", "{}"),
    )
    connection.execute(
        "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ev_fixture", "tbl_fixture", "src_fixture", filing_id, 0, 0, 1, 1, "data", "[]", "[]", "{}",
         "1,000", "1,000", "1"),
    )
    connection.execute(
        "INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?)",
        ("fact_fixture", filing_id, "event_kv_candidate", "테스트회사", "계약금액", "1000", "원", "fixture", "candidate"),
    )
    connection.execute("INSERT INTO fact_evidence VALUES(?,?)", ("fact_fixture", "ev_fixture"))
    connection.commit()
    connection.close()
    return path


class AgentGoldTests(unittest.TestCase):
    def test_predicate_config_has_only_explicit_safe_predicates(self) -> None:
        config = load_predicate_config(Path("config/agent_gold_predicates.json"))
        self.assertEqual(config["version"], "0.1.0")
        self.assertGreaterEqual(
            {item["id"] for item in config["predicates"]},
            {"contract_amount", "counterparty", "issued_shares"},
        )
        self.assertTrue(all(item["answer_kind"] in {"numeric", "text"} for item in config["predicates"]))

    def test_fact_candidate_extraction_keeps_evidence_filing_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            before = hashlib.sha256(base.read_bytes()).hexdigest()
            rows = extract_fact_candidates(base, SAFE_CONFIG, limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filing_id"], rows[0]["evidence_filing_id"])
            self.assertEqual(rows[0]["evidence_id"], "ev_fixture")
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
