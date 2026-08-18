from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_agent_gold import load_predicate_config
from scripts.build_agent_gold import AuditResult, audit_candidate, extract_fact_candidates, main as build_agent_gold_main, write_jsonl_atomic
from disclosure_db.schema import create_schema
from disclosure_db.gold_validation import validate_record_contract, validate_record_schema


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


def valid_text_record() -> dict[str, object]:
    filing_id = "20240101000001"
    return {
        "schema_version": "0.1.0",
        "question_id": "fixture_counterparty",
        "question_type": "single_filing_fact",
        "question": "테스트회사의 계약상대는 누구인가?",
        "answerability": "answerable",
        "answer": {"kind": "text", "text": "상대회사"},
        "answer_origin": "model_generated",
        "candidate_filing_ids": [filing_id],
        "company_resolution": {"query_name": "테스트회사", "issuer_name": "테스트회사", "corp_code": "00000001", "stock_code": "000001"},
        "period": {"period_type": "event_date", "start_date": None, "end_date": None, "instant_date": "2024-01-01"},
        "scope": "not_applicable",
        "as_of": "2024-01-01",
        "version_basis": "as_of",
        "formula": None,
        "attack_label": None,
        "evidence": [{
            "evidence_id": "ev_fixture", "filing_id": filing_id, "source_sha256": "a" * 64,
            "locator": {"kind": "table_cell", "table": 0, "row": 0, "column": 0}, "role": "support",
            "lineage_status": "root", "is_current": True, "effective_from": "2024-01-01", "effective_to": None,
            "source_format": "xml", "table_structure_status": "parsed_unreviewed", "table_id": "tbl_fixture",
            "cell_evidence_id": "ev_fixture", "unit": None, "scale": None,
        }],
        "version_evidence": [{
            "filing_id": filing_id, "event_id": "evt_fixture", "parent_filing_id": None,
            "lineage_status": "root", "lineage_confidence": "high", "is_current": True,
            "effective_from": "2024-01-01", "effective_to": None, "rationale": "fixture",
        }],
        "source_evidence": [{
            "source_id": "src_fixture", "filing_id": filing_id, "sha256": "a" * 64,
            "detected_format": "dart_xml", "parse_status": "success", "fragment_count": 0,
            "table_count": 1, "cell_count": 1, "table_structure_status": "parsed_unreviewed",
        }],
        "review": {"status": "candidate", "annotator": "fixture", "reviewer": None, "reviewed_at": None, "notes": ""},
    }


