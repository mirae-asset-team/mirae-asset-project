import re
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
BASE_SHA256 = "c" * 64
OVERLAY_SHA256 = "d" * 64
SEARCH_SHA256 = "e" * 64

PASSING_METRICS = {
    "financial_total": 856,
    "financial_passed": 856,
    "financial_failed": 0,
    "judge_case_count": 600,
    "judge_evaluated_count": 600,
    "judge_pass_count": 600,
    "judge_failure_count": 0,
    "judge_numeric_exactness": 1.0,
    "judge_claim_citation_coverage": 1.0,
    "judge_answerability_agreement": 0.95,
    "judge_metamorphic_consistency": 0.98,
    "retrieval_recall_at_20": 0.95,
    "concurrency_request_count": 20,
    "concurrency_error_count": 0,
    "concurrency_p95_ms": 2_000.0,
    "provider_eligible_root_count": 42,
    "provider_probe_observation_count": 120,
    "provider_call_count": 120,
    "provider_p95_ms": 10_000.0,
    "hallucinated_numeric_claim_count": 0,
    "unknown_citation_count": 0,
    "cross_filing_citation_count": 0,
    "policy_violation_count": 0,
    "secret_leak_count": 0,
    "evaluator_error_count": 0,
    "forbidden_provider_call_count": 0,
    "deterministic_provider_call_count": 0,
}

FAILING_METRIC_VALUES = {
    "financial_total": 855,
    "financial_passed": 855,
    "financial_failed": 1,
    "judge_case_count": 599,
    "judge_evaluated_count": 599,
    "judge_pass_count": 599,
    "judge_failure_count": 1,
    "judge_numeric_exactness": 0.999,
    "judge_claim_citation_coverage": 0.999,
    "judge_answerability_agreement": 0.949,
    "judge_metamorphic_consistency": 0.979,
    "retrieval_recall_at_20": 0.949,
    "concurrency_request_count": 19,
    "concurrency_error_count": 1,
    "concurrency_p95_ms": 2_000.01,
    "provider_eligible_root_count": 41,
    "provider_probe_observation_count": 119,
    "provider_call_count": 119,
    "provider_p95_ms": 10_000.01,
    "hallucinated_numeric_claim_count": 1,
    "unknown_citation_count": 1,
    "cross_filing_citation_count": 1,
    "policy_violation_count": 1,
    "secret_leak_count": 1,
    "evaluator_error_count": 1,
    "forbidden_provider_call_count": 1,
    "deterministic_provider_call_count": 1,
}

DEPLOY_SCRIPTS = (
    "scripts/deploy_staging.ps1",
    "scripts/deploy_release.ps1",
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def passing_report() -> dict[str, object]:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "release-gate-summary-v1",
        "evaluated_at_utc": timestamp,
        "release_state": "PASS",
        "hard_gate_passed": True,
        "hard_gate_reasons": [],
        "metrics": dict(PASSING_METRICS),
        "identity": {
            "commit": COMMIT,
            "image_id": IMAGE_ID,
            "base_sha256": BASE_SHA256,
            "overlay_sha256": OVERLAY_SHA256,
            "search_index_sha256": SEARCH_SHA256,
        },
        "source_timestamps": {
            "financial": timestamp,
            "judge": timestamp,
            "retrieval": timestamp,
            "deployment": timestamp,
        },
    }


def powershell_hosts() -> tuple[str, ...]:
    return tuple(
        executable
        for name in ("powershell", "pwsh")
        if (executable := shutil.which(name)) is not None
    )


def write_external_command_guards(directory: Path, marker: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for command in ("git", "docker", "ssh", "scp"):
        if os.name == "nt":
            shim = directory / f"{command}.cmd"
            output = f"echo {COMMIT}" if command == "git" else "rem no output"
            shim.write_text(
                "@echo off\n"
                f">>\"{marker}\" echo {command}\n"
                f"{output}\n"
                "exit /b 97\n",
                encoding="utf-8",
            )
        else:
            shim = directory / command
            output = f"printf '%s\\n' '{COMMIT}'" if command == "git" else ":"
            shim.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' '{command}' >> '{marker}'\n"
                f"{output}\n"
                "exit 97\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)


def run_deploy_script(
    host: str,
    relative_script: str,
    report: dict[str, object],
    temp: Path,
    *extra_arguments: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    report_path = temp / "release_gate_summary.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    marker = temp / "external-command.log"
    if marker.exists():
        marker.unlink()
    shim_directory = temp / "command-guards"
    write_external_command_guards(shim_directory, marker)
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(shim_directory), environment["PATH"]))
    command = [
        host,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / relative_script),
        "-ServerHost",
        "invalid.example",
        "-SshUser",
        "tester",
        "-ExpectedCommit",
        COMMIT,
        "-ExpectedImageId",
        IMAGE_ID,
        "-ExpectedBaseDbSha256",
        BASE_SHA256,
        "-ExpectedOverlayDbSha256",
        OVERLAY_SHA256,
        "-ExpectedSearchIndexSha256",
        SEARCH_SHA256,
        "-ReleaseGatePath",
        str(report_path),
        *extra_arguments,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    return completed, calls


