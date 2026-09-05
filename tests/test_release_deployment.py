import ast
import re
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
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

FINAL_GATE_SCRIPTS = ("scripts/deploy_release.ps1",)


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


def passing_financial_report() -> dict[str, object]:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "financial-release-evaluation-v1",
        "finished_at_utc": timestamp,
        "inputs": {
            "base_sha256": BASE_SHA256,
            "overlay_sha256": OVERLAY_SHA256,
            "search_index_sha256": SEARCH_SHA256,
        },
        "cases": {
            "total": 856,
            "passed": 856,
            "failed": 0,
            "structured_accuracy": 1.0,
            "false_numeric_claim_count": 0,
            "ungrounded_verified_answer_count": 0,
            "failures": [],
        },
        "concurrency": {
            "request_count": 20,
            "error_count": 0,
            "p95_ms": 2_000.0,
        },
        "hard_gate_passed": True,
        "hard_gate_reasons": [],
    }


def passing_retrieval_report() -> dict[str, object]:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "freeform-retrieval-evaluation-v2",
        "evaluation_scope": "independent_hidden",
        "release_eligible": True,
        "status": "ok",
        "generated_at": timestamp,
        "database_sha256": BASE_SHA256,
        "overlay_sha256": OVERLAY_SHA256,
        "search_index_sha256": SEARCH_SHA256,
        "metrics": {
            "case_count": 120,
            "query_count": 492,
            "target_recall_at_20": 0.95,
            "wrong_issuer_count": 0,
            "wrong_version_count": 0,
            "hard_failure_count": 0,
        },
    }


