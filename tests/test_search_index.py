from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.attestation import CorpusAttestation
from disclosure_db.agent_contracts import QueryPlan
from disclosure_db.evidence_service import EvidenceService
from disclosure_db.schema import create_schema
from disclosure_db.search_index import SafeSearchIndex, build_search_index


def _seed_base(path: Path) -> None:
    connection = sqlite3.connect(path)
    create_schema(connection)
    filing_values = [
        ("f_safe", "doc_safe", "00000001", "000001", "테스트", "테스트", "테스트", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-01-01", 2023, 12, 0, "xml", 1),
        ("f_unresolved", "doc_unresolved", "00000002", "000002", "테스트", "테스트", "테스트", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-01-02", 2023, 12, 0, "xml", 1),
        ("f_pdf", "doc_pdf", "00000003", "000003", "테스트", "테스트", "테스트", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-01-03", 2023, 12, 0, "pdf", 1),
    ]
    connection.executemany("INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", filing_values)
    source_values = [
        ("s_safe", "f_safe", "main", "safe.xml", ".xml", "dart_xml", "utf-8", "utf-8", "a" * 64, 10, "2024-01-01T00:00:00Z", "test", "1", "success", 1, 0, "[]", json.dumps({"image_reference_count": 0})),
        ("s_unresolved", "f_unresolved", "main", "unresolved.xml", ".xml", "dart_xml", "utf-8", "utf-8", "b" * 64, 10, "2024-01-02T00:00:00Z", "test", "1", "success", 1, 0, "[]", json.dumps({"image_reference_count": 0})),
        ("s_pdf", "f_pdf", "pdf_main", "pdf.pdf", ".pdf", "pdf", None, None, "c" * 64, 10, "2024-01-03T00:00:00Z", "test", "1", "success", None, 0, "[]", json.dumps({"image_reference_count": 0})),
    ]
    connection.executemany("INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", source_values)
    connection.executemany("INSERT INTO filing_event VALUES(?,?,?,?,?)", [("e_safe", "00000001", "periodic", "safe", "test"), ("e_unresolved", "00000002", "periodic", "unresolved", "test"), ("e_pdf", "00000003", "periodic", "pdf", "test")])
    connection.executemany("INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("f_safe", "e_safe", 1, None, "root", "high", "2024-01-01", None, 1, "test"),
        ("f_unresolved", "e_unresolved", 1, None, "unresolved", "none", "2024-01-02", None, 1, "test"),
        ("f_pdf", "e_pdf", 1, None, "root", "high", "2024-01-03", None, 1, "test"),
    ])
    connection.executemany("INSERT INTO fragment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("ev_safe", "f_safe", "s_safe", "heading", 0, "[]", None, None, "{}", "발행주식수는 100주 계약금액", "발행주식수는 100주 계약금액", "1"),
        ("ev_unresolved", "f_unresolved", "s_unresolved", "heading", 0, "[]", None, None, "{}", "계약금액 unsafe", "계약금액 unsafe", "1"),
        ("ev_pdf", "f_pdf", "s_pdf", "paragraph", 0, "[]", 1, None, "{}", "계약금액 PDF", "계약금액 PDF", "1"),
    ])
    connection.commit()
    connection.close()


class SearchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.base = root / "base.sqlite"
        self.index = root / "search.sqlite"
        _seed_base(self.base)
        digest = hashlib.sha256(self.base.read_bytes()).hexdigest()
        self.attestation = CorpusAttestation(digest, self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_indexes_only_answer_safe_fragments(self) -> None:
        result = build_search_index(self.base, self.index, self.attestation)
        self.assertEqual(result.indexed_rows, 1)
        rows = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256).search("계약금액", company="테스트", as_of=None)
        self.assertEqual([row["evidence_id"] for row in rows], ["ev_safe"])

    def test_korean_substring_uses_limited_trigram_fallback(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        rows = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256).search("발행주식수", company="테스트", as_of=None)
        self.assertEqual(rows[0]["matched_index"], "trigram")

    def test_attestation_mismatch_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        with self.assertRaisesRegex(ValueError, "base attestation"):
            SafeSearchIndex(self.index, base_sha256="b" * 64)

    def test_atomic_failure_preserves_existing_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        before = self.index.read_bytes()
        with patch("disclosure_db.search_index.os.replace", side_effect=OSError("promote failed")):
            with self.assertRaises(OSError):
                build_search_index(self.base, self.index, self.attestation)
        self.assertEqual(self.index.read_bytes(), before)

    def test_as_of_and_current_filters_apply_before_ranking(self) -> None:
        connection = sqlite3.connect(self.base)
        connection.execute("UPDATE filing_version SET effective_to='2024-01-02', is_current=0 WHERE filing_id='f_safe'")
        connection.execute("UPDATE filing_version SET effective_from='2024-01-02', is_current=1 WHERE filing_id='f_unresolved'")
        connection.execute("UPDATE filing_version SET lineage_status='resolved' WHERE filing_id='f_unresolved'")
        connection.execute("UPDATE fragment SET text_normalized='계약금액', text_raw='계약금액' WHERE evidence_id='ev_unresolved'")
        connection.commit()
        connection.close()
        self.attestation = CorpusAttestation(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")
        build_search_index(self.base, self.index, self.attestation)
        index = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256)
        self.assertEqual([row["evidence_id"] for row in index.search("계약금액", company="테스트", as_of="2024-01-01")], ["ev_safe"])
        self.assertEqual([row["evidence_id"] for row in index.search("계약금액", company="테스트", as_of=None)], ["ev_unresolved"])

    def test_evidence_service_uses_valid_index_without_ssot_fallback(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        service = EvidenceService(self.base, attestation=self.attestation, search_database=self.index)
        with patch("disclosure_db.evidence_service.query_database", side_effect=AssertionError("SSOT fallback")):
            bundle = service.search(QueryPlan("테스트 계약금액", company="테스트"))
        self.assertEqual([ref.evidence_id for ref in bundle.evidence], ["ev_safe"])

    def test_evidence_service_falls_back_when_index_is_corrupt(self) -> None:
        self.index.write_bytes(b"not sqlite")
        service = EvidenceService(self.base, attestation=self.attestation, search_database=self.index)
        with patch("disclosure_db.evidence_service.query_database", return_value=[{
            "evidence_id": "ev_safe", "filing_id": "f_safe", "source_id": "s_safe",
            "text_normalized": "계약금액", "locator_json": "{}", "lineage_status": "root",
            "score": 1.0, "detected_format": "xml", "image_reference_count": 0,
            "filed_at": "2024-01-01", "report_name_raw": "사업보고서", "is_current": 1,
        }]) as fallback:
            bundle = service.search(QueryPlan("테스트 계약금액", company="테스트"))
        self.assertTrue(fallback.called)
        self.assertIn("search_index_sqlite_error", bundle.reason_codes)

    def test_evidence_service_falls_back_when_index_is_missing(self) -> None:
        missing = self.index.with_name("missing.sqlite")
        service = EvidenceService(self.base, attestation=self.attestation, search_database=missing)
        with patch("disclosure_db.evidence_service.query_database", return_value=[{
            "evidence_id": "ev_safe", "filing_id": "f_safe", "source_id": "s_safe",
            "text_normalized": "계약금액", "locator_json": "{}", "lineage_status": "root",
            "score": 1.0, "detected_format": "xml", "image_reference_count": 0,
            "filed_at": "2024-01-01", "report_name_raw": "사업보고서", "is_current": 1,
        }]):
            bundle = service.search(QueryPlan("테스트 계약금액", company="테스트"))
        self.assertIn("search_index_unavailable", bundle.reason_codes)


if __name__ == "__main__":
    unittest.main()
