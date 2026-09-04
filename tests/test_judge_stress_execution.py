from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from disclosure_db.judge_stress_execution import (
    FAULT_PROBE_KINDS,
    METAMORPHIC_PROBE_KINDS,
    PROBE_VERSION,
    SECURITY_PROBE_KINDS,
    ContractJudgeRuntime,
    FaultInjectingJudgeRuntime,
    JudgeObservation,
    JudgeRunOptions,
    build_case_probes,
    build_provider_probe_contract,
    compare_metamorphic_observations,
    run_case,
    run_suite,
    sanitize_result,
    select_execution_lane,
    validate_execution_result,
)
from disclosure_db.judge_stress_v2 import (
    build_development_cases,
    load_audited_sources,
    load_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _case(
    category: str,
    *,
    split: str = "development",
    case_id: str | None = None,
    question: str = "삼성전자의 2025년 매출액을 알려줘.",
    oracle: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "judge-stress-v2-case-v1",
        "case_id": case_id or f"case-{split}-{category}",
        "case_sha256": _sha(f"case:{split}:{category}:{case_id or ''}"),
        "split": split,
        "category": category,
        "question": question,
        "question_sha256": _sha(question),
        "issuer_group_id": "issuer-samsung",
        "document_group_id": "document-report-2025",
        "question_template_family_id": "template-financial",
        "source_group_id": "source-report-2025",
        "source": {"record_sha256": _sha("source")},
        "execution": {"mode": "single"},
        "oracle": oracle
        or {
            "kind": "exact_numeric",
            "value": "300.00",
            "unit": "억원",
            "scope": "consolidated",
            "evidence_ids": ["ev-2", "ev-1"],
        },
    }


class RecordingRuntime(ContractJudgeRuntime):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.provider_calls = 0

    def execute(self, lane, case, probe):  # type: ignore[no-untyped-def]
        self.calls.append((lane, probe.kind))
        if lane == "provider_answer":
            self.provider_calls += 1
        return super().execute(lane, case, probe)


def _manifest() -> dict[str, object]:
    return json.loads(
        (REPO_ROOT / "data" / "derived" / "judge_stress_v2_manifest.json").read_text(
            encoding="utf-8"
        )
    )


def _development_cases() -> list[dict[str, object]]:
    contract = load_contract(REPO_ROOT / "config" / "judge_stress_v2_contract.json")
    sources, _ = load_audited_sources(REPO_ROOT, contract)
    return build_development_cases(sources, contract)


def test_selects_exact_lane_and_provider_only_for_hidden_semantic_roots() -> None:
    expected = {
        "structured": "deterministic_answer",
        "alias_period_correction": "deterministic_answer",
        "free_form": "retrieval_precheck",
        "multi_evidence_judgment": "retrieval_precheck",
        "policy_adversarial": "policy_guard",
        "api_concurrency": "concurrency",
        "fault": "fault_injection",
    }
    for category, lane in expected.items():
        assert select_execution_lane(_case(category)) == lane

    for category in ("free_form", "multi_evidence_judgment"):
        assert select_execution_lane(_case(category, split="holdout")) == "provider_answer"
    assert select_execution_lane(_case("structured", split="holdout")) == "deterministic_answer"


def test_provider_probe_contract_has_42_hidden_roots_and_120_versioned_observations() -> None:
    contract = build_provider_probe_contract(_manifest())

    assert contract["probe_version"] == PROBE_VERSION
    assert contract["eligible_root_count"] == 42
    assert contract["observation_count"] == 120
    assert contract["category_root_counts"] == {
        "free_form": 24,
        "multi_evidence_judgment": 18,
    }
    observations = contract["observations"]
    assert len(observations) == len({row["probe_id"] for row in observations}) == 120
    assert len({row["case_id"] for row in observations}) == 42
    assert all(set(row) == {"case_id", "case_sha256", "probe_id", "probe_kind"} for row in observations)
    assert not any("question" in key or "oracle" in key for row in observations for key in row)


def test_hidden_provider_execution_uses_exactly_120_observations_for_42_roots() -> None:
    cases: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []
    for category, count in (("free_form", 24), ("multi_evidence_judgment", 18)):
        for index in range(count):
            case = _case(
                category,
                split="holdout",
                case_id=f"hidden-{category}-{index:02d}",
                oracle={
                    "kind": "evidence_judgment",
                    "evidence_ids": [f"ev-{category}-{index:02d}"],
                },
            )
            cases.append(case)
            manifest_rows.append(
                {
                    "case_id": case["case_id"],
                    "case_sha256": case["case_sha256"],
                    "split": "holdout",
                    "category": category,
                }
            )
    runtime = RecordingRuntime()
    run = run_suite(
        cases,
        lambda: runtime,
        JudgeRunOptions(private_holdout_available=True, provider_available=True),
        manifest={"cases": manifest_rows},
    )

    assert len(run.results) == 42
    assert sum(result.provider_call_count for result in run.results) == 120
    assert runtime.provider_calls == 120
    assert run.summary_metadata["provider_probe_observation_count"] == 120
    assert run.summary_metadata["runtime_release_eligible"] is False
    assert "non_release_runtime" in run.summary_metadata["hard_gate_reasons"]


def test_tracked_contract_records_provider_probe_observations_without_new_roots() -> None:
    contract = json.loads(
        (REPO_ROOT / "config" / "judge_stress_v2_contract.json").read_text(
            encoding="utf-8"
        )
    )

    assert contract["provider_probe_contract"] == {
        "probe_version": PROBE_VERSION,
        "eligible_root_count": 42,
        "observation_count": 120,
        "source_split": "holdout",
        "category_root_counts": {
            "free_form": 24,
            "multi_evidence_judgment": 18,
        },
    }
    assert contract["case_count"] == 600
    assert contract["split_counts"] == {"development": 480, "holdout": 120}


def test_builds_all_versioned_metamorphic_security_and_fault_probes_without_decoding() -> None:
    structured = build_case_probes(_case("structured"))
    assert {probe.kind for probe in structured} == set(METAMORPHIC_PROBE_KINDS)
    assert all(probe.version == PROBE_VERSION for probe in structured)
    assert len({probe.probe_id for probe in structured}) == len(structured)

    policy = build_case_probes(_case("policy_adversarial"))
    assert set(SECURITY_PROBE_KINDS) <= {probe.kind for probe in policy}
    assert any(probe.kind == "encoded_base64" and "aWdub3Jl" in probe.question for probe in policy)
    assert any(probe.kind == "encoded_url" and "%" in probe.question for probe in policy)
    assert any(probe.kind == "encoded_zero_width" and "\u200b" in probe.question for probe in policy)

    fault = build_case_probes(_case("fault"))
    assert {probe.kind for probe in fault} == set(FAULT_PROBE_KINDS)


def test_metamorphic_comparison_is_exact_for_answerability_decimal_unit_scope_conclusion_and_citations() -> None:
    base = JudgeObservation(
        status="answered",
        answerable=True,
        value="300.00",
        unit="억원",
        scope="consolidated",
        conclusion="stable",
        citation_ids=("ev-2", "ev-1"),
        latency_ms=1.0,
    )
    equivalent = JudgeObservation(
        status="answered",
        answerable=True,
        value="3E2",
        unit="억원",
        scope="consolidated",
        conclusion="stable",
        citation_ids=("ev-1", "ev-2"),
        latency_ms=2.0,
    )
    assert compare_metamorphic_observations(base, equivalent) == ()

    mutations = {
        "answerability": {"answerable": False},
        "value": {"value": "301"},
        "unit": {"unit": "원"},
        "scope": {"scope": "separate"},
        "conclusion": {"conclusion": "improved"},
        "citation": {"citation_ids": ("ev-1",)},
    }
    for expected_code, changes in mutations.items():
        changed = JudgeObservation(**{**base.__dict__, **changes})
        assert expected_code in compare_metamorphic_observations(base, changed)

    nonfinite = JudgeObservation(**{**base.__dict__, "value": "NaN"})
    assert "value_non_finite" in compare_metamorphic_observations(base, nonfinite)


def test_deterministic_policy_and_error_lanes_never_call_provider() -> None:
    runtime = RecordingRuntime()
    for category in (
        "structured",
        "alias_period_correction",
        "free_form",
        "multi_evidence_judgment",
        "policy_adversarial",
        "api_concurrency",
        "fault",
    ):
        result = run_case(_case(category), runtime, provider_allowed=False)
        assert result.provider_call_count == 0
        assert result.forbidden_provider_call_count == 0
    assert runtime.provider_calls == 0

    hidden = _case("free_form", split="holdout")
    blocked = run_case(hidden, runtime, provider_allowed=False)
    assert blocked.blocked_reasons == ("BLOCKED_PROVIDER",)
    assert runtime.provider_calls == 0

    allowed = run_case(hidden, runtime, provider_allowed=True)
    assert allowed.provider_call_count > 0
    assert runtime.provider_calls == allowed.provider_call_count


def test_security_and_fault_probes_fail_closed_and_emit_no_runtime_content() -> None:
    class HostileRuntime(RecordingRuntime):
        def execute(self, lane, case, probe):  # type: ignore[no-untyped-def]
            if lane == "policy_guard":
                return {
                    "status": "abstained",
                    "answerable": False,
                    "conclusion": "refused",
                    "provider_used": False,
                    "latency_ms": 1.0,
                    "raw_question": case["question"],
                    "raw_answer": "SECRET_VALUE",
                    "provider_body": "provider-secret-body",
                    "prompt": "system-prompt",
                }
            if lane == "fault_injection":
                raise TimeoutError("SECRET timeout body")
            return super().execute(lane, case, probe)

    runtime = HostileRuntime()
    policy_result = run_case(_case("policy_adversarial"), runtime, provider_allowed=False)
    fault_result = run_case(_case("fault"), runtime, provider_allowed=False)
    serialized = json.dumps(
        [sanitize_result(policy_result), sanitize_result(fault_result)], ensure_ascii=False
    )

    assert policy_result.security_failure_count == 0
    assert fault_result.passed
    assert "SECRET_VALUE" not in serialized
    assert "provider-secret-body" not in serialized
    assert "system-prompt" not in serialized
    assert "SECRET timeout body" not in serialized
    assert "raw_question" not in serialized and "raw_answer" not in serialized


def test_malformed_observation_fails_closed_and_forbidden_provider_use_hard_fails() -> None:
    class MalformedRuntime(ContractJudgeRuntime):
        def execute(self, lane, case, probe):  # type: ignore[no-untyped-def]
            return {
                "status": "answered",
                "answerable": "yes",
                "provider_used": "false",
                "latency_ms": float("nan"),
                "provider_body": "SECRET malformed body",
            }

    structured = run_case(_case("structured"), MalformedRuntime(), provider_allowed=False)
    fault = run_case(_case("fault"), MalformedRuntime(), provider_allowed=False)

    assert structured.passed is False
    assert structured.evaluator_error_count > 0
    assert "evaluator_error" in structured.failure_codes
    assert fault.passed is True
    assert fault.evaluator_error_count == 0
    assert "SECRET malformed body" not in json.dumps(sanitize_result(fault))

    class SneakyProviderRuntime(ContractJudgeRuntime):
        def execute(self, lane, case, probe):  # type: ignore[no-untyped-def]
            observed = super().execute(lane, case, probe)
            return JudgeObservation(**{**observed.__dict__, "provider_used": True})

    forbidden = run_case(
        _case("structured"), SneakyProviderRuntime(), provider_allowed=False
    )
    assert forbidden.passed is False
    assert forbidden.forbidden_provider_call_count > 0
    assert forbidden.failure_category == "security"


def test_malformed_judge_observation_instance_fails_closed() -> None:
    class MalformedDataclassRuntime(ContractJudgeRuntime):
        def execute(self, lane, case, probe):
            return JudgeObservation(
                status="answered",
                answerable=True,
                unit=7,  # type: ignore[arg-type]
                scope=[],  # type: ignore[arg-type]
                conclusion={},  # type: ignore[arg-type]
                citation_ids=(42,),  # type: ignore[arg-type]
            )

    case = _case("structured", split="development")
    result = run_case(case, MalformedDataclassRuntime(), provider_allowed=False)

    assert result.passed is False
    assert result.evaluator_error_count == len(METAMORPHIC_PROBE_KINDS)
    assert result.failure_category == "runtime"


def test_restart_fault_rebuilds_runtime_and_detects_identity_change() -> None:
    class ChangedIdentityRuntime(ContractJudgeRuntime):
        def identity(self) -> str:
            return "identity-after"

    class RestartingRuntime(ContractJudgeRuntime):
        restart_calls = 0

        def identity(self) -> str:
            return "identity-before"

        def restart(self):  # type: ignore[no-untyped-def]
            self.restart_calls += 1
            return ChangedIdentityRuntime()

    runtime = RestartingRuntime()
    result = run_case(_case("fault"), runtime, provider_allowed=False)

    assert runtime.restart_calls == 1
    assert result.passed is False
    assert "restart_identity_mismatch" in result.failure_codes
    assert result.runtime_identity_before_sha256 != result.runtime_identity_after_sha256


def test_bounded_fake_fault_adapter_covers_provider_dense_and_restart_failures() -> None:
    runtime = FaultInjectingJudgeRuntime()
    result = run_case(_case("fault"), runtime, provider_allowed=False)

    assert result.passed is True
    assert result.evaluator_error_count == 0
    assert result.provider_call_count == 0
    assert result.forbidden_provider_call_count == 0
    assert set(runtime.injected_faults) == set(FAULT_PROBE_KINDS[:-1])
    serialized = json.dumps(sanitize_result(result))
    assert "provider response body" not in serialized
    assert "dense response body" not in serialized


def test_twenty_request_concurrency_and_restart_identity_are_measured() -> None:
    runtime = RecordingRuntime()
    concurrent = run_case(_case("api_concurrency"), runtime, provider_allowed=False)
    restart = run_case(_case("fault"), runtime, provider_allowed=False)

    assert concurrent.concurrency_request_count == 20
    assert concurrent.concurrency_error_count == 0
    assert concurrent.concurrency_p95_ms is not None
    assert concurrent.concurrency_p95_ms >= 0
    assert restart.runtime_identity_before_sha256 == restart.runtime_identity_after_sha256
    assert restart.runtime_identity_before_sha256 is not None


def test_validate_execution_result_rejects_duplicate_stale_or_mismatched_probe_identity() -> None:
    case = _case("structured")
    result = sanitize_result(run_case(case, RecordingRuntime(), provider_allowed=False))
    manifest_case = {
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "split": case["split"],
        "category": case["category"],
    }
    validate_execution_result(result, manifest_case)

    duplicate = deepcopy(result)
    duplicate["probe_ids"] = [result["probe_ids"][0], result["probe_ids"][0]]
    with pytest.raises(ValueError, match="duplicate probe"):
        validate_execution_result(duplicate, manifest_case)

    stale = deepcopy(result)
    stale["probe_version"] = "judge-probes-v0"
    with pytest.raises(ValueError, match="probe version"):
        validate_execution_result(stale, manifest_case)

    mismatched = deepcopy(result)
    mismatched["case_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="case hash"):
        validate_execution_result(mismatched, manifest_case)


def test_development_run_is_partial_blocked_and_sanitized() -> None:
    cases = _development_cases()
    run = run_suite(
        cases,
        ContractJudgeRuntime,
        JudgeRunOptions(
            private_holdout_available=False,
            provider_available=False,
            concurrency=20,
        ),
        manifest=_manifest(),
    )

    assert len(run.results) == 480
    assert run.summary_metadata["status"] == "PARTIAL"
    assert run.summary_metadata["hard_gate_passed"] is False
    assert run.summary_metadata["runtime_release_eligible"] is False
    assert "BLOCKED_PRIVATE_HOLDOUT" in run.summary_metadata["blocked_reasons"]
    assert "BLOCKED_PROVIDER" in run.summary_metadata["blocked_reasons"]
    assert run.summary_metadata["provider_eligible_root_count"] == 42
    assert run.summary_metadata["provider_probe_observation_count"] == 120
    assert run.summary_metadata["provider_call_count"] == 0
    assert run.summary_metadata["forbidden_provider_call_count"] == 0
    assert run.summary_metadata["evaluator_error_count"] == 0
    assert run.summary_metadata["security_failure_count"] == 0

    payload = json.dumps(
        {
            "results": [sanitize_result(result) for result in run.results],
            "metadata": run.summary_metadata,
        },
        ensure_ascii=False,
    )
    for forbidden in ("question", "raw_answer", "provider_body", "prompt", "secret"):
        assert f'"{forbidden}"' not in payload.lower()


def test_run_cli_writes_partial_sanitized_reports_without_private_input(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    failures = tmp_path / "failures.jsonl"
    summary = tmp_path / "summary.json"
    html = tmp_path / "summary.html"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_judge_stress_v2.py"),
            "--repository-root",
            str(REPO_ROOT),
            "--manifest",
            str(REPO_ROOT / "data" / "derived" / "judge_stress_v2_manifest.json"),
            "--results",
            str(results),
            "--failures",
            str(failures),
            "--json-summary",
            str(summary),
            "--html-summary",
            str(html),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["status"] == "PARTIAL"
    assert report["hard_gate_passed"] is False
    assert "BLOCKED_PRIVATE_HOLDOUT" in report["blocked_reasons"]
    assert report["provider_probe_observation_count"] == 120
    assert len(results.read_text(encoding="utf-8").splitlines()) == 480
    assert failures.read_text(encoding="utf-8") == ""
    assert "BLOCKED" in html.read_text(encoding="utf-8")

    serialized = results.read_text(encoding="utf-8") + summary.read_text(encoding="utf-8") + html.read_text(encoding="utf-8")
    for forbidden in ("raw_question", "raw_answer", "provider_body", "system-prompt", "api_key"):
        assert forbidden not in serialized
    assert "삼성전자의" not in serialized
