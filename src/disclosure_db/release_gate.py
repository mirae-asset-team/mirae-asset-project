"""Independent, fail-closed release-candidate hard gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence


SCHEMA_VERSION = "release-gate-summary-v1"
PASS = "PASS"
BLOCKED = "BLOCKED_HARD_GATE"
_SOURCE_NAMES = ("financial", "judge", "retrieval", "deployment")
_TIMESTAMP_FIELDS = ("finished_at_utc", "generated_at", "evaluated_at_utc")
_FIXED_VALUES = {
    ("freshness", "max_age_seconds"): 86_400,
    ("freshness", "future_skew_seconds"): 300,
    ("financial", "required_total"): 856,
    ("financial", "required_passed"): 856,
    ("judge", "required_total"): 600,
    ("judge", "required_passed"): 600,
    ("judge", "numeric_exactness"): 1.0,
    ("judge", "claim_citation_coverage"): 1.0,
    ("judge", "answerability_agreement_min"): 0.95,
    ("judge", "metamorphic_consistency_min"): 0.98,
    ("judge", "provider_eligible_root_count"): 42,
    ("judge", "provider_probe_observation_count"): 120,
    ("judge", "provider_call_count"): 120,
    ("judge", "provider_p95_ms_max"): 10_000,
    ("retrieval", "recall_at_20_min"): 0.95,
    ("concurrency", "request_count"): 20,
    ("concurrency", "error_count"): 0,
    ("concurrency", "p95_ms_max"): 2_000,
}
_FIXED_ZERO_COUNTERS = (
    "hallucinated_numeric_claim_count",
    "unknown_citation_count",
    "cross_filing_citation_count",
    "policy_violation_count",
    "secret_leak_count",
    "evaluator_error_count",
    "forbidden_provider_call_count",
    "deterministic_provider_call_count",
)
_INDEPENDENT_SECURITY_COUNTERS = _FIXED_ZERO_COUNTERS[:5]
_FIXED_IDENTITY_FIELDS = (
    "commit",
    "image_id",
    "base_sha256",
    "overlay_sha256",
    "search_index_sha256",
)
_IDENTITY_PATTERNS = {
    "commit": re.compile(r"[0-9a-fA-F]{40}"),
    "image_id": re.compile(r"sha256:[0-9a-fA-F]{64}"),
    "base_sha256": re.compile(r"[0-9a-fA-F]{64}"),
    "overlay_sha256": re.compile(r"[0-9a-fA-F]{64}"),
    "search_index_sha256": re.compile(r"[0-9a-fA-F]{64}"),
}


@dataclass(frozen=True)
class ReleaseGateResult:
    release_state: str
    hard_gate_passed: bool
    hard_gate_reasons: tuple[str, ...]
    evaluated_at_utc: str
    metrics: Mapping[str, int | float | None]
    identity: Mapping[str, str]
    source_timestamps: Mapping[str, str | None]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "evaluated_at_utc": self.evaluated_at_utc,
            "release_state": self.release_state,
            "hard_gate_passed": self.hard_gate_passed,
            "hard_gate_reasons": list(self.hard_gate_reasons),
            "metrics": dict(self.metrics),
            "identity": dict(self.identity),
            "source_timestamps": dict(self.source_timestamps),
        }


def load_release_contract(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("release gate contract must be an object")
    _validate_contract(value)
    return value


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _contract_section(contract: Mapping[str, object], name: str) -> Mapping[str, object]:
    section = contract.get(name)
    if not isinstance(section, Mapping):
        raise ValueError(f"release gate contract {name} must be an object")
    return section


def _contract_count(section: Mapping[str, object], field: str) -> int:
    value = section.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(
            f"release gate contract {field} must be a non-negative integer"
        )
    return value


def _contract_ratio(section: Mapping[str, object], field: str) -> float:
    value = section.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"release gate contract {field} must be a ratio")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"release gate contract {field} must be between zero and one")
    return result


def _contract_latency(section: Mapping[str, object], field: str) -> float:
    value = section.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"release gate contract {field} must be a latency")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"release gate contract {field} must be finite and non-negative")
    return result


def _validate_contract(contract: Mapping[str, object]) -> None:
    if contract.get("schema_version") != "release-gate-contract-v1":
        raise ValueError("unsupported release gate contract")
    freshness = _contract_section(contract, "freshness")
    financial = _contract_section(contract, "financial")
    judge = _contract_section(contract, "judge")
    retrieval = _contract_section(contract, "retrieval")
    concurrency = _contract_section(contract, "concurrency")
    for section, fields in (
        (freshness, ("max_age_seconds", "future_skew_seconds")),
        (financial, ("required_total", "required_passed")),
        (
            judge,
            (
                "required_total",
                "required_passed",
                "provider_eligible_root_count",
                "provider_probe_observation_count",
                "provider_call_count",
            ),
        ),
        (concurrency, ("request_count", "error_count")),
    ):
        for field in fields:
            _contract_count(section, field)
    for section, fields in (
        (
            judge,
            (
                "numeric_exactness",
                "claim_citation_coverage",
                "answerability_agreement_min",
                "metamorphic_consistency_min",
            ),
        ),
        (retrieval, ("recall_at_20_min",)),
    ):
        for field in fields:
            _contract_ratio(section, field)
    _contract_latency(judge, "provider_p95_ms_max")
    _contract_latency(concurrency, "p95_ms_max")
    zero_counters = contract.get("zero_counters")
    identity_fields = contract.get("identity_fields")
    if not isinstance(zero_counters, list) or not zero_counters or not all(
        isinstance(value, str) and value for value in zero_counters
    ):
        raise ValueError("release gate contract zero_counters must be strings")
    if not isinstance(identity_fields, list) or not identity_fields or not all(
        isinstance(value, str) and value for value in identity_fields
    ):
        raise ValueError("release gate contract identity_fields must be strings")
    for (section_name, field), expected in _FIXED_VALUES.items():
        section = _contract_section(contract, section_name)
        if section.get(field) != expected:
            raise ValueError("contract must preserve fixed release hard gates")
    if tuple(zero_counters) != _FIXED_ZERO_COUNTERS:
        raise ValueError("contract must preserve fixed release hard gates")
    if tuple(identity_fields) != _FIXED_IDENTITY_FIELDS:
        raise ValueError("contract must preserve fixed release hard gates")


def _at(source: Mapping[str, object], *paths: Sequence[str]) -> object:
    for path in paths:
        current: object = source
        found = True
        for field in path:
            if not isinstance(current, Mapping) or field not in current:
                found = False
                break
            current = current[field]
        if found:
            return current
    return None


def _count(
    source: Mapping[str, object],
    paths: Sequence[Sequence[str]],
    label: str,
    reasons: list[str],
) -> int | None:
    value = _at(source, *paths)
    if value is None:
        reasons.append(f"missing_or_invalid:{label}")
        return None
    if type(value) is not int or value < 0:
        reasons.append(f"invalid_domain:{label}")
        return None
    return value


def _ratio(
    source: Mapping[str, object],
    paths: Sequence[Sequence[str]],
    label: str,
    reasons: list[str],
) -> float | None:
    value = _at(source, *paths)
    if value is None:
        reasons.append(f"missing_or_invalid:{label}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        reasons.append(f"invalid_domain:{label}")
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        reasons.append(f"nonfinite:{label}")
        return None
    if not 0 <= numeric <= 1:
        reasons.append(f"invalid_domain:{label}")
        return None
    return numeric


def _latency(
    source: Mapping[str, object],
    paths: Sequence[Sequence[str]],
    label: str,
    reasons: list[str],
) -> float | None:
    value = _at(source, *paths)
    if value is None:
        reasons.append(f"missing_or_invalid:{label}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        reasons.append(f"invalid_domain:{label}")
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        reasons.append(f"nonfinite:{label}")
        return None
    if numeric < 0:
        reasons.append(f"invalid_domain:{label}")
        return None
    return numeric


def _expect_equal(
    metrics: dict[str, int | float | None],
    key: str,
    actual: int | float | None,
    expected: int | float,
    reasons: list[str],
    *,
    reason_label: str | None = None,
) -> None:
    metrics[key] = actual
    if actual is not None and actual != expected:
        reasons.append(
            f"threshold:{reason_label or key}:expected_{expected}:actual_{actual}"
        )


def _expect_minimum(
    metrics: dict[str, int | float | None],
    key: str,
    actual: int | float | None,
    minimum: float,
    reasons: list[str],
    *,
    reason_label: str | None = None,
) -> None:
    metrics[key] = actual
    if actual is not None and actual < minimum:
        reasons.append(
            f"threshold:{reason_label or key}:minimum_{minimum}:actual_{actual}"
        )


def _expect_maximum(
    metrics: dict[str, int | float | None],
    key: str,
    actual: int | float | None,
    maximum: float,
    reasons: list[str],
    *,
    reason_label: str | None = None,
) -> None:
    metrics[key] = actual
    if actual is not None and actual > maximum:
        reasons.append(
            f"threshold:{reason_label or key}:maximum_{maximum}:actual_{actual}"
        )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _timestamp_for(source: Mapping[str, object]) -> object:
    identity = _mapping(source.get("run_metadata"))
    for field in _TIMESTAMP_FIELDS:
        if field in source:
            return source[field]
        if field in identity:
            return identity[field]
    return None


def _identity_for(source: Mapping[str, object]) -> Mapping[str, object]:
    identity = source.get("identity")
    if isinstance(identity, Mapping):
        return identity
    inputs = _mapping(source.get("inputs"))
    return {
        "commit": source.get("commit") or source.get("git_commit"),
        "image_id": source.get("image_id") or source.get("image_digest"),
        "base_sha256": source.get("base_sha256")
        or source.get("database_sha256")
        or inputs.get("base_sha256"),
        "overlay_sha256": source.get("overlay_sha256")
        or inputs.get("overlay_sha256"),
        "search_index_sha256": source.get("search_index_sha256")
        or inputs.get("search_index_sha256"),
    }


def _recompute_judge_results(
    judge: Mapping[str, object], required_total: int, reasons: list[str]
) -> tuple[dict[str, int | float | None], set[str]]:
    empty = {
        "case_count": None,
        "evaluated_count": None,
        "pass_count": None,
        "failure_count": None,
        "numeric_exactness": None,
        "claim_citation_coverage": None,
        "evaluator_error_count": None,
        "forbidden_provider_call_count": None,
        "deterministic_provider_call_count": None,
    }
    raw_results = judge.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        reasons.append("missing_or_invalid:judge.results")
        return empty, set()
    if len(raw_results) != required_total:
        reasons.append(
            f"incomplete:judge.results:expected_{required_total}:actual_{len(raw_results)}"
        )

    case_ids: set[str] = set()
    passed_count = 0
    numeric_values: list[float] = []
    citation_values: list[float] = []
    evaluator_errors = 0
    forbidden_calls = 0
    valid = len(raw_results) == required_total
    for index, raw_result in enumerate(raw_results):
        label = f"judge.results[{index}]"
        if not isinstance(raw_result, Mapping):
            reasons.append(f"invalid_domain:{label}")
            valid = False
            continue
        case_id = raw_result.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            reasons.append(f"invalid_domain:{label}.case_id")
            valid = False
        else:
            case_ids.add(case_id)
        passed = raw_result.get("passed")
        if type(passed) is not bool:
            reasons.append(f"invalid_domain:{label}.passed")
            valid = False
        else:
            passed_count += int(passed)
        for field, values in (
            ("numeric_exactness", numeric_values),
            ("claim_citation_coverage", citation_values),
        ):
            value = raw_result.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                reasons.append(f"invalid_domain:{label}.{field}")
                valid = False
                continue
            numeric = float(value)
            if not math.isfinite(numeric) or not 0 <= numeric <= 1:
                reasons.append(f"invalid_domain:{label}.{field}")
                valid = False
                continue
            values.append(numeric)
        for field in ("evaluator_error_count", "forbidden_provider_call_count"):
            value = raw_result.get(field)
            if type(value) is not int or value < 0:
                reasons.append(f"invalid_domain:{label}.{field}")
                valid = False
                continue
            if field == "evaluator_error_count":
                evaluator_errors += value
            else:
                forbidden_calls += value

    if not valid:
        return empty, case_ids
    evaluated_count = len(raw_results)
    return (
        {
            "case_count": evaluated_count,
            "evaluated_count": evaluated_count,
            "pass_count": passed_count,
            "failure_count": evaluated_count - passed_count,
            "numeric_exactness": round(
                sum(numeric_values) / evaluated_count, 12
            ),
            "claim_citation_coverage": round(
                sum(citation_values) / evaluated_count, 12
            ),
            "evaluator_error_count": evaluator_errors,
            "forbidden_provider_call_count": forbidden_calls,
            "deterministic_provider_call_count": forbidden_calls,
        },
        case_ids,
    )


def _recompute_security_counters(
    judge: Mapping[str, object], expected_case_ids: set[str], reasons: list[str]
) -> dict[str, int | None]:
    raw_observations = judge.get("security_observations")
    totals: dict[str, int | None] = {
        field: 0 for field in _INDEPENDENT_SECURITY_COUNTERS
    }
    if not isinstance(raw_observations, list) or not raw_observations:
        reasons.append("missing_or_invalid:judge.security_observations")
        return {field: None for field in _INDEPENDENT_SECURITY_COUNTERS}
    observed_case_ids: set[str] = set()
    for index, raw_observation in enumerate(raw_observations):
        if not isinstance(raw_observation, Mapping):
            reasons.append(
                f"invalid_domain:judge.security_observations[{index}]"
            )
            return {field: None for field in _INDEPENDENT_SECURITY_COUNTERS}
        case_id = raw_observation.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            reasons.append(
                f"invalid_domain:judge.security_observations[{index}].case_id"
            )
            return {field: None for field in _INDEPENDENT_SECURITY_COUNTERS}
        observed_case_ids.add(case_id)
        nested = raw_observation.get("security_counts")
        observation = nested if isinstance(nested, Mapping) else raw_observation
        for field in _INDEPENDENT_SECURITY_COUNTERS:
            value = observation.get(field)
            if type(value) is not int or value < 0:
                reasons.append(
                    f"invalid_domain:judge.security_observations[{index}].{field}"
                )
                totals[field] = None
                continue
            if totals[field] is not None:
                totals[field] += value
    if observed_case_ids != expected_case_ids:
        reasons.append("incomplete:judge.security_observations.case_coverage")
        return {field: None for field in _INDEPENDENT_SECURITY_COUNTERS}
    return totals


def _normalized_identity_value(field: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    pattern = _IDENTITY_PATTERNS.get(field)
    if pattern is None or pattern.fullmatch(stripped) is None:
        return None
    return stripped.lower()


def _deduplicate(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def evaluate_release_gate(
    financial: Mapping[str, object],
    judge: Mapping[str, object],
    retrieval: Mapping[str, object],
    deployment: Mapping[str, object],
    *,
    contract: Mapping[str, object],
    expected_identity: Mapping[str, object] | None = None,
    actual_commit: str | None = None,
    now: datetime | None = None,
) -> ReleaseGateResult:
    """Recompute every release condition from source metrics and identities."""

    _validate_contract(contract)
    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
    reports = {
        "financial": _mapping(financial),
        "judge": _mapping(judge),
        "retrieval": _mapping(retrieval),
        "deployment": _mapping(deployment),
    }
    reasons: list[str] = []
    metrics: dict[str, int | float | None] = {}
    financial_contract = _contract_section(contract, "financial")
    judge_contract = _contract_section(contract, "judge")
    retrieval_contract = _contract_section(contract, "retrieval")
    concurrency_contract = _contract_section(contract, "concurrency")

    financial_total = _count(financial, (("cases", "total"),), "financial.cases.total", reasons)
    financial_passed = _count(financial, (("cases", "passed"),), "financial.cases.passed", reasons)
    financial_failed = _count(financial, (("cases", "failed"),), "financial.cases.failed", reasons)
    _expect_equal(metrics, "financial_total", financial_total, int(financial_contract["required_total"]), reasons, reason_label="financial.total")
    _expect_equal(metrics, "financial_passed", financial_passed, int(financial_contract["required_passed"]), reasons, reason_label="financial.passed")
    _expect_equal(metrics, "financial_failed", financial_failed, 0, reasons, reason_label="financial.failed")

    recomputed_judge, raw_case_ids = _recompute_judge_results(
        judge, int(judge_contract["required_total"]), reasons
    )
    for field, expected in (
        ("case_count", int(judge_contract["required_total"])),
        ("evaluated_count", int(judge_contract["required_total"])),
        ("pass_count", int(judge_contract["required_passed"])),
        ("failure_count", 0),
    ):
        reported = _count(judge, ((field,),), f"judge.{field}", reasons)
        actual = recomputed_judge[field]
        if actual is not None and reported is not None and actual != reported:
            reasons.append(
                f"mismatch:judge.{field}:reported_{reported}:recomputed_{actual}"
            )
        _expect_equal(metrics, f"judge_{field}", actual, expected, reasons, reason_label=f"judge.{field}")

    for field, expected in (
        ("numeric_exactness", float(judge_contract["numeric_exactness"])),
        ("claim_citation_coverage", float(judge_contract["claim_citation_coverage"])),
    ):
        reported = _ratio(judge, ((field,),), f"judge.{field}", reasons)
        actual = recomputed_judge[field]
        if actual is not None and reported is not None and actual != reported:
            reasons.append(
                f"mismatch:judge.{field}:reported_{reported}:recomputed_{actual}"
            )
        _expect_equal(metrics, f"judge_{field}", actual, expected, reasons, reason_label=f"judge.{field}")
    for field, minimum in (
        ("answerability_agreement", float(judge_contract["answerability_agreement_min"])),
        ("metamorphic_consistency", float(judge_contract["metamorphic_consistency_min"])),
    ):
        actual = _ratio(judge, ((field,),), f"judge.{field}", reasons)
        _expect_minimum(metrics, f"judge_{field}", actual, minimum, reasons, reason_label=f"judge.{field}")

    recall = _ratio(
        retrieval,
        (("metrics", "target_recall_at_20"), ("recall_at_20",)),
        "retrieval.recall_at_20",
        reasons,
    )
    _expect_minimum(
        metrics,
        "retrieval_recall_at_20",
        recall,
        float(retrieval_contract["recall_at_20_min"]),
        reasons,
        reason_label="retrieval.recall_at_20",
    )

    concurrency_request_count = _count(
        judge,
        (("concurrency_request_count",), ("concurrency", "request_count")),
        "judge.concurrency_request_count",
        reasons,
    )
    concurrency_error_count = _count(
        judge,
        (("concurrency_error_count",), ("concurrency", "error_count")),
        "judge.concurrency_error_count",
        reasons,
    )
    concurrency_p95 = _latency(
        judge,
        (("concurrency_p95_ms",), ("concurrency", "p95_ms")),
        "judge.concurrency_p95_ms",
        reasons,
    )
    _expect_equal(metrics, "concurrency_request_count", concurrency_request_count, int(concurrency_contract["request_count"]), reasons, reason_label="judge.concurrency_request_count")
    _expect_equal(metrics, "concurrency_error_count", concurrency_error_count, int(concurrency_contract["error_count"]), reasons, reason_label="judge.concurrency_error_count")
    _expect_maximum(metrics, "concurrency_p95_ms", concurrency_p95, float(concurrency_contract["p95_ms_max"]), reasons, reason_label="judge.concurrency_p95_ms")

    for field in (
        "provider_eligible_root_count",
        "provider_probe_observation_count",
        "provider_call_count",
    ):
        actual = _count(judge, ((field,),), f"judge.{field}", reasons)
        _expect_equal(metrics, field, actual, int(judge_contract[field]), reasons, reason_label=f"judge.{field}")
    provider_p95 = _latency(judge, (("provider_p95_ms",),), "judge.provider_p95_ms", reasons)
    _expect_maximum(metrics, "provider_p95_ms", provider_p95, float(judge_contract["provider_p95_ms_max"]), reasons, reason_label="judge.provider_p95_ms")

    zero_counters = contract["zero_counters"]
    assert isinstance(zero_counters, list)
    recomputed_security = _recompute_security_counters(
        judge, raw_case_ids, reasons
    )
    recomputed_security.update(
        {
            field: recomputed_judge[field]
            for field in (
                "evaluator_error_count",
                "forbidden_provider_call_count",
                "deterministic_provider_call_count",
            )
        }
    )
    for field in zero_counters:
        field = str(field)
        reported = _count(judge, ((field,),), f"judge.{field}", reasons)
        if field in recomputed_security:
            actual = recomputed_security[field]
            if actual is not None and reported is not None and actual != reported:
                reasons.append(
                    f"mismatch:judge.{field}:reported_{reported}:recomputed_{actual}"
                )
        else:
            actual = reported
        _expect_equal(metrics, str(field), actual, 0, reasons, reason_label=f"judge.{field}")

    freshness = _contract_section(contract, "freshness")
    max_age = int(freshness["max_age_seconds"])
    future_skew = int(freshness["future_skew_seconds"])
    source_timestamps: dict[str, str | None] = {}
    for name, report in reports.items():
        raw_timestamp = _timestamp_for(report)
        parsed = _parse_timestamp(raw_timestamp)
        source_timestamps[name] = parsed.isoformat() if parsed is not None else None
        if parsed is None:
            reasons.append(f"missing_or_invalid:{name}.timestamp")
            continue
        age = (evaluated_at - parsed).total_seconds()
        if age > max_age:
            reasons.append(f"freshness:{name}.stale")
        elif age < -future_skew:
            reasons.append(f"freshness:{name}.future")

    identity_fields = contract["identity_fields"]
    assert isinstance(identity_fields, list)
    identities = {name: _identity_for(report) for name, report in reports.items()}
    anchors = _mapping(expected_identity)
    trusted_identity: dict[str, str] = {}
    for field_value in identity_fields:
        field = str(field_value)
        anchor = _normalized_identity_value(field, anchors.get(field))
        if anchor is None:
            reasons.append(f"missing_or_invalid:trust_anchor.{field}")
        else:
            trusted_identity[field] = anchor
        for name in _SOURCE_NAMES:
            observed = _normalized_identity_value(field, identities[name].get(field))
            if observed is None:
                reasons.append(f"missing_or_invalid:{name}.identity.{field}")
            elif anchor is not None and observed != anchor:
                reasons.append(f"{name}.identity.{field}:anchor_mismatch")
    normalized_head = _normalized_identity_value("commit", actual_commit)
    if normalized_head is None:
        reasons.append("missing_or_invalid:actual_head.commit")
    elif "commit" in trusted_identity and trusted_identity["commit"] != normalized_head:
        reasons.append("trust_anchor.commit:head_mismatch")

    hard_gate_reasons = _deduplicate(reasons)
    passed = not hard_gate_reasons
    return ReleaseGateResult(
        release_state=PASS if passed else BLOCKED,
        hard_gate_passed=passed,
        hard_gate_reasons=hard_gate_reasons,
        evaluated_at_utc=evaluated_at.isoformat(),
        metrics=metrics,
        identity=trusted_identity,
        source_timestamps=source_timestamps,
    )


def render_release_html(result: ReleaseGateResult) -> str:
    payload = result.to_dict()
    state = escape(str(payload["release_state"]))
    reason_items = "".join(
        f"<li><code>{escape(reason)}</code></li>" for reason in result.hard_gate_reasons
    ) or "<li>None</li>"
    metric_rows = "".join(
        "<tr><th>{}</th><td>{}</td></tr>".format(escape(key), escape(str(value)))
        for key, value in sorted(result.metrics.items())
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Release gate</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:70rem}}code{{word-break:break-all}}th{{text-align:left}}th,td{{padding:.35rem .8rem;border-bottom:1px solid #ddd}}</style>
</head><body><h1>Release candidate: {state}</h1>
<p>Hard gate passed: {str(result.hard_gate_passed).lower()}</p>
<h2>Reasons</h2><ul>{reason_items}</ul>
<h2>Recomputed metrics</h2><table>{metric_rows}</table>
</body></html>"""


def _write_atomic(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_release_report(
    result: ReleaseGateResult, json_path: Path, html_path: Path
) -> None:
    json_payload = (
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _write_atomic(Path(json_path), json_payload)
    _write_atomic(Path(html_path), render_release_html(result).encode("utf-8"))


__all__ = [
    "BLOCKED",
    "PASS",
    "ReleaseGateResult",
    "evaluate_release_gate",
    "load_release_contract",
    "render_release_html",
    "write_release_report",
]
