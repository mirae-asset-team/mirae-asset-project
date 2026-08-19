import unittest
from pathlib import Path


class DeploymentArtifactTests(unittest.TestCase):
    def test_compose_mounts_databases_read_only_and_has_healthcheck(self):
        text = Path("compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/data/base/disclosure.sqlite:ro", text)
        self.assertIn("/data/agent:ro", text)
        self.assertIn("healthcheck:", text)
        self.assertNotIn("CLOVASTUDIO_API_KEY=", text)

    def test_dockerfile_does_not_copy_local_data(self):
        self.assertIn("data/", Path(".dockerignore").read_text(encoding="utf-8"))
        self.assertNotIn("D:\\", Path("Dockerfile").read_text(encoding="utf-8"))

    def test_dockerfile_copies_runtime_alias_configuration(self):
        text = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY config ./config", text)
        self.assertIn("DISCLOSURE_CONFIG_DIR=/app/config", text)

    def test_windows_scripts_validate_read_only_inputs_and_smoke_query(self):
        start = Path("scripts/start-agent.ps1").read_text(encoding="utf-8")
        smoke = Path("scripts/smoke-agent.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-Path -LiteralPath", start)
        self.assertIn("disclosure-agent", start)
        self.assertIn("Get-Command disclosure-agent", start)
        self.assertIn("-m disclosure_db.cli", start)
        self.assertIn("/health", smoke)
        self.assertIn("/query", smoke)
        self.assertIn("연결 XI.", smoke)


if __name__ == "__main__":
    unittest.main()
