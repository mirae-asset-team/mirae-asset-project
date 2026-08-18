import tempfile
import unittest
from pathlib import Path

from disclosure_db.runtime import RuntimeConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_from_env_requires_all_three_databases_and_attestation(self):
        with self.assertRaisesRegex(ValueError, "DISCLOSURE_BASE_DB"):
            RuntimeConfig.from_env({})

    def test_validate_accepts_existing_readable_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {
                name: root / name
                for name in ("base.sqlite", "overlay.sqlite", "search.sqlite", "attestation.json")
            }
            for path in files.values():
                path.write_bytes(b"x")
            config = RuntimeConfig.from_env(
                {
                    "DISCLOSURE_BASE_DB": str(files["base.sqlite"]),
                    "DISCLOSURE_OVERLAY_DB": str(files["overlay.sqlite"]),
                    "DISCLOSURE_SEARCH_DB": str(files["search.sqlite"]),
                    "DISCLOSURE_ATTESTATION": str(files["attestation.json"]),
                }
            )
            self.assertEqual(config.port, 8000)
            self.assertFalse(config.provider_configured)
            config.validate()


if __name__ == "__main__":
    unittest.main()
