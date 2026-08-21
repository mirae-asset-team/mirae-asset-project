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
from disclosure_db.schema import create_schema, create_indexes
from disclosure_db.search_index import SafeSearchIndex, build_search_index


def _seed_base(path: Path) -> None:
    connection = sqlite3.connect(path)
    create_schema(connection)
    filing_values = [
        ("f_safe", "doc_safe", "00000001", "000001", "테스트", "테스트상장", "테스트보고", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-01-01", 2023, 12, 0, "xml", 1),
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
    create_indexes(connection)
    connection.commit()
    connection.close()


def _seed_filing_date_base(path: Path) -> None:
    _seed_base(path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE filing SET filed_at='2023-04-10' WHERE filing_id='f_safe'")
    connection.execute(
        "INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("f_nearby", "doc_nearby", "00000001", "000001", "테스트", "테스트상장", "테스트보고", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2023-04-03", 2023, 12, 0, "xml", 1),
    )
    connection.execute(
        "INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s_nearby", "f_nearby", "main", "nearby.xml", ".xml", "dart_xml", "utf-8", "utf-8", "d" * 64, 10, "2023-04-03T00:00:00Z", "test", "1", "success", 1, 0, "[]", json.dumps({"image_reference_count": 0})),
    )
    connection.execute("INSERT INTO filing_event VALUES(?,?,?,?,?)", ("e_nearby", "00000001", "periodic", "nearby", "test"))
    connection.execute("INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)", ("f_nearby", "e_nearby", 1, None, "root", "high", "2023-04-03", None, 1, "test"))
    connection.execute(
        "INSERT INTO fragment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ev_nearby", "f_nearby", "s_nearby", "heading", 0, "[]", None, None, "{}", "공시 제목 계약금액", "공시 제목 계약금액", "1"),
    )
    connection.commit()
    connection.close()