def extract_rollback_bash() -> str:
    match = re.search(
        r"\$rollbackScript\s*=\s*@'\r?\n(?P<script>.*?)\r?\n'@",
        read("scripts/deploy_release.ps1"),
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("Invoke-Rollback embedded bash was not found")
    return match.group("script")


def run_rollback_bash(scenario: str, temp: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bash = shutil.which("bash")
    if bash is None:
        raise unittest.SkipTest("bash is required for embedded rollback execution tests")
    shim_directory = temp / "bash-command-guards"
    candidate_directory = temp / "candidate"
    marker = temp / "rollback-command.log"
    shim_directory.mkdir(parents=True, exist_ok=True)
    candidate_directory.mkdir(parents=True, exist_ok=True)
    docker_shim = shim_directory / "docker"
    docker_shim.write_bytes((
        "#!/bin/sh\n"
        "printf 'docker %s\\n' \"$*\" >> \"$ROLLBACK_MARKER\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then\n"
        "  if [ \"$ROLLBACK_SCENARIO\" = absent ]; then exit 44; fi\n"
        "  case \" $* \" in *' --format '*) printf '%s\\n' 'sha256:rollback' ;; esac\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = compose ]; then\n"
        "  case \" $* \" in *' ps -q disclosure-agent'*) printf '%s\\n' 'rollback-container' ;; esac\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = inspect ]; then\n"
        "  if [ \"$ROLLBACK_SCENARIO\" = mismatch ]; then printf '%s\\n' 'sha256:wrong'; else printf '%s\\n' 'sha256:rollback'; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 97\n"
    ).encode("utf-8"))
    curl_shim = shim_directory / "curl"
    curl_shim.write_bytes((
        "#!/bin/sh\n"
        "printf 'curl %s\\n' \"$*\" >> \"$ROLLBACK_MARKER\"\n"
        "if [ \"$ROLLBACK_SCENARIO\" = health-fail ]; then exit 22; fi\n"
        "case \" $* \" in *'/health'*) printf '%s\\n' '{\"ready\":true}' ;; esac\n"
        "exit 0\n"
    ).encode("utf-8"))
    docker_shim.chmod(0o755)
    curl_shim.chmod(0o755)
    try:
        shim_relative = shim_directory.relative_to(ROOT).as_posix()
        candidate_relative = candidate_directory.relative_to(ROOT).as_posix()
        marker_relative = marker.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise AssertionError("rollback bash temp directory must be under the repository") from exc
    shell_command = (
        f'export ROLLBACK_MARKER="$(pwd)/{marker_relative}"; '
        f'export ROLLBACK_SCENARIO={shlex.quote(scenario)}; '
        f'PATH="$(pwd)/{shim_relative}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" '
        f'bash -s -- "$(pwd)/{candidate_relative}" "rollback:test"'
    )
    command = [
        bash,
        "-lc",
        shell_command,
    ]
    raw_completed = subprocess.run(
        command,
        input=extract_rollback_bash().encode("utf-8"),
        capture_output=True,
        timeout=20,
        check=False,
    )
    completed = subprocess.CompletedProcess(
        args=raw_completed.args,
        returncode=raw_completed.returncode,
        stdout=raw_completed.stdout.decode("utf-8", errors="replace"),
        stderr=raw_completed.stderr.decode("utf-8", errors="replace"),
    )
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    return completed, calls


class ReleaseDeploymentAssetTests(unittest.TestCase):
    def test_release_compose_uses_prebuilt_images_only(self):
        compose = read("compose.release.yaml")

        self.assertNotRegex(compose, r"(?m)^\s*build\s*:")
        self.assertEqual(compose.count("${DISCLOSURE_RELEASE_IMAGE:?"), 2)
        self.assertIn("${DISCLOSURE_DENSE_IMAGE:-mirae-dense-retriever:latest}", compose)
        self.assertIn('target: /data/base/disclosure.sqlite', compose)
        self.assertIn('target: /data/agent/agent_overlay.sqlite', compose)
        self.assertIn('target: /data/agent/agent_search.sqlite', compose)
        self.assertIn('target: /data/attestation.json', compose)

    def test_release_compose_marks_every_protected_mount_read_only(self):
        compose = read("compose.release.yaml")
        protected_targets = (
            "/data/base/disclosure.sqlite",
            "/data/agent/agent_overlay.sqlite",
            "/data/agent/agent_search.sqlite",
            "/data/attestation.json",
            "/data/dense",
            "/model",
        )

        for target in protected_targets:
            block = re.search(
                rf"target:\s*{re.escape(target)}(?P<body>.*?)(?:\n\s*-\s+type:|\n\s+healthcheck:|\n\s+environment:)",
                compose,
                re.DOTALL,
            )
            self.assertIsNotNone(block, target)
            self.assertIn("read_only: true", block.group("body"), target)

    def test_both_scripts_fail_closed_on_gate_schema_state_and_identity(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            self.assertIn('release-gate-summary-v1', script, relative)
            self.assertRegex(script, r'hard_gate_passed\s*-ne\s*\$true', relative)
            self.assertRegex(script, r'release_state\s*-ne\s*"PASS"', relative)
            for field in (
                "commit",
                "image_id",
                "base_sha256",
                "overlay_sha256",
                "search_index_sha256",
            ):
                self.assertIn(field, script, f"{relative}: {field}")

    def test_current_blocked_release_report_cannot_satisfy_the_scripts(self):
        report = json.loads(read("data/derived/release_gate_summary.json"))

        self.assertEqual(report["schema_version"], "release-gate-summary-v1")
        self.assertEqual(report["release_state"], "BLOCKED_HARD_GATE")
        self.assertIs(report["hard_gate_passed"], False)
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            self.assertIn('hard_gate_passed -ne $true', script)
            self.assertIn('release_state -ne "PASS"', script)

    def test_gate_json_parsing_is_compatible_with_windows_powershell(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            self.assertNotIn("ConvertFrom-Json -Depth", script, relative)

    def test_gate_schema_requires_fresh_complete_content_free_summary(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            for field in (
                "evaluated_at_utc",
                "hard_gate_reasons",
                "metrics",
                "source_timestamps",
            ):
                self.assertIn(field, script, f"{relative}: {field}")
            self.assertIn("[DateTimeOffset]::TryParse", script)
            self.assertIn("TotalSeconds -gt 86400", script)
            self.assertRegex(script, r"hard_gate_reasons\s+-isnot\s+\[System\.Array\]")
            self.assertRegex(script, r"hard_gate_reasons\.Count\s+-ne\s+0")

    def test_empty_metrics_and_null_hard_gate_reasons_fail_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]
        malformed_reports = []
        empty_metrics = passing_report()
        empty_metrics["metrics"] = {}
        malformed_reports.append(("empty metrics", empty_metrics))
        null_reasons = passing_report()
        null_reasons["hard_gate_reasons"] = None
        malformed_reports.append(("null reasons", null_reasons))

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for label, report in malformed_reports:
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(case=label, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                        self.assertEqual(calls, [], f"{label} invoked external commands: {calls}")

    def test_every_required_metric_key_is_required_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for metric_name in PASSING_METRICS:
                report = passing_report()
                del report["metrics"][metric_name]
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(metric=metric_name, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                        self.assertEqual(calls, [], f"missing {metric_name} invoked: {calls}")

    def test_every_required_metric_rejects_non_numeric_types_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for metric_name in PASSING_METRICS:
                report = passing_report()
                report["metrics"][metric_name] = str(PASSING_METRICS[metric_name])
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(metric=metric_name, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                        self.assertEqual(calls, [], f"string {metric_name} invoked: {calls}")

    def test_every_required_metric_enforces_its_threshold_before_external_commands(self):
        self.assertEqual(PASSING_METRICS.keys(), FAILING_METRIC_VALUES.keys())
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for metric_name, failing_value in FAILING_METRIC_VALUES.items():
                report = passing_report()
                report["metrics"][metric_name] = failing_value
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(metric=metric_name, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                        self.assertEqual(calls, [], f"bad {metric_name} invoked: {calls}")

    def test_valid_iso_timestamp_is_accepted_by_windows_powershell_and_pwsh(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for host in hosts:
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(host=Path(host).name, script=relative):
                        completed, calls = run_deploy_script(host, relative, passing_report(), temp)
                        self.assertNotEqual(completed.returncode, 0)
                        self.assertEqual(calls, ["git"], completed.stdout + completed.stderr)

    def test_invalid_or_missing_evaluated_timestamp_fails_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for value in (None, "not-a-timestamp"):
                report = passing_report()
                if value is None:
                    del report["evaluated_at_utc"]
                else:
                    report["evaluated_at_utc"] = value
                for relative in DEPLOY_SCRIPTS:
                    with self.subTest(value=value, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0)
                        self.assertEqual(calls, [], completed.stdout + completed.stderr)

    def test_remote_paths_reject_parent_and_current_segments_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]
        cases = (
            ("scripts/deploy_staging.ps1", ("-RemoteDirectory", "/srv/mirae/../../tmp")),
            ("scripts/deploy_release.ps1", ("-RemoteDirectory", "/srv/mirae/../../tmp")),
            ("scripts/deploy_release.ps1", ("-RollbackDirectory", "/srv/mirae/./tmp")),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            for relative, arguments in cases:
                with self.subTest(script=relative, arguments=arguments):
                    completed, calls = run_deploy_script(
                        host,
                        relative,
                        passing_report(),
                        temp,
                        *arguments,
                    )
                    self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    self.assertEqual(calls, [], f"unsafe path invoked external commands: {calls}")

    def test_rollback_fails_when_rollback_image_is_absent(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            completed, calls = run_rollback_bash("absent", Path(temporary_directory))

        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn("docker image inspect rollback:test", calls[0])

    def test_rollback_requires_matching_active_image_and_healthy_service(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            temp = Path(temporary_directory)
            mismatch, mismatch_calls = run_rollback_bash("mismatch", temp)
            health_failure, health_calls = run_rollback_bash("health-fail", temp)
            healthy, healthy_calls = run_rollback_bash("healthy", temp)

        self.assertNotEqual(mismatch.returncode, 0, mismatch.stdout + mismatch.stderr)
        self.assertFalse(any(call.startswith("curl ") for call in mismatch_calls))
        self.assertNotEqual(health_failure.returncode, 0, health_failure.stdout + health_failure.stderr)
        self.assertTrue(any("/health" in call for call in health_calls))
        self.assertEqual(healthy.returncode, 0, healthy.stdout + healthy.stderr)
        self.assertTrue(any("/health" in call for call in healthy_calls))
        self.assertTrue(any("--output /dev/null" in call for call in healthy_calls))

    def test_gate_validation_precedes_any_docker_ssh_or_scp_invocation(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            gate_index = script.index("$gate = Assert-ReleaseGateReport")
            main_flow = script[gate_index:]
            for command in ("& docker", "& ssh", "& scp"):
                command_index = main_flow.find(command)
                if command_index >= 0:
                    self.assertGreater(command_index, 0, f"{relative}: {command}")
            self.assertIn("# RELEASE_GATE_VALIDATED_BEFORE_MUTATION", main_flow)

    def test_staging_builds_once_saves_hashes_and_packages_required_dense_summary(self):
        script = read("scripts/deploy_staging.ps1")

        self.assertEqual(len(re.findall(r"(?m)^\s*& docker build\b", script)), 1)
        self.assertRegex(script, r"(?m)^\s*& docker save\b")
        self.assertIn("Get-FileHash -LiteralPath $imageArchive -Algorithm SHA256", script)
        self.assertIn("data/derived/freeform_retrieval_summary.json", script)
        self.assertNotRegex(script, r"(?m)^\s*&?\s*docker compose build\b")
        self.assertIn("--no-build", script)
        self.assertIn("docker inspect", script)
        self.assertIn(".Image", script)
        self.assertIn(".Mounts", script)
        self.assertIn(".RW", script)

    def test_release_promotes_staging_image_and_has_immutable_rollback(self):
        script = read("scripts/deploy_release.ps1")

        self.assertIn("stagingImageId", script)
        self.assertIn("ExpectedImageId", script)
        self.assertIn("rollbackTag", script)
        self.assertIn("rollbackArchive", script)
        self.assertIn("docker tag", script)
        self.assertIn("docker save", script)
        self.assertIn("sha256sum", script)
        self.assertIn("chmod 0444", script)
        self.assertIn("--no-build", script)
        self.assertIn("Invoke-Rollback", script)
        self.assertRegex(script, r"catch\s*\{(?s:.*?)Invoke-Rollback")

    def test_scripts_do_not_read_or_write_secret_material(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            self.assertNotRegex(script, r"(?i)Get-Content[^\n]*(?:\.env|\.pem|api.?key)")
            self.assertNotRegex(script, r"(?i)Set-Content[^\n]*(?:\.env|\.pem|api.?key)")
            self.assertNotIn("CLOVASTUDIO_API_KEY=", script)

    def test_runbook_documents_refusal_same_image_mount_and_rollback_contracts(self):
        runbook = read("docs/operations/contest-server.md")

        self.assertIn("release-gate-summary-v1", runbook)
        self.assertIn("BLOCKED", runbook)
        self.assertIn("--no-build", runbook)
        self.assertIn("동일한 image ID", runbook)
        self.assertIn("RW=false", runbook)
        self.assertIn("rollback", runbook.casefold())


if __name__ == "__main__":
    unittest.main()
