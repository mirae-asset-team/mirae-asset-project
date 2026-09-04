import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.agent import AgentSettings
from disclosure_db.cli import _serving_settings
from disclosure_db.runtime import RuntimeConfig


class CliTests(unittest.TestCase):
    def test_serve_without_paths_loads_runtime_environment(self):
        base = Path("base.sqlite")
        overlay = Path("overlay.sqlite")
        search = Path("search.sqlite")
        attestation = Path("attestation.json")
        args = argparse.Namespace(
            database=None,
            overlay=None,
            search_database=None,
            attestation=None,
            host="127.0.0.1",
            port=8000,
        )
        config = RuntimeConfig(base, overlay, search, attestation, host="0.0.0.0", port=9000)
        with patch("disclosure_db.cli.RuntimeConfig.from_env", return_value=config) as load:
            with patch.object(RuntimeConfig, "validate"):
                settings, host, port = _serving_settings(args)
        load.assert_called_once()
        self.assertEqual(settings.base_database, base)
        self.assertEqual((host, port), (config.host, config.port))

    def test_serve_with_explicit_paths_does_not_load_runtime_environment(self):
        args = argparse.Namespace(
            database=Path("base.sqlite"),
            overlay=Path("overlay.sqlite"),
            search_database=Path("search.sqlite"),
            attestation=Path("attestation.json"),
            host="127.0.0.1",
            port=8000,
        )
        with patch("disclosure_db.cli.RuntimeConfig.from_env") as load:
            settings, host, port = _serving_settings(args)
        load.assert_not_called()
        self.assertIsInstance(settings, AgentSettings)
        self.assertEqual(settings.base_database, args.database)
        self.assertEqual((host, port), (args.host, args.port))


if __name__ == "__main__":
    unittest.main()
