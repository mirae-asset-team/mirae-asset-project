from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from disclosure_db.attestation import load_distribution_attestation
from disclosure_db.schema import create_schema
from disclosure_db.financial_overlay import (
    FinancialOverlay,
    build_agent_overlay,
    fetch_event_facts,
    import_seed,
    fetch_overlay_facts,
    overlay_matches_base,
)
from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.api import _fetch_financial_facts
from disclosure_db.cli import agent_main


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
    connection.execute(
        "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c2", "t1", "s1", "f1", 1, 0, 1, 1, "data", "[]", "[]", "{}", "계약금액 2,000원", "계약금액 2,000원", "1"),
    )
    connection.execute(
        "INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?)",
        ("fact1", "f1", "event_kv_candidate", "테스트", "계약금액", "2000", "원", "parser", "candidate"),
    )
    connection.execute("INSERT INTO fact_evidence VALUES('fact1','c2')")
    connection.commit()
    connection.close()


def seed_composite_event_base(path: Path, *, ambiguous: bool = False) -> str:
    seed_base(path)
    numeric_evidence_id = "event_numeric_cell"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("t2", "f1", "s1", 1, "[]", "계약내역", None, 2, 3 if ambiguous else 2, "success", "{}"),
        )
        cells = [
            ("event_header_label", "t2", "s1", "f1", 0, 0, 1, 1, "header", "[]", "[]", "{}", "2. 계약내역", "2. 계약내역", "1"),
            ("event_header_amount", "t2", "s1", "f1", 0, 1, 1, 1, "header", "[]", "[]", "{}", "계약금액(원)", "계약금액(원)", "1"),
            ("event_label", "t2", "s1", "f1", 1, 0, 1, 1, "data", "[]", "[]", "{}", "계약금액", "계약금액", "1"),
            (numeric_evidence_id, "t2", "s1", "f1", 1, 1, 1, 1, "data", "[]", "[]", "{}", "240,993,039,040", "240,993,039,040", "1"),
        ]
        if ambiguous:
            cells.append(
                ("event_numeric_cell_2", "t2", "s1", "f1", 1, 2, 1, 1, "data", "[]", "[]", "{}", "2,000", "2,000", "1")
            )
        connection.executemany(
            "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cells
        )
        connection.execute(
            "UPDATE fact SET value_raw=?, unit=? WHERE fact_id='fact1'",
            ("2. 계약내역 | 계약금액(원) | 240,993,039,040", None),
        )
        connection.execute("DELETE FROM fact_evidence WHERE fact_id='fact1'")
        connection.execute("INSERT INTO fact_evidence VALUES('fact1',?)", ("event_label",))
        connection.commit()
    return numeric_evidence_id


