import unittest
from pathlib import Path
import tomllib


class DeploymentArtifactTests(unittest.TestCase):
    def test_numpy_is_pinned_for_the_dense_image_and_absent_from_the_agent_image(self):
        project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        extras = project["project"]["optional-dependencies"]
        self.assertIn("numpy==2.5.2", extras["dense"])
        self.assertFalse(any(item.casefold().startswith("numpy") for item in extras["agent"]))

        agent_dockerfile = Path("Dockerfile").read_text(encoding="utf-8").casefold()
        dense_dockerfile = Path("Dockerfile.dense").read_text(encoding="utf-8").casefold()
        self.assertIn('".[agent]"', agent_dockerfile)
        self.assertNotIn("numpy", agent_dockerfile)
        self.assertIn('".[dense]"', dense_dockerfile)

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
        self.assertIn("DENSE_RUNTIME_MANIFEST_PATH: /runtime/dense_runtime_manifest.json", compose)
        for name, value in (
            ("DISCLOSURE_DENSE_TIMEOUT_SECONDS", "5"),
            ("DISCLOSURE_DENSE_VECTOR_COUNT", "2571506"),
        ):
            self.assertIn(f"{name}: ${{{name}:-{value}}}", compose)
            self.assertIn(f"{name}={value}", example)
        self.assertIn('CMD ["disclosure-dense"]', dense_dockerfile)
        self.assertIn('HF_HUB_OFFLINE=1', dense_dockerfile)

    def test_agent_image_binds_dense_adoption_to_tracked_evaluation(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(
            "COPY data/derived/freeform_retrieval_summary.json /app/data/derived/freeform_retrieval_summary.json",
            dockerfile,
        )
        self.assertIn("data/*", dockerignore)
        self.assertIn("!data/derived/", dockerignore)
        self.assertIn("data/derived/*", dockerignore)
        self.assertIn("!data/derived/freeform_retrieval_summary.json", dockerignore)
        self.assertEqual(
            compose.count("DISCLOSURE_DENSE_EVALUATION_SUMMARY: /app/data/derived/freeform_retrieval_summary.json"),
            2,
        )
        self.assertEqual(
            compose.count("DISCLOSURE_DENSE_ADOPTION_ARTIFACT: /runtime/dense_adoption.json"),
            2,
        )

    def test_dense_model_identity_has_a_reproducible_staging_builder(self):
        script = Path("scripts/build_dense_model_identity.py")
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertIn("build_model_identity", text)
        self.assertIn("snapshot_download", text)
        self.assertIn("local_files_only=True", text)
        self.assertIn("--model-path", text)
        self.assertIn("--output", text)

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
        self.assertIn(
            'python -m pip install -e ".[agent]" pytest httpx numpy==2.5.2 PyYAML==6.0.3',
            text,
        )
        self.assertNotIn('".[dense]"', text)
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

    def test_staging_reuses_dense_and_enables_eval_only_on_port_8001(self):
        compose = Path("compose.yaml").read_text(encoding="utf-8")
        deploy = Path("scripts/deploy_staging.ps1").read_text(encoding="utf-8")
        self.assertEqual(compose.splitlines().count("  dense-retriever:"), 1)
        self.assertIn("disclosure-agent-staging:", compose)
        self.assertIn('ports: ["8001:8000"]', compose)
        self.assertIn('EVAL_ENABLED: "1"', compose)
        self.assertIn("DISCLOSURE_DENSE_URL: http://dense-retriever:8080", compose)
        self.assertIn("qa-eval-data:/app/eval", compose)
        self.assertIn("& docker build", deploy)
        self.assertIn("up -d --no-build dense-retriever disclosure-agent-staging", deploy)
        self.assertNotIn("docker compose build disclosure-agent-staging", deploy)
        self.assertNotIn("docker compose down", deploy)
        self.assertNotIn("CLOVASTUDIO_API_KEY=", deploy)


if __name__ == "__main__":
    unittest.main()
