from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from disclosure_db.release_gate import (
    evaluate_release_gate,
    load_release_contract,
    render_release_html,
    write_release_report,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
IDENTITY = {
    "commit": "0e4e5b35d6bd429a6df68828d529829611afee6c",
    "image_id": "sha256:" + "1" * 64,
    "base_sha256": "2" * 64,
    "overlay_sha256": "3" * 64,
    "search_index_sha256": "4" * 64,
}


def _contract() -> dict[str, object]:
    return {
        "schema_version": "release-gate-contract-v1",
        "freshness": {"max_age_seconds": 86_400, "future_skew_seconds": 300},
        "financial": {"required_total": 856, "required_passed": 856},
        "judge": {
            "required_total": 600,
            "required_passed": 600,
            "numeric_exactness": 1.0,
            "claim_citation_coverage": 1.0,
            "answerability_agreement_min": 0.95,
            "metamorphic_consistency_min": 0.98,
            "provider_eligible_root_count": 42,
            "provider_probe_observation_count": 120,
            "provider_call_count": 120,
            "provider_p95_ms_max": 10_000,
        },
        "retrieval": {"recall_at_20_min": 0.95},
        "concurrency": {
            "request_count": 20,
            "error_count": 0,
            "p95_ms_max": 2_000,
        },
        "zero_counters": [
            "hallucinated_numeric_claim_count",
            "unknown_citation_count",
            "cross_filing_citation_count",
            "policy_violation_count",
            "secret_leak_count",
            "evaluator_error_count",
            "forbidden_provider_call_count",
            "deterministic_provider_call_count",
        ],
        "identity_fields": [
            "commit",
            "image_id",
            "base_sha256",
            "overlay_sha256",
            "search_index_sha256",
        ],
    }


def _passing_reports() -> tuple[dict[str, object], ...]:
    timestamp = (NOW - timedelta(minutes=5)).isoformat()
    financial = {
        "finished_at_utc": timestamp,
        "identity": deepcopy(IDENTITY),
        "cases": {"total": 856, "passed": 856, "failed": 0},
    }
    raw_results = [
        {
            "case_id": f"judge-case-{index:04d}",
            "passed": True,
            "numeric_exactness": 1.0,
            "claim_citation_coverage": 1.0,
            "evaluator_error_count": 0,
            "forbidden_provider_call_count": 0,
        }
        for index in range(600)
    ]
    judge = {
        "finished_at_utc": timestamp,
        "identity": deepcopy(IDENTITY),
        "case_count": 600,
        "evaluated_count": 600,
        "pass_count": 600,
        "failure_count": 0,
        "numeric_exactness": 1.0,
        "claim_citation_coverage": 1.0,
        "answerability_agreement": 0.95,
        "metamorphic_consistency": 0.98,
        "provider_eligible_root_count": 42,
        "provider_probe_observation_count": 120,
        "provider_call_count": 120,
        "provider_p95_ms": 10_000.0,
        "concurrency_request_count": 20,
        "concurrency_error_count": 0,
        "concurrency_p95_ms": 2_000.0,
        "hallucinated_numeric_claim_count": 0,
        "unknown_citation_count": 0,
        "cross_filing_citation_count": 0,
        "policy_violation_count": 0,
        "secret_leak_count": 0,
        "evaluator_error_count": 0,
        "forbidden_provider_call_count": 0,
        "deterministic_provider_call_count": 0,
        "results": raw_results,
        "security_observations": [
            {
                "case_id": f"judge-case-{index:04d}",
                "hallucinated_numeric_claim_count": 0,
                "unknown_citation_count": 0,
                "cross_filing_citation_count": 0,
                "policy_violation_count": 0,
                "secret_leak_count": 0,
            }
            for index in range(600)
        ],
    }
    retrieval = {
        "generated_at": timestamp,
        "identity": deepcopy(IDENTITY),
        "metrics": {"target_recall_at_20": 0.95},
    }
    deployment = {
        "evaluated_at_utc": timestamp,
        "identity": deepcopy(IDENTITY),
    }
    return financial, judge, retrieval, deployment


def _evaluate(reports: tuple[dict[str, object], ...]):
    return evaluate_release_gate(
        *reports,
        contract=_contract(),
        expected_identity=IDENTITY,
        actual_commit=IDENTITY["commit"],
        now=NOW,
    )


def test_exact_boundary_candidate_passes_only_after_independent_recomputation() -> None:
    financial, judge, retrieval, deployment = _passing_reports()
    financial["hard_gate_passed"] = False
    judge["hard_gate_passed"] = False
    retrieval["hard_gate_passed"] = False

    result = _evaluate((financial, judge, retrieval, deployment))

    assert result.hard_gate_passed is True
    assert result.release_state == "PASS"
    assert result.hard_gate_reasons == ()
    assert result.metrics["financial_passed"] == 856
    assert result.metrics["retrieval_recall_at_20"] == 0.95


def test_judge_outcome_and_exactness_are_recomputed_from_raw_results() -> None:
    financial, judge, retrieval, deployment = _passing_reports()
    judge["pass_count"] = 600
    judge["failure_count"] = 0
    judge["numeric_exactness"] = 1.0
    judge["claim_citation_coverage"] = 1.0
    judge["results"][0].update(  # type: ignore[index,union-attr]
        {
            "passed": False,
            "numeric_exactness": 0.0,
            "claim_citation_coverage": 0.0,
        }
    )

    result = _evaluate((financial, judge, retrieval, deployment))

    assert result.hard_gate_passed is False
    assert result.metrics["judge_pass_count"] == 599
    assert result.metrics["judge_failure_count"] == 1
    assert result.metrics["judge_numeric_exactness"] == pytest.approx(599 / 600)
    assert result.metrics["judge_claim_citation_coverage"] == pytest.approx(599 / 600)
    assert any("mismatch:judge.pass_count" in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize("raw_results", [None, [], [{"case_id": "only-one"}]])
def test_missing_or_incomplete_raw_judge_results_fail_closed(raw_results: object) -> None:
    reports = list(_passing_reports())
    if raw_results is None:
        reports[1].pop("results")
    else:
        reports[1]["results"] = raw_results

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any("judge.results" in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize(
    ("target", "path", "value", "reason_fragment"),
    [
        ("financial", ("cases", "passed"), 855, "financial.passed"),
        ("judge", ("evaluated_count",), 599, "judge.evaluated_count"),
        ("judge", ("pass_count",), 599, "judge.pass_count"),
        ("judge", ("numeric_exactness",), 0.999, "judge.numeric_exactness"),
        ("judge", ("claim_citation_coverage",), 0.999, "judge.claim_citation_coverage"),
        ("judge", ("answerability_agreement",), 0.9499, "judge.answerability_agreement"),
        ("judge", ("metamorphic_consistency",), 0.9799, "judge.metamorphic_consistency"),
        ("retrieval", ("metrics", "target_recall_at_20"), 0.9499, "retrieval.recall_at_20"),
        ("judge", ("concurrency_request_count",), 19, "judge.concurrency_request_count"),
        ("judge", ("concurrency_error_count",), 1, "judge.concurrency_error_count"),
        ("judge", ("concurrency_p95_ms",), 2000.01, "judge.concurrency_p95_ms"),
        ("judge", ("provider_eligible_root_count",), 41, "judge.provider_eligible_root_count"),
        ("judge", ("provider_probe_observation_count",), 119, "judge.provider_probe_observation_count"),
        ("judge", ("provider_call_count",), 119, "judge.provider_call_count"),
        ("judge", ("provider_p95_ms",), 10000.01, "judge.provider_p95_ms"),
        ("judge", ("hallucinated_numeric_claim_count",), 1, "judge.hallucinated_numeric_claim_count"),
        ("judge", ("unknown_citation_count",), 1, "judge.unknown_citation_count"),
        ("judge", ("cross_filing_citation_count",), 1, "judge.cross_filing_citation_count"),
        ("judge", ("policy_violation_count",), 1, "judge.policy_violation_count"),
        ("judge", ("secret_leak_count",), 1, "judge.secret_leak_count"),
        ("judge", ("evaluator_error_count",), 1, "judge.evaluator_error_count"),
        ("judge", ("forbidden_provider_call_count",), 1, "judge.forbidden_provider_call_count"),
        ("judge", ("deterministic_provider_call_count",), 1, "judge.deterministic_provider_call_count"),
    ],
)
def test_each_hard_gate_threshold_blocks(
    target: str, path: tuple[str, ...], value: object, reason_fragment: str
) -> None:
    reports = list(_passing_reports())
    report = reports[{"financial": 0, "judge": 1, "retrieval": 2}[target]]
    cursor = report
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[index,assignment]
    cursor[path[-1]] = value  # type: ignore[index]

    result = _evaluate(tuple(reports))

    assert result.release_state == "BLOCKED_HARD_GATE"
    assert result.hard_gate_passed is False
    assert any(reason_fragment in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf"), -float("inf")])
def test_missing_or_nonfinite_metric_blocks(bad_value: object) -> None:
    reports = list(_passing_reports())
    reports[1]["provider_p95_ms"] = bad_value

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any("judge.provider_p95_ms" in reason for reason in result.hard_gate_reasons)
    assert not any(
        isinstance(value, float) and not math.isfinite(value)
        for value in result.metrics.values()
    )


def test_stale_future_and_identity_mismatch_block() -> None:
    stale = list(_passing_reports())
    stale[0]["finished_at_utc"] = (NOW - timedelta(days=2)).isoformat()
    future = list(_passing_reports())
    future[1]["finished_at_utc"] = (NOW + timedelta(minutes=6)).isoformat()
    mismatch = list(_passing_reports())
    mismatch[2]["identity"]["overlay_sha256"] = "9" * 64  # type: ignore[index]

    stale_result = _evaluate(tuple(stale))
    future_result = _evaluate(tuple(future))
    mismatch_result = _evaluate(tuple(mismatch))

    assert any("financial.stale" in reason for reason in stale_result.hard_gate_reasons)
    assert any("judge.future" in reason for reason in future_result.hard_gate_reasons)
    assert any("identity.overlay_sha256" in reason for reason in mismatch_result.hard_gate_reasons)


def test_self_consistent_report_identity_cannot_replace_external_trust_anchors() -> None:
    reports = _passing_reports()

    result = evaluate_release_gate(
        *reports,
        contract=_contract(),
        actual_commit=IDENTITY["commit"],
        now=NOW,
    )

    assert result.hard_gate_passed is False
    assert any("trust_anchor" in reason for reason in result.hard_gate_reasons)


def test_every_report_must_match_external_trust_anchors() -> None:
    reports = list(_passing_reports())
    reports[0]["identity"] = {**IDENTITY, "image_id": "sha256:" + "9" * 64}

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any(
        "financial.identity.image_id" in reason
        for reason in result.hard_gate_reasons
    )


def test_trusted_commit_must_match_actual_full_head() -> None:
    result = evaluate_release_gate(
        *_passing_reports(),
        contract=_contract(),
        expected_identity=IDENTITY,
        actual_commit="f" * 40,
        now=NOW,
    )

    assert result.hard_gate_passed is False
    assert any("trust_anchor.commit:head_mismatch" in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize(
    ("target", "path", "value"),
    [
        ("financial", ("cases", "passed"), 856.0),
        ("financial", ("cases", "failed"), True),
        ("judge", ("case_count",), -1),
        ("judge", ("provider_call_count",), 120.0),
    ],
)
def test_counts_require_exact_nonnegative_integers(
    target: str, path: tuple[str, ...], value: object
) -> None:
    reports = list(_passing_reports())
    report = reports[{"financial": 0, "judge": 1}[target]]
    cursor = report
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[index,assignment]
    cursor[path[-1]] = value  # type: ignore[index]

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any("invalid_domain" in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_ratios_require_finite_closed_unit_interval(value: object) -> None:
    reports = list(_passing_reports())
    reports[1]["answerability_agreement"] = value

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any("judge.answerability_agreement" in reason for reason in result.hard_gate_reasons)


@pytest.mark.parametrize("value", [-0.01, float("nan"), float("inf"), True])
def test_latencies_require_finite_nonnegative_numbers(value: object) -> None:
    reports = list(_passing_reports())
    reports[1]["provider_p95_ms"] = value

    result = _evaluate(tuple(reports))

    assert result.hard_gate_passed is False
    assert any("judge.provider_p95_ms" in reason for reason in result.hard_gate_reasons)


def test_current_tracked_artifacts_are_blocked() -> None:
    financial = json.loads(
        (REPO_ROOT / "data/derived/financial_release_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    judge = json.loads(
        (REPO_ROOT / "data/derived/judge_stress_v2_summary.json").read_text(
            encoding="utf-8"
        )
    )
    retrieval = json.loads(
        (REPO_ROOT / "data/derived/freeform_retrieval_summary.json").read_text(
            encoding="utf-8"
        )
    )

    result = evaluate_release_gate(
        financial,
        judge,
        retrieval,
        {},
        contract=_contract(),
        expected_identity=IDENTITY,
        actual_commit=IDENTITY["commit"],
        now=NOW,
    )

    assert result.release_state == "BLOCKED_HARD_GATE"
    assert result.hard_gate_passed is False
    assert any("judge.results" in reason for reason in result.hard_gate_reasons)
    assert any("retrieval.recall_at_20" in reason for reason in result.hard_gate_reasons)
    assert any("deployment" in reason for reason in result.hard_gate_reasons)


def test_reports_are_content_free_and_contract_is_strict(tmp_path: Path) -> None:
    contract = load_release_contract(REPO_ROOT / "config/release_gate_contract.json")
    assert contract["financial"]["required_total"] == 856
    assert contract["judge"]["provider_call_count"] == 120

    reports = list(_passing_reports())
    reports[1]["raw_question"] = "DO-NOT-LEAK-QUESTION"
    reports[1]["answer"] = "DO-NOT-LEAK-ANSWER"
    reports[1]["provider_body"] = "DO-NOT-LEAK-PROVIDER"
    reports[3]["api_key"] = "DO-NOT-LEAK-SECRET"
    result = _evaluate(tuple(reports))
    json_path = tmp_path / "release.json"
    html_path = tmp_path / "release.html"

    write_release_report(result, json_path, html_path)
    rendered = json_path.read_text(encoding="utf-8") + render_release_html(result)

    assert result.hard_gate_passed is True
    assert "DO-NOT-LEAK" not in rendered
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["financial"].__setitem__("required_total", 1),
        lambda value: value["judge"].__setitem__("numeric_exactness", 0.5),
        lambda value: value["retrieval"].__setitem__("recall_at_20_min", 0.1),
        lambda value: value.__setitem__(
            "zero_counters", value["zero_counters"][:-1]
        ),
        lambda value: value.__setitem__(
            "identity_fields", value["identity_fields"][:-1]
        ),
    ],
)
def test_contract_cannot_lower_or_remove_fixed_hard_gates(mutate) -> None:
    contract = _contract()
    mutate(contract)

    with pytest.raises(ValueError, match="fixed release hard gates"):
        evaluate_release_gate(
            *_passing_reports(),
            contract=contract,
            expected_identity=IDENTITY,
            actual_commit=IDENTITY["commit"],
            now=NOW,
        )


def test_cli_writes_blocked_report_and_returns_nonzero_for_current_artifacts(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "release.json"
    html_path = tmp_path / "release.html"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/evaluate_release_candidate.py"),
            "--repository-root",
            str(REPO_ROOT),
            "--now",
            NOW.isoformat(),
            "--json-summary",
            str(json_path),
            "--html-summary",
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1, completed.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["release_state"] == "BLOCKED_HARD_GATE"
    assert report["hard_gate_passed"] is False
    assert "question" not in json_path.read_text(encoding="utf-8").lower()
    assert html_path.exists()


def test_cli_accepts_explicit_trust_anchor_and_matches_actual_head(
    tmp_path: Path,
) -> None:
    financial, judge, retrieval, deployment = _passing_reports()
    paths: list[Path] = []
    for name, report in zip(
        ("financial", "judge", "retrieval", "deployment"),
        (financial, judge, retrieval, deployment),
        strict=True,
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    anchor_path = tmp_path / "trusted-identity.json"
    anchor_path.write_text(json.dumps(IDENTITY), encoding="utf-8")
    json_path = tmp_path / "release.json"
    html_path = tmp_path / "release.html"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/evaluate_release_candidate.py"),
            "--repository-root",
            str(REPO_ROOT),
            "--financial",
            str(paths[0]),
            "--judge",
            str(paths[1]),
            "--retrieval",
            str(paths[2]),
            "--deployment",
            str(paths[3]),
            "--expected-identity",
            str(anchor_path),
            "--now",
            NOW.isoformat(),
            "--json-summary",
            str(json_path),
            "--html-summary",
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["release_state"] == "PASS"
    assert report["identity"] == IDENTITY


def test_cli_recomputes_security_counters_from_observations_not_forged_summary(
    tmp_path: Path,
) -> None:
    financial, judge, retrieval, deployment = _passing_reports()
    judge["hallucinated_numeric_claim_count"] = 0
    judge["security_observations"][0][  # type: ignore[index]
        "hallucinated_numeric_claim_count"
    ] = 1
    paths: list[Path] = []
    for name, report in zip(
        ("financial", "judge", "retrieval", "deployment"),
        (financial, judge, retrieval, deployment),
        strict=True,
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    anchor_path = tmp_path / "trusted-identity.json"
    anchor_path.write_text(json.dumps(IDENTITY), encoding="utf-8")
    json_path = tmp_path / "release.json"
    html_path = tmp_path / "release.html"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/evaluate_release_candidate.py"),
            "--repository-root",
            str(REPO_ROOT),
            "--financial",
            str(paths[0]),
            "--judge",
            str(paths[1]),
            "--retrieval",
            str(paths[2]),
            "--deployment",
            str(paths[3]),
            "--expected-identity",
            str(anchor_path),
            "--now",
            NOW.isoformat(),
            "--json-summary",
            str(json_path),
            "--html-summary",
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1, completed.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["release_state"] == "BLOCKED_HARD_GATE"
    assert report["metrics"]["hallucinated_numeric_claim_count"] == 1
    assert any(
        "judge.hallucinated_numeric_claim_count" in reason
        for reason in report["hard_gate_reasons"]
    )