def passing_judge_manifest(private_holdout: Path) -> dict[str, object]:
    return {
        "schema_version": "judge-stress-v2-manifest-v1",
        "case_count": 600,
        "split_counts": {"development": 480, "holdout": 120},
        "raw_artifacts": {
            "holdout": {
                "sha256": hashlib.sha256(private_holdout.read_bytes()).hexdigest(),
                "case_count": 120,
                "visibility": "git-ignored evaluator artifact",
            }
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


def write_staging_command_guards(
    directory: Path, marker: Path, expected_commit: str
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    real_git = shutil.which("git")
    if real_git is None:
        raise unittest.SkipTest("Git is required for staging execution tests")
    for command in ("git", "docker", "ssh", "scp"):
        if os.name == "nt":
            shim = directory / f"{command}.cmd"
            output = (
                f'if "%3"=="rev-parse" echo {expected_commit}\n'
                'if "%3"=="status" exit /b 0\n'
                'if "%3"=="check-ignore" exit /b 0\n'
                f'if "%3"=="archive" (\n  "{real_git}" %*\n  exit /b %ERRORLEVEL%\n)'
                if command == "git"
                else "rem no output"
            )
            exit_code = 0 if command == "git" else 97
            shim.write_text(
                "@echo off\n"
                f">>\"{marker}\" echo {command} %*\n"
                f"{output}\n"
                f"exit /b {exit_code}\n",
                encoding="utf-8",
            )
        else:
            shim = directory / command
            output = (
                f"case \"$*\" in *' rev-parse HEAD') printf '%s\\n' '{expected_commit}' ;; "
                "*' status --porcelain --untracked-files=no') exit 0 ;; "
                "*' check-ignore -q -- '*) exit 0 ;; "
                f"*' archive '*) exec {shlex.quote(real_git)} \"$@\" ;; esac"
                if command == "git"
                else ":"
            )
            exit_code = 0 if command == "git" else 97
            shim.write_text(
                "#!/bin/sh\n"
                f"printf '%s %s\\n' '{command}' \"$*\" >> '{marker}'\n"
                f"{output}\n"
                f"exit {exit_code}\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)


def extract_staging_evaluator_source() -> str:
    match = re.search(
        r"\$stagingEvaluatorSource\s*=\s*@'\r?\n(?P<script>.*?)\r?\n'@",
        read("scripts/deploy_staging.ps1"),
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("bounded staging evaluator was not found")
    return match.group("script")


def load_staging_evaluator_function(name: str):
    tree = ast.parse(extract_staging_evaluator_source())
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )
    if function is None:
        raise AssertionError(f"embedded staging evaluator function is missing: {name}")
    module = ast.Module(
        body=[
            ast.Import(names=[ast.alias(name="hashlib")]),
            ast.Import(names=[ast.alias(name="json")]),
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {}
    exec(compile(module, "embedded-staging-evaluator-function.py", "exec"), namespace)
    return namespace[name]


def write_build_context_guards(directory: Path, marker: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    guard = directory / "guard.py"
    guard.write_text(
        """from pathlib import Path
import sys

marker = Path(sys.argv[1])
command, *arguments = sys.argv[2:]
with marker.open("a", encoding="utf-8") as stream:
    stream.write(command + " " + " ".join(arguments) + "\\n")
if command == "docker" and arguments and arguments[0] == "build":
    context = Path(arguments[-1])
    forbidden = (
        context / "src" / "untracked-build-input.py",
        context / "config" / "untracked-build-input.json",
    )
    if any(path.exists() for path in forbidden):
        raise SystemExit(91)
    with marker.open("a", encoding="utf-8") as stream:
        stream.write("clean-build-context\\n")
raise SystemExit(97)
""",
        encoding="utf-8",
    )
    quoted_python = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
    for command in ("docker", "ssh", "scp"):
        if os.name == "nt":
            shim = directory / f"{command}.cmd"
            shim.write_text(
                "@echo off\n"
                f"{quoted_python} \"{guard}\" \"{marker}\" {command} %*\n"
                "exit /b %ERRORLEVEL%\n",
                encoding="utf-8",
            )
        else:
            shim = directory / command
            shim.write_text(
                "#!/bin/sh\n"
                f"exec {quoted_python} {shlex.quote(str(guard))} {shlex.quote(str(marker))} {command} \"$@\"\n",
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


def run_staging_script(
    host: str,
    financial: dict[str, object],
    retrieval: dict[str, object],
    temp: Path,
    private_holdout_path: Path,
    *extra_arguments: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    financial_path = temp / "financial_release_evaluation.json"
    retrieval_path = temp / "freeform_retrieval_summary.json"
    financial_path.write_text(json.dumps(financial), encoding="utf-8")
    retrieval_path.write_text(json.dumps(retrieval), encoding="utf-8")
    manifest_path = temp / "judge_stress_v2_manifest.json"
    manifest_path.write_text(
        json.dumps(passing_judge_manifest(private_holdout_path)), encoding="utf-8"
    )
    marker = temp / "staging-command.log"
    if marker.exists():
        marker.unlink()
    shim_directory = temp / "staging-command-guards"
    expected_commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    write_staging_command_guards(shim_directory, marker, expected_commit)
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(shim_directory), environment["PATH"]))
    command = [
        host,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts/deploy_staging.ps1"),
        "-ServerHost",
        "invalid.example",
        "-SshUser",
        "tester",
        "-ExpectedCommit",
        expected_commit,
        "-ExpectedBaseDbSha256",
        BASE_SHA256,
        "-ExpectedOverlayDbSha256",
        OVERLAY_SHA256,
        "-ExpectedSearchIndexSha256",
        SEARCH_SHA256,
        "-ExpectedRetrievalReportSha256",
        hashlib.sha256(retrieval_path.read_bytes()).hexdigest(),
        "-FinancialGatePath",
        str(financial_path),
        "-RetrievalGatePath",
        str(retrieval_path),
        "-PrivateHoldoutPath",
        str(private_holdout_path),
        "-JudgeManifestPath",
        str(manifest_path),
        "-ArtifactDirectory",
        str(temp / "artifacts"),
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

    def test_release_requires_final_pass_with_exact_identity(self):
        script = read("scripts/deploy_release.ps1")
        self.assertIn('release-gate-summary-v1', script)
        self.assertRegex(script, r'hard_gate_passed\s*-ne\s*\$true')
        self.assertRegex(script, r'release_state\s*-ne\s*"PASS"')
        for field in (
            "commit",
            "image_id",
            "base_sha256",
            "overlay_sha256",
            "search_index_sha256",
        ):
            self.assertIn(field, script)

    def test_current_blocked_release_report_cannot_authorize_production(self):
        report = json.loads(read("data/derived/release_gate_summary.json"))

        self.assertEqual(report["schema_version"], "release-gate-summary-v1")
        self.assertEqual(report["release_state"], "BLOCKED_HARD_GATE")
        self.assertIs(report["hard_gate_passed"], False)
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        with tempfile.TemporaryDirectory() as temporary_directory:
            completed, calls = run_deploy_script(
                hosts[0],
                "scripts/deploy_release.ps1",
                report,
                Path(temporary_directory),
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(calls, [], completed.stdout + completed.stderr)

    def test_gate_json_parsing_is_compatible_with_windows_powershell(self):
        for relative in ("scripts/deploy_staging.ps1", "scripts/deploy_release.ps1"):
            script = read(relative)
            self.assertNotIn("ConvertFrom-Json -Depth", script, relative)

    def test_gate_schema_requires_fresh_complete_content_free_summary(self):
        for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
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
                for relative in FINAL_GATE_SCRIPTS:
                    with self.subTest(value=value, script=relative):
                        completed, calls = run_deploy_script(host, relative, report, temp)
                        self.assertNotEqual(completed.returncode, 0)
                        self.assertEqual(calls, [], completed.stdout + completed.stderr)

    def test_staging_can_reach_candidate_build_without_a_final_release_report(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        ignored_root = ROOT / "eval" / "judge_stress_v2"
        ignored_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory(
            dir=ignored_root
        ) as private_directory:
            private_holdout = Path(private_directory) / "holdout.jsonl"
            private_holdout.write_text("{}\n" * 120, encoding="utf-8")
            completed, calls = run_staging_script(
                hosts[0],
                passing_financial_report(),
                passing_retrieval_report(),
                Path(temporary_directory),
                private_holdout,
            )
            artifact_directory = Path(temporary_directory) / "artifacts"
            pre_stage = json.loads(
                (artifact_directory / "pre_stage_attestation.json").read_text(
                    encoding="utf-8"
                )
            )
            final_gate_exists = (artifact_directory / "release_gate_summary.json").exists()

        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(calls and calls[0].startswith("git "), completed.stdout + completed.stderr)
        self.assertTrue(any(call.startswith("docker build ") for call in calls), calls)
        self.assertFalse(any(call.startswith(("ssh ", "scp ")) for call in calls), calls)
        self.assertEqual(pre_stage["stage_state"], "READY_FOR_STAGING")
        self.assertIs(pre_stage["final_release_passed"], False)
        self.assertFalse(final_gate_exists)

    def test_staging_rejects_release_ineligible_retrieval_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        ignored_root = ROOT / "eval" / "judge_stress_v2"
        ignored_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory(
            dir=ignored_root
        ) as private_directory:
            private_holdout = Path(private_directory) / "holdout.jsonl"
            private_holdout.write_text("{}\n" * 120, encoding="utf-8")
            retrieval = passing_retrieval_report()
            retrieval["release_eligible"] = False
            completed, calls = run_staging_script(
                hosts[0],
                passing_financial_report(),
                retrieval,
                Path(temporary_directory),
                private_holdout,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("release-eligible", completed.stdout + completed.stderr)
        self.assertEqual(calls, [])

    def test_untracked_src_and_config_files_cannot_enter_staging_build_context(self):
        hosts = powershell_hosts()
        git = shutil.which("git")
        if not hosts or git is None:
            self.skipTest("PowerShell and Git are required for build-context execution tests")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            repository = temp / "repository"
            (repository / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts" / "deploy_staging.ps1", repository / "scripts")
            tracked_files = {
                "Dockerfile": "FROM scratch\nCOPY src /src\nCOPY config /config\nCOPY eval /eval\n",
                ".dockerignore": ".git\n",
                ".gitignore": "eval/judge_stress_v2/\n",
                "pyproject.toml": "[project]\nname='archive-context-test'\nversion='0'\n",
                "compose.release.yaml": "services: {}\n",
                "src/tracked.py": "TRACKED = True\n",
                "config/tracked.json": "{}\n",
                "config/release_gate_contract.json": "{}\n",
                "eval/qa_cases.jsonl": "{}\n",
                "scripts/evaluate_release_candidate.py": "raise SystemExit(99)\n",
            }
            for relative, content in tracked_files.items():
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run([git, "init", "-q", str(repository)], check=True)
            subprocess.run([git, "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run([git, "-C", str(repository), "config", "user.name", "Task8B Test"], check=True)
            subprocess.run([git, "-C", str(repository), "add", "."], check=True)
            subprocess.run([git, "-C", str(repository), "commit", "-q", "-m", "fixture"], check=True)
            expected_commit = subprocess.check_output(
                [git, "-C", str(repository), "rev-parse", "HEAD"], text=True
            ).strip()

            (repository / "src" / "untracked-build-input.py").write_text(
                "UNTRUSTED = True\n", encoding="utf-8"
            )
            (repository / "config" / "untracked-build-input.json").write_text(
                '{"untrusted": true}\n', encoding="utf-8"
            )
            private_holdout = repository / "eval" / "judge_stress_v2" / "private.jsonl"
            private_holdout.parent.mkdir(parents=True)
            private_holdout.write_text("{}\n" * 120, encoding="utf-8")
            financial_path = temp / "financial.json"
            retrieval_path = temp / "retrieval.json"
            manifest_path = temp / "manifest.json"
            financial_path.write_text(json.dumps(passing_financial_report()), encoding="utf-8")
            retrieval_path.write_text(json.dumps(passing_retrieval_report()), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(passing_judge_manifest(private_holdout)), encoding="utf-8"
            )
            retrieval_sha256 = hashlib.sha256(retrieval_path.read_bytes()).hexdigest()

            marker = temp / "build-context-commands.log"
            shim_directory = temp / "guards"
            write_build_context_guards(shim_directory, marker)
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join((str(shim_directory), environment["PATH"]))
            command = [
                hosts[0], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(repository / "scripts" / "deploy_staging.ps1"),
                "-ServerHost", "invalid.example", "-SshUser", "tester",
                "-ExpectedCommit", expected_commit,
                "-ExpectedBaseDbSha256", BASE_SHA256,
                "-ExpectedOverlayDbSha256", OVERLAY_SHA256,
                "-ExpectedSearchIndexSha256", SEARCH_SHA256,
                "-ExpectedRetrievalReportSha256", retrieval_sha256,
                "-FinancialGatePath", str(financial_path),
                "-RetrievalGatePath", str(retrieval_path),
                "-PrivateHoldoutPath", str(private_holdout),
                "-JudgeManifestPath", str(manifest_path),
                "-ArtifactDirectory", str(temp / "artifacts"),
            ]
            completed = subprocess.run(
                command,
                cwd=repository,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("clean-build-context", calls, completed.stdout + completed.stderr)
        self.assertTrue(any(call.startswith("docker build ") for call in calls), calls)
        self.assertFalse(any(call.startswith(("ssh ", "scp ")) for call in calls), calls)

    def test_current_nonstaging_reports_block_before_build_or_remote_mutation(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        ignored_root = ROOT / "eval" / "judge_stress_v2"
        ignored_root.mkdir(parents=True, exist_ok=True)
        financial = json.loads(read("data/derived/financial_release_evaluation.json"))
        retrieval = json.loads(read("data/derived/freeform_retrieval_summary.json"))

        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory(
            dir=ignored_root
        ) as private_directory:
            private_holdout = Path(private_directory) / "holdout.jsonl"
            private_holdout.write_text("{}\n" * 120, encoding="utf-8")
            completed, calls = run_staging_script(
                hosts[0],
                financial,
                retrieval,
                Path(temporary_directory),
                private_holdout,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(any(call.startswith(("docker ", "ssh ", "scp ")) for call in calls), calls)

    def test_staging_evaluator_is_bounded_and_emits_only_content_free_results(self):
        source = extract_staging_evaluator_source()
        compile(source, "embedded-staging-evaluator.py", "exec")
        self.assertIn("concurrency=20", source)
        self.assertIn("private_holdout_available=True", source)
        self.assertIn("provider_available=True", source)
        self.assertIn("len(cases) != 600", source)
        self.assertNotRegex(source, r'["\'](?:question|answer|response|prompt|secret|api_key)["\']\s*:')
        self.assertIn("def assert_content_free", source)
        self.assertNotIn("in json.dumps(payload", source)

    def test_staging_health_identity_rejects_missing_wrong_or_extra_fields(self):
        validator = load_staging_evaluator_function("validate_health_identity")
        trusted = {
            "commit": COMMIT,
            "image_id": IMAGE_ID,
            "base_sha256": BASE_SHA256,
            "overlay_sha256": OVERLAY_SHA256,
            "search_index_sha256": SEARCH_SHA256,
        }
        invalid = [
            {},
            {"identity": {key: value for key, value in trusted.items() if key != "commit"}},
            {"identity": {**trusted, "image_id": "sha256:" + "f" * 64}},
            {"identity": {**trusted, "base_sha256": "f" * 64}},
            {"identity": {**trusted, "unexpected": "value"}},
        ]
        for health in invalid:
            with self.subTest(health=health), self.assertRaises(RuntimeError):
                validator(health, trusted)

    def test_staging_health_identity_accepts_only_exact_external_identity(self):
        validator = load_staging_evaluator_function("validate_health_identity")
        trusted = {
            "commit": COMMIT,
            "image_id": IMAGE_ID,
            "base_sha256": BASE_SHA256,
            "overlay_sha256": OVERLAY_SHA256,
            "search_index_sha256": SEARCH_SHA256,
        }
        actual, identity_sha256 = validator({"identity": dict(trusted)}, trusted)
        expected_sha256 = hashlib.sha256(
            json.dumps(trusted, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        self.assertEqual(actual, trusted)
        self.assertEqual(identity_sha256, expected_sha256)

    def test_staging_uses_actual_health_identity_and_rechecks_it_remotely(self):
        script = read("scripts/deploy_staging.ps1")
        source = extract_staging_evaluator_source()

        self.assertIn("validate_health_identity(health, identity)", source)
        self.assertIn("validate_health_identity(post_health, identity)", source)
        self.assertNotIn("class BoundRuntime", source)
        self.assertNotIn("def identity(self)", source)
        self.assertIn('"health_identity_sha256": health_identity_sha256', source)
        self.assertIn("expected_health_identity_sha256", script)
        self.assertIn("post-evaluation staging identity verification", script)

    def test_staging_runs_final_gate_only_after_8001_evaluation(self):
        script = read("scripts/deploy_staging.ps1")
        staging_deploy = script.find(
            "Invoke-RemoteScript -Target $target -ScriptText $remoteStageScript"
        )
        staging_evaluation = script.find("$stagingEvaluatorSource")
        final_gate = script.find(
            '& $PythonExecutable (Join-Path $repository "scripts/evaluate_release_candidate.py")'
        )

        self.assertGreaterEqual(staging_deploy, 0)
        self.assertGreaterEqual(staging_evaluation, 0)
        self.assertGreaterEqual(final_gate, 0)
        self.assertLess(staging_deploy, staging_evaluation)
        self.assertLess(staging_evaluation, final_gate)
        self.assertNotIn("Assert-ReleaseGateReport", script[:staging_deploy])

    def test_remote_paths_reject_parent_and_current_segments_before_external_commands(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        host = hosts[0]
        cases = (
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

    def test_staging_remote_path_rejects_parent_segments_before_build(self):
        hosts = powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is required for deployment execution tests")
        ignored_root = ROOT / "eval" / "judge_stress_v2"
        ignored_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory(
            dir=ignored_root
        ) as private_directory:
            private_holdout = Path(private_directory) / "holdout.jsonl"
            private_holdout.write_text("{}\n" * 120, encoding="utf-8")
            completed, calls = run_staging_script(
                hosts[0],
                passing_financial_report(),
                passing_retrieval_report(),
                Path(temporary_directory),
                private_holdout,
                "-RemoteDirectory",
                "/srv/mirae/../../tmp",
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("canonical path components", completed.stdout + completed.stderr)
        self.assertEqual(calls, [])

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
        script = read("scripts/deploy_release.ps1")
        gate_index = script.index("$gate = Assert-ReleaseGateReport")
        main_flow = script[gate_index:]
        for command in ("& docker", "& ssh", "& scp"):
            command_index = main_flow.find(command)
            if command_index >= 0:
                self.assertGreater(command_index, 0, command)
        self.assertIn("# RELEASE_GATE_VALIDATED_BEFORE_MUTATION", main_flow)

    def test_staging_builds_once_saves_hashes_and_packages_required_dense_summary(self):
        script = read("scripts/deploy_staging.ps1")

        self.assertEqual(len(re.findall(r"(?m)^\s*& docker build\b", script)), 1)
        self.assertRegex(script, r"(?m)^\s*& docker save\b")
        self.assertIn("Get-FileSha256 $imageArchive", script)
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
