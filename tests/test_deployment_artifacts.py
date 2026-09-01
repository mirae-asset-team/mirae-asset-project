import unittest
from pathlib import Path


class DeploymentArtifactTests(unittest.TestCase):
    def test_compose_mounts_databases_read_only_and_has_healthcheck(self):
        text = Path("compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/data/base/disclosure.sqlite:ro", text)
        self.assertIn("/data/agent:ro", text)
        self.assertIn("healthcheck:", text)
        self.assertIn("dense-retriever:", text)
        self.assertIn("/srv/mirae/data/dense:/data/dense:ro", text)
        self.assertIn("/srv/mirae/data/model/bge-m3:/model:ro", text)
        self.assertNotIn("CLOVASTUDIO_API_KEY=", text)

    def test_full_corpus_dense_runtime_is_bounded_and_explicit(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        example = Path(".env.example").read_text(encoding="utf-8")
        dense_dockerfile = Path("Dockerfile.dense").read_text(encoding="utf-8")
        for name, value in (
            ("DISCLOSURE_DENSE_TIMEOUT_SECONDS", "5"),
            ("DISCLOSURE_DENSE_VECTOR_COUNT", "2571506"),
        ):
            self.assertIn(f"{name}: ${{{name}:-{value}}}", compose)
            self.assertIn(f"{name}={value}", example)
        self.assertIn('CMD ["disclosure-dense"]', dense_dockerfile)
        self.assertIn('HF_HUB_OFFLINE=1', dense_dockerfile)

    def test_public_limit_defaults_are_explicit_in_compose_and_example_env(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        example = Path(".env.example").read_text(encoding="utf-8")
        expected = {
            "DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "120",
            "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "4",
            "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "8",
        }
        for name, value in expected.items():
            self.assertIn(f"{name}: ${{{name}:-{value}}}", compose)
            self.assertIn(f"{name}={value}", example)

    def test_hcx_function_calling_environment_is_forwarded_without_tracking_local_env(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        example = Path(".env.example").read_text(encoding="utf-8")
        gitignore = Path(".gitignore").read_text(encoding="utf-8")
        for name in ("CLOVASTUDIO_API_KEY", "CLOVASTUDIO_BASE_URL", "CLOVASTUDIO_MODEL"):
            self.assertIn(name, compose)
            self.assertIn(name, example)
        self.assertIn(".env\n", gitignore.replace("\r\n", "\n"))
        self.assertNotIn("in-memory-test-key", compose)

    def test_container_entrypoint_composes_remote_dense_hybrid_function_calling_runtime(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        cli = Path("src/disclosure_db/cli.py").read_text(encoding="utf-8")
        runtime = Path("src/disclosure_db/runtime.py").read_text(encoding="utf-8")
        api = Path("src/disclosure_db/api.py").read_text(encoding="utf-8")
        self.assertIn('CMD ["disclosure-agent", "serve"]', dockerfile)
        self.assertIn("build_runtime_services(settings)", cli)
        self.assertIn("function_calling_service=services.function_calling", cli)
        self.assertIn("EvidenceServiceDenseRetriever(evidence_service)", runtime)
        self.assertIn("HybridRetriever(sparse, remote_dense)", runtime)
        self.assertIn("DISCLOSURE_DENSE_URL: http://dense-retriever:8080", Path("compose.yaml").read_text(encoding="utf-8"))
        self.assertIn('app.post("/v1/hcx/function-answer")', api)

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
