from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity


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


if __name__ == "__main__":
    unittest.main()
