import unittest
from pathlib import Path


class DeploymentArtifactTests(unittest.TestCase):
    def test_compose_mounts_databases_read_only_and_has_healthcheck(self):
        text = Path("compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/data/base/disclosure.sqlite:ro", text)
        self.assertIn("/data/agent:ro", text)
        self.assertIn("healthcheck:", text)
        self.assertNotIn("CLOVASTUDIO_API_KEY=", text)

    def test_public_limit_defaults_are_explicit_in_compose_and_example_env(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        example = Path(".env.example").read_text(encoding="utf-8")
        expected = {
            "DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "120",
            "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "4",
            "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "8",
        }
        self.assertIn("DISCLOSURE_QA_DB: /runtime/qa_lab.sqlite", compose)
        self.assertIn("DISCLOSURE_QA_DB=/runtime/qa_lab.sqlite", example)
        self.assertNotIn("DISCLOSURE_QA_TOKEN", compose)
        self.assertNotIn("DISCLOSURE_QA_TOKEN", example)
        for name, value in expected.items():
            self.assertIn(f"{name}: ${{{name}:-{value}}}", compose)
            self.assertIn(f"{name}={value}", example)

    def test_team_qa_compose_keeps_data_read_only_and_uses_nonpublic_port(self):
        compose = Path("compose.team-qa.yaml").read_text(encoding="utf-8")
        caddyfile = Path("Caddyfile.team-qa").read_text(encoding="utf-8")
        self.assertNotIn("8001:8000", compose)
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)
        self.assertIn("DISCLOSURE_QA_PASSWORD_HASH", compose)
        self.assertIn("basic_auth", caddyfile)
        self.assertIn("reverse_proxy team-qa:8000", caddyfile)
        self.assertIn("DISCLOSURE_QA_DB: /runtime/qa_lab.sqlite", compose)
        self.assertIn("${DISCLOSURE_BASE_DB_HOST}:/data/base/disclosure.sqlite:ro", compose)
        self.assertIn("${DISCLOSURE_AGENT_DB_DIR_HOST}:/data/agent:ro", compose)
        self.assertIn("${DISCLOSURE_ATTESTATION_HOST}:/data/attestation.json:ro", compose)
        self.assertNotIn("0.0.0.0/0", compose)

    def test_dockerfile_does_not_copy_local_data(self):
        self.assertIn("data/", Path(".dockerignore").read_text(encoding="utf-8"))
        self.assertNotIn("D:\\", Path("Dockerfile").read_text(encoding="utf-8"))

    def test_dockerfile_copies_runtime_alias_configuration(self):
        text = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY config ./config", text)
        self.assertIn("DISCLOSURE_CONFIG_DIR=/app/config", text)

    def test_python_package_declares_local_webfont_assets(self):
        text = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"web/fonts/*.woff2"', text)
        self.assertIn('"web/fonts/LICENSE.md"', text)

    def test_ci_installs_public_api_test_dependencies_and_runs_pytest(self):
        text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('python -m pip install -e ".[agent]" pytest httpx', text)
        self.assertIn("python -m pytest -q", text)

        smoke_test = Path("tests/test_smoke_agent.py").read_text(encoding="utf-8")
        self.assertIn('shutil.which("powershell") or shutil.which("pwsh")', smoke_test)

    def test_windows_scripts_validate_read_only_inputs_and_smoke_query(self):
        start = Path("scripts/start-agent.ps1").read_text(encoding="utf-8")
        smoke = Path("scripts/smoke-agent.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-Path -LiteralPath", start)
        self.assertIn("disclosure-agent", start)
        self.assertIn("Get-Command disclosure-agent", start)
        self.assertIn("-m disclosure_db.cli", start)
        self.assertIn("/health", smoke)
        self.assertIn("/query", smoke)
        self.assertIn("ConvertFrom-Base64Utf8", smoke)
        self.assertIn("smoke-answerable", smoke)
        self.assertIn("smoke-injection", smoke)


if __name__ == "__main__":
    unittest.main()