class FinancialOverlayTests(unittest.TestCase):
    def test_agent_overlay_resolves_numeric_value_from_labeled_sibling_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            numeric_evidence_id = seed_composite_event_base(base)
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            rows = fetch_event_facts(base, overlay, company="테스트", predicate_terms=["계약금액"])
            self.assertEqual(result.event_imported, 1)
            self.assertEqual(rows[0]["value_numeric"], "240993039040")
            self.assertEqual(rows[0]["unit"], "원")
            self.assertEqual(rows[0]["evidence_ids"], [numeric_evidence_id])

    def test_agent_overlay_rejects_ambiguous_numeric_sibling_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_composite_event_base(base, ambiguous=True)
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 0)
            self.assertIn("event_numeric_cell_ambiguous", result.reasons[0])

    def test_agent_overlay_imports_allowlisted_event_fact_with_trust_tier(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base.sqlite"
            overlay = root / "agent.sqlite"
            seed = root / "financial.jsonl"
            predicates = root / "predicates.json"
            seed_base(base)
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({
                "predicates": [{
                    "id": "contract_amount",
                    "answer_kind": "numeric",
                    "allowed_fact_types": ["event_kv_candidate"],
                    "predicate_values": ["계약금액"],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 1)
            rows = fetch_event_facts(base, overlay, company="테스트", predicate_terms=["계약금액"])
            self.assertEqual(rows[0]["value_numeric"], "2000")
            self.assertEqual(rows[0]["trust_tier"], "agent_audited")
            self.assertEqual(rows[0]["evidence_ids"], ["c2"])

    def test_agent_overlay_text_event_keeps_value_cell_citation_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute(
                    "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("label", "t1", "s1", "f1", 2, 0, 1, 1, "data", "[]", "[]", "{}", "계약상대", "계약상대", "1"),
                )
                connection.execute(
                    "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("value", "t1", "s1", "f1", 2, 1, 1, 1, "data", "[]", "[]", "{}", "상대회사", "상대회사", "1"),
                )
                connection.execute("UPDATE fact SET predicate='계약상대', value_raw='상대회사' WHERE fact_id='fact1'")
                connection.execute("DELETE FROM fact_evidence WHERE fact_id='fact1'")
                connection.executemany("INSERT INTO fact_evidence VALUES('fact1',?)", [("label",), ("value",)])
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "counterparty", "answer_kind": "text",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약상대"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 1)
            rows = fetch_event_facts(base, overlay, company="테스트", predicate_terms=["계약상대"])
            self.assertEqual(rows[0]["evidence_ids"], ["value"])

    def test_agent_overlay_rejects_disallowed_event_validation_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute("UPDATE fact SET validation_status='rejected' WHERE fact_id='fact1'")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 0)
            with closing(sqlite3.connect(overlay)) as connection:
                self.assertEqual(
                    connection.execute("SELECT reason_code FROM build_reject").fetchone()[0],
                    "event_validation_status_invalid",
                )

    def test_agent_overlay_rejects_missing_event_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute("DELETE FROM fact_evidence WHERE fact_id='fact1'")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 0)
            self.assertIn("event_evidence_missing", result.reasons[0])

    def test_agent_overlay_rejects_cross_filing_event_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute("""INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    "f2", "doc_f2", "00000001", "000001", "테스트", "테스트", "제출인", "IT", "IT",
                    "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-04-01", 2023, 12, 0, "xml", 1,
                ))
                connection.execute("""INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    "s2", "f2", "main", "f2.xml", ".xml", "dart_xml", "utf-8", "utf-8", "b" * 64, 10,
                    "2024-04-01T00:00:00Z", "test", "1", "success", 1, 0, "[]", "{}",
                ))
                connection.execute("INSERT INTO filing_event VALUES(?,?,?,?,?)", ("e2", "00000001", "periodic", "f2", "test"))
                connection.execute("INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    "f2", "e2", 1, None, "root", "high", "2024-04-01", None, 1, "test",
                ))
                connection.execute("INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    "t2", "f2", "s2", 0, "[]", "계약", "원", 1, 1, "success", "{}",
                ))
                connection.execute("INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    "c3", "t2", "s2", "f2", 0, 0, 1, 1, "data", "[]", "[]", "{}", "계약금액 2,000원", "계약금액 2,000원", "1",
                ))
                connection.execute("DELETE FROM fact_evidence WHERE fact_id='fact1'")
                connection.execute("DELETE FROM fact WHERE fact_id='fact1'")
                connection.execute("INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?)", (
                    "fact1", "f2", "event_kv_candidate", "테스트", "계약금액", "2000", "원", "parser", "candidate",
                ))
                connection.execute("INSERT INTO fact_evidence VALUES('fact1','c3')")
                connection.execute("UPDATE fact SET filing_id='f1' WHERE fact_id='fact1'")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 0)
            self.assertIn("event_cross_filing_evidence", result.reasons[0])

    def test_agent_overlay_keeps_safe_historical_event_version_for_as_of_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute("UPDATE filing_version SET is_current=0 WHERE filing_id='f1'")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 1)
            self.assertEqual(fetch_event_facts(base, overlay), [])
            rows = fetch_event_facts(base, overlay, as_of="2024-03-02")
            self.assertEqual(rows[0]["filing_id"], "f1")

    def test_agent_overlay_sorts_event_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute("INSERT INTO fact_evidence VALUES('fact1','c1')")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            build_agent_overlay(base, overlay, seed, predicates)
            rows = fetch_event_facts(base, overlay)
            self.assertEqual(rows[0]["evidence_ids"], ["c1", "c2"])

    def test_agent_overlay_audits_numeric_and_evidence_rejections(self) -> None:
        cases = (
            ("not-a-number", "원", "event_numeric_not_decimal"),
            ("NaN", "원", "event_numeric_not_finite"),
            ("2000", None, "event_numeric_unit_missing"),
            ("2000", "원/달러", "event_numeric_unit_ambiguous"),
        )
        for value_raw, unit, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                base, overlay = root / "base.sqlite", root / "agent.sqlite"
                seed, predicates = root / "financial.jsonl", root / "predicates.json"
                seed_base(base)
                with closing(sqlite3.connect(base)) as connection:
                    connection.execute("UPDATE fact SET value_raw=?,unit=? WHERE fact_id='fact1'", (value_raw, unit))
                    connection.commit()
                seed.write_text("", encoding="utf-8")
                predicates.write_text(json.dumps({"predicates": [{
                    "id": "contract_amount", "answer_kind": "numeric",
                    "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
                }]}, ensure_ascii=False), encoding="utf-8")
                result = build_agent_overlay(base, overlay, seed, predicates)
                self.assertEqual(result.event_imported, 0)
                self.assertTrue(any(reason in item for item in result.reasons))
                with closing(sqlite3.connect(overlay)) as connection:
                    self.assertEqual(connection.execute("SELECT reason_code FROM build_reject").fetchone()[0], reason)
                self.assertEqual(fetch_event_facts(base, overlay), [])

    def test_agent_overlay_audits_pdf_parse_and_lineage_rejections(self) -> None:
        cases = (("source_document", "parse_status", "failed", "event_parse_failure"),
                 ("source_document", "detected_format", "pdf", "event_pdf_evidence"),
                 ("filing_version", "lineage_status", "unresolved", "event_unsafe_lineage"))
        for table, column, value, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                base, overlay = root / "base.sqlite", root / "agent.sqlite"
                seed, predicates = root / "financial.jsonl", root / "predicates.json"
                seed_base(base)
                with closing(sqlite3.connect(base)) as connection:
                    connection.execute(f"UPDATE {table} SET {column}=?", (value,))
                    connection.commit()
                seed.write_text("", encoding="utf-8")
                predicates.write_text(json.dumps({"predicates": [{
                    "id": "contract_amount", "answer_kind": "numeric",
                    "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
                }]}, ensure_ascii=False), encoding="utf-8")
                result = build_agent_overlay(base, overlay, seed, predicates)
                self.assertEqual(result.event_imported, 0)
                with closing(sqlite3.connect(overlay)) as connection:
                    self.assertEqual(connection.execute("SELECT reason_code FROM build_reject").fetchone()[0], reason)

    def test_agent_overlay_conflict_rejects_all_values_and_keeps_live_file_on_config_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "agent.sqlite"
            seed, predicates = root / "financial.jsonl", root / "predicates.json"
            seed_base(base)
            with closing(sqlite3.connect(base)) as connection:
                connection.execute(
                    "INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?)",
                    ("fact2", "f1", "event_kv_candidate", "테스트", "계약금액", "3000", "원", "parser", "candidate"),
                )
                connection.execute("INSERT INTO fact_evidence VALUES('fact2','c1')")
                connection.commit()
            seed.write_text("", encoding="utf-8")
            predicates.write_text(json.dumps({"predicates": [{
                "id": "contract_amount", "answer_kind": "numeric",
                "allowed_fact_types": ["event_kv_candidate"], "predicate_values": ["계약금액"],
            }]}, ensure_ascii=False), encoding="utf-8")
            result = build_agent_overlay(base, overlay, seed, predicates)
            self.assertEqual(result.event_imported, 0)
            with closing(sqlite3.connect(overlay)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM build_reject WHERE reason_code='event_fact_conflict'").fetchone()[0], 2)
            before = overlay.read_bytes()
            with self.assertRaises(FileNotFoundError):
                build_agent_overlay(base, overlay, seed, root / "missing-predicates.json")
            self.assertEqual(overlay.read_bytes(), before)

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
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {
                    "uncompressed_bytes": base.stat().st_size,
                    "uncompressed_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                    "mtime_ns": base.stat().st_mtime_ns,
                },
            }), encoding="utf-8")
            answer = DisclosureAgent(AgentSettings(base, overlay, attestation_path=manifest)).answer("테스트 매출액은 얼마인가?", company="테스트")
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
                    "mtime_ns": base.stat().st_mtime_ns,
                },
            }), encoding="utf-8")
            attestation = load_distribution_attestation(manifest, database=base)
            with patch("disclosure_db.financial_overlay.sha256_file", side_effect=AssertionError("request hash")):
                self.assertTrue(overlay_matches_base(base, overlay, attestation=attestation))

    def test_api_attested_financial_facts_never_hashes_request_database(self) -> None:
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
                    "mtime_ns": base.stat().st_mtime_ns,
                },
            }), encoding="utf-8")
            agent = DisclosureAgent(AgentSettings(
                base_database=base,
                overlay_database=overlay,
                attestation_path=manifest,
                use_hcx=False,
            ))
            with patch("disclosure_db.financial_overlay.sha256_file", side_effect=AssertionError("request hash")):
                facts = _fetch_financial_facts(agent.evidence_service, limit=1)
            self.assertEqual(facts, [])

    def test_runtime_agent_requires_attestation_when_overlay_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "overlay.sqlite"
            seed_base(base)
            overlay.write_bytes(b"overlay")
            with self.assertRaisesRegex(ValueError, "attestation is required"):
                DisclosureAgent(AgentSettings(base, overlay))

    def test_api_overlay_without_attestation_returns_empty_without_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "overlay.sqlite"
            seed_base(base)
            overlay.write_bytes(b"overlay")

            class Service:
                base_database = base
                overlay_database = overlay
                attestation = None

            with patch("disclosure_db.financial_overlay.sha256_file", side_effect=AssertionError("request hash")):
                self.assertEqual(_fetch_financial_facts(Service(), limit=1), [])

    def test_cli_agent_query_requires_attestation_before_any_base_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, overlay = root / "base.sqlite", root / "overlay.sqlite"
            base.write_bytes(b"fixture")
            overlay.write_bytes(b"overlay")
            argv = [
                "disclosure-agent", "agent-query",
                "--database", str(base), "--overlay", str(overlay),
                "--question", "테스트 질문",
            ]
            with patch.object(sys, "argv", argv), patch(
                "disclosure_db.financial_overlay.sha256_file", side_effect=AssertionError("request hash")
            ), self.assertRaisesRegex(SystemExit, "attestation is required"):
                agent_main()


if __name__ == "__main__":
    unittest.main()
