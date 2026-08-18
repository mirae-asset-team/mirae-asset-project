from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.api import _health_status
from disclosure_db.generation import DeterministicGenerator


class AttestationTests(unittest.TestCase):
    def test_distribution_manifest_verifies_size_without_hashing_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            database.write_bytes(b"fixture")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {
                    "uncompressed_bytes": database.stat().st_size,
                    "uncompressed_sha256": "a" * 64,
                },
            }), encoding="utf-8")
            attestation = load_distribution_attestation(manifest, database=database)
            self.assertTrue(verify_fast_identity(database, attestation))

    def test_size_change_fails_fast_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            database.write_bytes(b"fixture")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {"uncompressed_bytes": 999, "uncompressed_sha256": "a" * 64},
            }), encoding="utf-8")
            self.assertFalse(verify_fast_identity(
                database,
                load_distribution_attestation(manifest, database=database),
            ))

    def test_manifest_rejects_invalid_sha256_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            database.write_bytes(b"fixture")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {"uncompressed_bytes": 7, "uncompressed_sha256": "not-a-sha"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "64-character SHA-256"):
                load_distribution_attestation(manifest, database=database)

    def test_missing_database_allows_health_to_report_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "missing.sqlite"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {"uncompressed_bytes": 7, "uncompressed_sha256": "a" * 64},
            }), encoding="utf-8")
            agent = DisclosureAgent(AgentSettings(
                base_database=database,
                attestation_path=manifest,
                use_hcx=False,
            ))
            health = _health_status(agent.evidence_service)
            self.assertEqual(health["status"], "degraded")
            self.assertFalse(health["ready"])
            database.write_bytes(b"fixture")
            self.assertFalse(verify_fast_identity(database, agent.evidence_service.attestation))
            health = _health_status(agent.evidence_service)
            self.assertEqual(health["status"], "degraded")
            self.assertFalse(health["ready"])

    def test_attested_request_refuses_after_database_size_changes_before_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            manifest = root / "manifest.json"
            database.write_bytes(b"fixture")
            manifest.write_text(json.dumps({
                "database_version": "semantic-v1",
                "database": {"uncompressed_bytes": 7, "uncompressed_sha256": "a" * 64},
            }), encoding="utf-8")
            attestation = load_distribution_attestation(manifest, database=database)

            class Service:
                base_database = database
                overlay_database = None

                def company_candidates(self):
                    raise AssertionError("database retrieval")

            service = Service()
            service.attestation = attestation
            agent = DisclosureAgent(evidence_service=service, generator=DeterministicGenerator())
            database.write_bytes(b"changed-size")
            answer = agent.answer("테스트 설명")
            self.assertTrue(answer.verified)
            self.assertFalse(answer.answerable)
            self.assertIn("base_attestation_failed", answer.reason_codes)

    def test_health_reports_configured_missing_search_index_as_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            database.write_bytes(b"fixture")

            class Service:
                base_database = database
                overlay_database = None
                search_database = root / "missing-search.sqlite"
                attestation = None

            health = _health_status(Service())
            self.assertFalse(health["search_index_ready"])
            self.assertFalse(health["ready"])
            self.assertEqual(health["status"], "degraded")

    def test_health_reports_attested_overlay_mismatch_without_hashing_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "base.sqlite"
            overlay = root / "overlay.sqlite"
            database.write_bytes(b"fixture")
            overlay.write_bytes(b"overlay")
            attestation = load_distribution_attestation(
                root.joinpath("manifest.json").write_text(
                    json.dumps({"database_version": "semantic-v1", "database": {"uncompressed_bytes": 7, "uncompressed_sha256": "a" * 64}}),
                    encoding="utf-8",
                ) and root / "manifest.json",
                database=database,
            )

            class Service:
                base_database = database
                overlay_database = overlay
                search_database = None

            Service.attestation = attestation

            with patch("disclosure_db.api.verify_fast_identity", return_value=True), patch(
                "disclosure_db.financial_overlay.overlay_matches_base", return_value=False
            ):
                health = _health_status(Service())
            self.assertFalse(health["overlay_attested"])
            self.assertFalse(health["ready"])


if __name__ == "__main__":
    unittest.main()