def _seed_correction_pair_base(path: Path) -> None:
    _seed_base(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE filing_version SET effective_to='2024-02-01', is_current=0 WHERE filing_id='f_safe'"
    )
    connection.execute(
        "UPDATE filing SET issuer_corp_code='00000001', issuer_name='테스트', listed_name='테스트상장', "
        "reporter_name='테스트보고', is_correction=1 WHERE filing_id='f_unresolved'"
    )
    connection.execute(
        "UPDATE filing_version SET event_id='e_safe', version_no=2, parent_filing_id='f_safe', "
        "lineage_status='resolved', lineage_confidence='high', "
        "effective_from='2024-02-01', effective_to=NULL, is_current=1 WHERE filing_id='f_unresolved'"
    )
    connection.execute(
        "UPDATE fragment SET text_raw='계약금액 정정', text_normalized='계약금액 정정' "
        "WHERE evidence_id='ev_unresolved'"
    )
    connection.execute(
        "INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("f_other", "doc_other", "99999999", "999999", "다른회사", "다른회사", "다른회사", "IT", "IT", "periodic", "사업보고서", "사업보고서", "사업보고서", "사업보고서", "2024-02-02", 2023, 12, 1, "xml", 1),
    )
    connection.execute(
        "INSERT INTO source_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s_other", "f_other", "main", "other.xml", ".xml", "dart_xml", "utf-8", "utf-8", "d" * 64, 10, "2024-02-02T00:00:00Z", "test", "1", "success", 1, 0, "[]", json.dumps({"image_reference_count": 0})),
    )
    connection.execute(
        "INSERT INTO filing_event VALUES(?,?,?,?,?)",
        ("e_other", "99999999", "periodic", "other", "test"),
    )
    connection.execute(
        "INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("f_other", "e_other", 1, None, "root", "high", "2024-02-02", None, 1, "test"),
    )
    connection.execute(
        "INSERT INTO fragment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ev_other", "f_other", "s_other", "paragraph", 0, "[]", None, None, "{}", "계약금액 정정", "계약금액 정정", "1"),
    )
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

    def test_search_index_exact_filing_date_excludes_nearby_filing(self) -> None:
        base = Path(self.temp.name) / "filing_date_base.sqlite"
        index = Path(self.temp.name) / "filing_date_search.sqlite"
        _seed_filing_date_base(base)
        digest = hashlib.sha256(base.read_bytes()).hexdigest()
        attestation = CorpusAttestation(digest, base.stat().st_size, base.stat().st_mtime_ns, "semantic-v1")
        build_search_index(base, index, attestation)
        rows = SafeSearchIndex(index, base_sha256=attestation.sha256).search(
            "계약금액", company="테스트", as_of=None, filed_at="2023-04-10"
        )
        self.assertEqual({row["filed_at"] for row in rows}, {"2023-04-10"})

    def test_korean_substring_uses_limited_trigram_fallback(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        rows = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256).search("발행주식수", company="테스트", as_of=None)
        self.assertEqual(rows[0]["matched_index"], "trigram")

    def test_attestation_mismatch_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        with self.assertRaisesRegex(ValueError, "base attestation"):
            SafeSearchIndex(self.index, base_sha256="b" * 64)

    def test_metadata_revision_mismatch_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        connection = sqlite3.connect(self.index)
        connection.execute("UPDATE index_revision SET revision='unknown-schema'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "revision"):
            SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)

    def test_metadata_base_size_mismatch_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        connection = sqlite3.connect(self.index)
        connection.execute("UPDATE index_revision SET base_size_bytes=base_size_bytes+1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "size"):
            SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)

    def test_metadata_row_count_mismatch_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        connection = sqlite3.connect(self.index)
        connection.execute("UPDATE index_revision SET row_count=row_count+1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "row count"):
            SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)

    def test_index_row_deletion_refuses_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        connection = sqlite3.connect(self.index)
        connection.execute("DELETE FROM search_document WHERE evidence_id='ev_safe'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "row count"):
            SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)

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

    def test_correction_policy_original_ignores_current_flag(self) -> None:
        connection = sqlite3.connect(self.base)
        connection.execute("UPDATE filing_version SET is_current=0 WHERE filing_id='f_safe'")
        connection.commit()
        connection.close()
        self.attestation = CorpusAttestation(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")
        build_search_index(self.base, self.index, self.attestation)
        index = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)
        self.assertEqual(index.search("계약금액", company="테스트", as_of=None), [])
        self.assertEqual([row["evidence_id"] for row in index.search("계약금액", company="테스트", as_of=None, correction_policy="original")], ["ev_safe"])

    def test_correction_policy_corrected_selects_correction(self) -> None:
        connection = sqlite3.connect(self.base)
        connection.execute("UPDATE filing_version SET lineage_status='resolved', is_current=1 WHERE filing_id='f_unresolved'")
        connection.execute("UPDATE filing SET is_correction=1 WHERE filing_id='f_unresolved'")
        connection.commit()
        connection.close()
        self.attestation = CorpusAttestation(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")
        build_search_index(self.base, self.index, self.attestation)
        index = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)
        self.assertEqual([row["evidence_id"] for row in index.search("계약금액", company="테스트", as_of=None, correction_policy="corrected")], ["ev_unresolved"])

    def test_correction_policies_preserve_version_issuer_and_date_boundaries(self) -> None:
        base = Path(self.temp.name) / "correction_pair.sqlite"
        index_path = Path(self.temp.name) / "correction_pair_search.sqlite"
        _seed_correction_pair_base(base)
        attestation = CorpusAttestation(
            hashlib.sha256(base.read_bytes()).hexdigest(),
            base.stat().st_size,
            base.stat().st_mtime_ns,
            "semantic-v1",
        )
        build_search_index(base, index_path, attestation)
        index = SafeSearchIndex(index_path, base_sha256=attestation.sha256)

        current = index.search("계약금액", company="00000001", as_of=None, correction_policy="current")
        original = index.search("계약금액", company="00000001", as_of=None, correction_policy="original")
        both = index.search("계약금액", company="00000001", as_of=None, correction_policy="both")

        self.assertEqual([row["evidence_id"] for row in current], ["ev_unresolved"])
        self.assertEqual([row["evidence_id"] for row in original], ["ev_safe"])
        self.assertEqual({row["evidence_id"] for row in both}, {"ev_safe", "ev_unresolved"})
        self.assertEqual(
            [row["evidence_id"] for row in index.search(
                "계약금액", company="00000001", as_of="2024-01-15", correction_policy="both",
            )],
            ["ev_safe"],
        )
        self.assertEqual(
            [row["evidence_id"] for row in index.search(
                "계약금액", company="00000001", as_of=None,
                filed_at="2024-01-02", correction_policy="both",
            )],
            ["ev_unresolved"],
        )
        self.assertNotIn("ev_other", {row["evidence_id"] for row in both})

    def test_company_aliases_filter_index(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        index = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)
        for alias in ("테스트상장", "테스트보고", "000001", "00000001"):
            with self.subTest(alias=alias):
                self.assertEqual([row["evidence_id"] for row in index.search("계약금액", company=alias, as_of=None)], ["ev_safe"])

    def test_empty_query_returns_no_results(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        index = SafeSearchIndex(self.index, base_sha256=self.attestation.sha256, expected_base_size=self.attestation.size_bytes)
        self.assertEqual(index.search("!!!", company="테스트", as_of=None), [])

    def test_empty_query_does_not_broaden_ssot_fallback(self) -> None:
        service = EvidenceService(self.base, attestation=self.attestation, search_database=self.index)
        with patch("disclosure_db.evidence_service.query_database", side_effect=AssertionError("broad fallback")):
            bundle = service.search(QueryPlan("!!!", company="테스트"))
        self.assertFalse(bundle.evidence)
        self.assertIn("empty_search_query", bundle.reason_codes)

    def test_evidence_service_uses_valid_index_without_ssot_fallback(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        service = EvidenceService(self.base, attestation=self.attestation, search_database=self.index)
        with patch("disclosure_db.evidence_service.query_database", side_effect=AssertionError("SSOT fallback")):
            bundle = service.search(QueryPlan("테스트 계약금액", company="테스트"))
        self.assertEqual([ref.evidence_id for ref in bundle.evidence], ["ev_safe"])

    def test_evidence_service_falls_back_when_index_metadata_drifts(self) -> None:
        build_search_index(self.base, self.index, self.attestation)
        connection = sqlite3.connect(self.index)
        connection.execute("UPDATE index_revision SET row_count=row_count+1")
        connection.commit()
        connection.close()
        service = EvidenceService(self.base, attestation=self.attestation, search_database=self.index)
        with patch("disclosure_db.evidence_service.query_database", return_value=[{
            "evidence_id": "ev_safe", "filing_id": "f_safe", "source_id": "s_safe",
            "text_normalized": "계약금액", "locator_json": "{}", "lineage_status": "root",
            "score": 1.0, "detected_format": "xml", "image_reference_count": 0,
            "filed_at": "2024-01-01", "report_name_raw": "사업보고서", "is_current": 1,
        }]) as fallback:
            bundle = service.search(QueryPlan("테스트 계약금액", company="테스트"))
        self.assertTrue(fallback.called)
        self.assertIn("search_index_attestation_mismatch", bundle.reason_codes)

    def test_ssot_fallback_preserves_original_correction_policy(self) -> None:
        connection = sqlite3.connect(self.base)
        connection.execute("UPDATE filing_version SET is_current=0 WHERE filing_id='f_safe'")
        connection.execute("UPDATE filing_version SET lineage_status='resolved', is_current=1 WHERE filing_id='f_unresolved'")
        connection.execute("UPDATE filing SET is_correction=1 WHERE filing_id='f_unresolved'")
        connection.commit()
        connection.close()
        self.attestation = CorpusAttestation(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")
        missing = self.index.with_name("missing-original.sqlite")
        service = EvidenceService(self.base, attestation=self.attestation, search_database=missing)
        bundle = service.search(QueryPlan("계약금액", company="테스트", correction_policy="original"))
        self.assertEqual([ref.evidence_id for ref in bundle.evidence], ["ev_safe"])

    def test_ssot_fallback_preserves_corrected_correction_policy(self) -> None:
        connection = sqlite3.connect(self.base)
        connection.execute("UPDATE filing_version SET lineage_status='resolved', is_current=1 WHERE filing_id='f_unresolved'")
        connection.execute("UPDATE filing SET is_correction=1 WHERE filing_id='f_unresolved'")
        connection.commit()
        connection.close()
        self.attestation = CorpusAttestation(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.base.stat().st_size, self.base.stat().st_mtime_ns, "semantic-v1")
        missing = self.index.with_name("missing-corrected.sqlite")
        service = EvidenceService(self.base, attestation=self.attestation, search_database=missing)
        bundle = service.search(QueryPlan("계약금액", company="테스트", correction_policy="corrected"))
        self.assertEqual([ref.evidence_id for ref in bundle.evidence], ["ev_unresolved"])

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