class AgentGoldTests(unittest.TestCase):
    def test_nested_contract_validation_rejects_malformed_shapes_without_raising(self) -> None:
        record = valid_text_record()
        record["review"] = "not-an-object"
        issues = validate_record_contract(record)
        self.assertTrue(issues)
        self.assertTrue(any(item["rule_id"] == "canonical_schema" for item in issues))

    def test_nested_schema_rejects_unknown_fields_and_partial_evidence(self) -> None:
        record = valid_text_record()
        record["answer"]["unexpected"] = True
        record["evidence"][0].pop("locator")
        issues = validate_record_schema(record)
        messages = {item["message"] for item in issues}
        self.assertTrue(any("answer.unexpected" in message for message in messages))
        self.assertTrue(any("evidence[0].locator" in message for message in messages))

    def test_schema_validation_handles_huge_numeric_values_without_raising(self) -> None:
        record = valid_text_record()
        record["formula"] = {
            "expression": "x",
            "operand_evidence_ids": [],
            "expected_result": 10**1000,
            "unit": None,
            "scale": None,
        }
        self.assertTrue(validate_record_schema(record))

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

    def test_valid_text_candidate_is_agent_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            result = audit_candidate(valid_text_record(), base, source_sha256="b" * 64)
            self.assertIsInstance(result, AuditResult)
            self.assertEqual(result.status, "agent_audited")
            self.assertEqual(result.reason_codes, [])
            self.assertEqual(result.record["review"]["status"], "agent_audited")

    def test_agent_audited_contract_is_not_human_approval(self) -> None:
        record = valid_text_record()
        record["review"]["status"] = "agent_audited"
        self.assertEqual(validate_record_contract(record), [])
        record["review"].update({"status": "approved", "reviewer": "fixture", "reviewed_at": "2024-01-02T00:00:00+00:00"})
        issues = validate_record_contract(record)
        self.assertTrue(any(item["rule_id"] == "approved_answer_not_human" for item in issues))

    def test_missing_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            record = valid_text_record()
            record["evidence"][0]["evidence_id"] = "missing"
            result = audit_candidate(record, base, source_sha256="b" * 64)
            self.assertEqual(result.status, "rejected")
            self.assertIn("evidence_not_found", result.reason_codes)

    def test_cross_filing_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            record = valid_text_record()
            record["candidate_filing_ids"] = ["20240101000002"]
            result = audit_candidate(record, base, source_sha256="b" * 64)
            self.assertIn("cross_filing_evidence", result.reason_codes)

    def test_unresolved_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            connection = sqlite3.connect(base)
            try:
                connection.execute("UPDATE filing_version SET lineage_status='unresolved' WHERE filing_id=?", ("20240101000001",))
                connection.commit()
            finally:
                connection.close()
            result = audit_candidate(valid_text_record(), base, source_sha256="b" * 64)
            self.assertIn("lineage_not_answer_safe", result.reason_codes)

    def test_pdf_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            connection = sqlite3.connect(base)
            try:
                connection.execute("UPDATE source_document SET detected_format='pdf', extension='.pdf' WHERE source_id=?", ("src_fixture",))
                connection.commit()
            finally:
                connection.close()
            result = audit_candidate(valid_text_record(), base, source_sha256="b" * 64)
            self.assertIn("visual_evidence_blocked", result.reason_codes)

    def test_nonfinite_numeric_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            record = valid_text_record()
            record["answer"] = {"kind": "numeric", "value": "NaN", "unit": "원", "scale": 1}
            result = audit_candidate(record, base, source_sha256="b" * 64)
            self.assertIn("numeric_not_decimal", result.reason_codes)

    def test_duplicate_evidence_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = build_fixture_database(Path(temp) / "base.sqlite")
            record = valid_text_record()
            record["evidence"].append(dict(record["evidence"][0]))
            result = audit_candidate(record, base, source_sha256="b" * 64)
            self.assertIn("duplicate_candidate", result.reason_codes)

    def test_atomic_jsonl_writer_replaces_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "nested" / "rows.jsonl"
            write_jsonl_atomic(output, [{"b": 2, "a": 1}])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"a": 1, "b": 2})

    def test_cli_outputs_jsonl_summary_and_rejects_with_input_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = build_fixture_database(root / "base.sqlite")
            gold = root / "gold.jsonl"
            human = valid_text_record()
            human["answer_origin"] = "human_verified"
            human["review"] = {"status": "approved", "annotator": "fixture", "reviewer": "reviewer", "reviewed_at": "2024-01-02T00:00:00+00:00", "notes": "fixture"}
            gold.write_text(json.dumps(human, ensure_ascii=False) + "\n", encoding="utf-8")
            seed = root / "seed.jsonl"
            seed.write_text("", encoding="utf-8")
            output, summary, rejects = root / "out.jsonl", root / "summary.json", root / "rejects.jsonl"
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            build_agent_gold_main([
                "--database", str(base), "--gold", str(gold), "--overlay-seed", str(seed),
                "--output", str(output), "--summary", str(summary), "--rejects", str(rejects),
                "--expected-base-sha256", base_hash,
            ])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreaterEqual(len(rows), 1)
            self.assertTrue(all(row["audit"]["base_sha256"] == base_hash for row in rows))
            summary_row = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(summary_row["status"], "ok")
            self.assertEqual(summary_row["audited_count"], sum(row["review"]["status"] == "agent_audited" for row in rows))
            for line in rejects.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.assertTrue(json.loads(line)["reason_codes"])

    def test_audit_docs_record_generator_and_release_boundary(self) -> None:
        audit = Path("docs/gold-set-audit.md").read_text(encoding="utf-8")
        development = Path("docs/development-log.md").read_text(encoding="utf-8")
        for text in ("Quick start", "Dataset states", "Gate decision table", "Reject ledger", "Evidence → Finding → Path", "Release boundary"):
            self.assertIn(text, audit)
        self.assertIn("build_agent_gold.py", audit)
        self.assertIn("agent_audited", audit)
        self.assertIn("human_verified", audit)
        self.assertIn("b882f19", development)
        self.assertIn("gold_qa.agent_audited.jsonl", development)


if __name__ == "__main__":
    unittest.main()
