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
_REQUIRED_RETRIEVAL_CASES = 120
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
        "answerability_agreement": None,
        "metamorphic_consistency": None,
        "provider_call_count": None,
        "concurrency_request_count": None,
        "concurrency_error_count": None,
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
    answerability_values: list[float] = []
    metamorphic_values: list[float] = []
    provider_calls = 0
    concurrency_wave_sizes: set[int] = set()
    concurrency_errors = 0
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
        lane = raw_result.get("lane")
        if not isinstance(lane, str) or not lane:
            reasons.append(f"invalid_domain:{label}.lane")
            valid = False
        for field, values in (
            ("numeric_exactness", numeric_values),
            ("claim_citation_coverage", citation_values),
            ("answerability_agreement", answerability_values),
            ("metamorphic_consistency", metamorphic_values),
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
        raw_counts: dict[str, int] = {}
        for field in (
            "provider_call_count",
            "concurrency_request_count",
            "concurrency_error_count",
            "evaluator_error_count",
            "forbidden_provider_call_count",
        ):
            value = raw_result.get(field)
            if type(value) is not int or value < 0:
                reasons.append(f"invalid_domain:{label}.{field}")
                valid = False
                continue
            raw_counts[field] = value
        if len(raw_counts) == 5:
            provider_calls += raw_counts["provider_call_count"]
            evaluator_errors += raw_counts["evaluator_error_count"]
            forbidden_calls += raw_counts["forbidden_provider_call_count"]
            concurrency_errors += raw_counts["concurrency_error_count"]
            if lane == "concurrency":
                concurrency_wave_sizes.add(raw_counts["concurrency_request_count"])
            elif raw_counts["concurrency_request_count"] != 0:
                reasons.append(
                    f"invalid_domain:{label}.concurrency_request_count"
                )
                valid = False

    if not concurrency_wave_sizes:
        reasons.append("missing_or_invalid:judge.results.concurrency")
        valid = False
    elif len(concurrency_wave_sizes) != 1:
        reasons.append("inconsistent:judge.results.concurrency_request_count")
        valid = False

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
            "answerability_agreement": round(
                sum(answerability_values) / evaluated_count, 12
            ),
            "metamorphic_consistency": round(
                sum(metamorphic_values) / evaluated_count, 12
            ),
            "provider_call_count": provider_calls,
            "concurrency_request_count": next(iter(concurrency_wave_sizes)),
            "concurrency_error_count": concurrency_errors,
            "evaluator_error_count": evaluator_errors,
            "forbidden_provider_call_count": forbidden_calls,
            "deterministic_provider_call_count": forbidden_calls,
        },
        case_ids,
    )


def _nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 6)


def _recompute_latency_observations(
    judge: Mapping[str, object],
    *,
    expected_case_ids: set[str],
    expected_provider_observations: int,
    provider_limit_ms: float,
    concurrency_limit_ms: float,
    reasons: list[str],
) -> dict[str, int | float | None]:
    """Recompute latency gates from content-free HTTP observations.

    ``judge.latency_observations`` has exactly these required fields per row:
    ``case_id``, ``probe_id``, ``lane``, ``provider_configured``,
    ``final_generation_called``, ``provider_used`` and ``latency_ms``.
    Provider p95 uses all 120 provider probe responses; concurrency p95 uses
    every request in every 20-request wave. Both use nearest-rank p95, while
    any individual latency above the corresponding limit also blocks release.
    """

    empty: dict[str, int | float | None] = {
        "provider_call_count": None,
        "forbidden_provider_call_count": None,
        "provider_p95_ms": None,
        "concurrency_p95_ms": None,
    }
    raw_observations = judge.get("latency_observations")
    if not isinstance(raw_observations, list) or not raw_observations:
        reasons.append("missing_or_invalid:judge.latency_observations")
        return empty

    raw_results = judge.get("results")
    if not isinstance(raw_results, list):
        reasons.append("missing_or_invalid:judge.results")
        return empty
    provider_expected: dict[str, int] = {}
    concurrency_expected: dict[str, int] = {}
    provider_reported_p95: dict[str, float] = {}
    concurrency_reported_p95: dict[str, float] = {}
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, Mapping):
            continue
        case_id = raw_result.get("case_id")
        lane = raw_result.get("lane")
        if not isinstance(case_id, str) or case_id not in expected_case_ids:
            continue
        if lane == "provider_answer":
            probe_count = raw_result.get("probe_count")
            if type(probe_count) is int and probe_count > 0:
                provider_expected[case_id] = probe_count
            else:
                reasons.append(f"invalid_domain:judge.results[{index}].probe_count")
            raw_p95 = raw_result.get("provider_p95_ms")
            if (
                isinstance(raw_p95, bool)
                or not isinstance(raw_p95, (int, float))
                or not math.isfinite(float(raw_p95))
                or float(raw_p95) < 0
            ):
                reasons.append(
                    f"invalid_domain:judge.results[{index}].provider_p95_ms"
                )
                valid = False
            else:
                provider_reported_p95[case_id] = float(raw_p95)
        elif lane == "concurrency":
            request_count = raw_result.get("concurrency_request_count")
            if type(request_count) is int and request_count >= 0:
                concurrency_expected[case_id] = request_count
            else:
                reasons.append(
                    f"invalid_domain:judge.results[{index}].concurrency_request_count"
                )
            raw_p95 = raw_result.get("concurrency_p95_ms")
            if (
                isinstance(raw_p95, bool)
                or not isinstance(raw_p95, (int, float))
                or not math.isfinite(float(raw_p95))
                or float(raw_p95) < 0
            ):
                reasons.append(
                    f"invalid_domain:judge.results[{index}].concurrency_p95_ms"
                )
                valid = False
            else:
                concurrency_reported_p95[case_id] = float(raw_p95)
        elif "provider_p95_ms" in raw_result or "concurrency_p95_ms" in raw_result:
            reasons.append(f"invalid_domain:judge.results[{index}].lane_p95")
            valid = False

    provider_latencies: list[float] = []
    concurrency_latencies: list[float] = []
    provider_seen_by_case: dict[str, int] = {}
    concurrency_seen_by_case: dict[str, int] = {}
    provider_latencies_by_case: dict[str, list[float]] = {}
    concurrency_latencies_by_case: dict[str, list[float]] = {}
    provider_probe_keys: set[tuple[str, str]] = set()
    provider_calls = 0
    forbidden_calls = 0
    valid = True
    for index, raw_observation in enumerate(raw_observations):
        label = f"judge.latency_observations[{index}]"
        if not isinstance(raw_observation, Mapping):
            reasons.append(f"invalid_domain:{label}")
            valid = False
            continue
        case_id = raw_observation.get("case_id")
        probe_id = raw_observation.get("probe_id")
        lane = raw_observation.get("lane")
        provider_configured = raw_observation.get("provider_configured")
        final_generation_called = raw_observation.get("final_generation_called")
        provider_used = raw_observation.get("provider_used")
        raw_latency = raw_observation.get("latency_ms")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id not in expected_case_ids
        ):
            reasons.append(f"invalid_domain:{label}.case_id")
            valid = False
        if not isinstance(probe_id, str) or not probe_id:
            reasons.append(f"invalid_domain:{label}.probe_id")
            valid = False
        if lane not in {"provider_answer", "concurrency"}:
            reasons.append(f"invalid_domain:{label}.lane")
            valid = False
        if type(provider_configured) is not bool:
            reasons.append(f"invalid_domain:{label}.provider_configured")
            valid = False
        if type(final_generation_called) is not bool:
            reasons.append(f"invalid_domain:{label}.final_generation_called")
            valid = False
        observed_provider_used = (
            provider_configured is True and final_generation_called is True
        )
        if type(provider_used) is not bool:
            reasons.append(f"invalid_domain:{label}.provider_used")
            valid = False
        elif provider_used != observed_provider_used:
            reasons.append(f"mismatch:{label}.provider_used")
            valid = False
        if isinstance(raw_latency, bool) or not isinstance(raw_latency, (int, float)):
            reasons.append(f"invalid_domain:{label}.latency_ms")
            valid = False
            continue
        latency = float(raw_latency)
        if not math.isfinite(latency) or latency < 0:
            reasons.append(f"invalid_domain:{label}.latency_ms")
            valid = False
            continue
        if lane == "provider_answer":
            if isinstance(case_id, str):
                provider_seen_by_case[case_id] = provider_seen_by_case.get(case_id, 0) + 1
                provider_latencies_by_case.setdefault(case_id, []).append(latency)
            if isinstance(case_id, str) and isinstance(probe_id, str):
                key = (case_id, probe_id)
                if key in provider_probe_keys:
                    reasons.append(f"invalid_domain:{label}.probe_id")
                    valid = False
                provider_probe_keys.add(key)
            provider_latencies.append(latency)
            provider_calls += int(observed_provider_used)
            if latency > provider_limit_ms:
                reasons.append(
                    "threshold:judge.provider_answer.raw_latency_ms:"
                    f"expected_max_{provider_limit_ms}:actual_{latency}"
                )
        elif lane == "concurrency":
            if isinstance(case_id, str):
                concurrency_seen_by_case[case_id] = (
                    concurrency_seen_by_case.get(case_id, 0) + 1
                )
                concurrency_latencies_by_case.setdefault(case_id, []).append(latency)
            concurrency_latencies.append(latency)
            forbidden_calls += int(observed_provider_used)
            if latency > concurrency_limit_ms:
                reasons.append(
                    "threshold:judge.concurrency.raw_latency_ms:"
                    f"expected_max_{concurrency_limit_ms}:actual_{latency}"
                )

    if len(provider_latencies) != expected_provider_observations:
        reasons.append(
            "incomplete:judge.latency_observations.provider_answer:"
            f"expected_{expected_provider_observations}:actual_{len(provider_latencies)}"
        )
        valid = False
    if provider_seen_by_case != provider_expected:
        reasons.append("incomplete:judge.latency_observations.provider_cases")
        valid = False
    expected_concurrency_total = sum(concurrency_expected.values())
    if len(concurrency_latencies) != expected_concurrency_total:
        reasons.append(
            "incomplete:judge.latency_observations.concurrency:"
            f"expected_{expected_concurrency_total}:actual_{len(concurrency_latencies)}"
        )
        valid = False
    if concurrency_seen_by_case != concurrency_expected:
        reasons.append("incomplete:judge.latency_observations.concurrency_cases")
        valid = False
    for case_id, expected_p95 in provider_reported_p95.items():
        recomputed_p95 = _nearest_rank_p95(provider_latencies_by_case.get(case_id, []))
        if recomputed_p95 is None or not math.isclose(
            expected_p95, recomputed_p95, rel_tol=0.0, abs_tol=1e-6
        ):
            reasons.append(
                "mismatch:judge.results.provider_p95_ms:"
                f"case_{case_id}:reported_{expected_p95}:recomputed_{recomputed_p95}"
            )
    for case_id, expected_p95 in concurrency_reported_p95.items():
        recomputed_p95 = _nearest_rank_p95(
            concurrency_latencies_by_case.get(case_id, [])
        )
        if recomputed_p95 is None or not math.isclose(
            expected_p95, recomputed_p95, rel_tol=0.0, abs_tol=1e-6
        ):
            reasons.append(
                "mismatch:judge.results.concurrency_p95_ms:"
                f"case_{case_id}:reported_{expected_p95}:recomputed_{recomputed_p95}"
            )
    if not valid:
        return empty
    return {
        "provider_call_count": provider_calls,
        "forbidden_provider_call_count": forbidden_calls,
        "provider_p95_ms": _nearest_rank_p95(provider_latencies),
        "concurrency_p95_ms": _nearest_rank_p95(concurrency_latencies),
    }


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


def _recompute_retrieval_recall(
    retrieval: Mapping[str, object], reasons: list[str]
) -> float | None:
    raw_scores = retrieval.get("case_scores")
    if not isinstance(raw_scores, list) or not raw_scores:
        reasons.append("missing_or_invalid:retrieval.case_scores")
        return None
    if len(raw_scores) != _REQUIRED_RETRIEVAL_CASES:
        reasons.append(
            "incomplete:retrieval.case_scores:"
            f"expected_{_REQUIRED_RETRIEVAL_CASES}:actual_{len(raw_scores)}"
        )

    case_ids: set[str] = set()
    target_count = 0
    target_hits = 0
    valid = len(raw_scores) == _REQUIRED_RETRIEVAL_CASES
    for index, raw_score in enumerate(raw_scores):
        label = f"retrieval.case_scores[{index}]"
        if not isinstance(raw_score, Mapping):
            reasons.append(f"invalid_domain:{label}")
            valid = False
            continue
        case_id = raw_score.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            reasons.append(f"invalid_domain:{label}.case_id")
            valid = False
        else:
            case_ids.add(case_id)
        raw_target_count = raw_score.get("target_count")
        raw_target_hits = raw_score.get("target_hits_at_20")
        if type(raw_target_count) is not int or raw_target_count <= 0:
            reasons.append(f"invalid_domain:{label}.target_count")
            valid = False
            continue
        if (
            type(raw_target_hits) is not int
            or raw_target_hits < 0
            or raw_target_hits > raw_target_count
        ):
            reasons.append(f"invalid_domain:{label}.target_hits_at_20")
            valid = False
            continue
        target_count += raw_target_count
        target_hits += raw_target_hits

    metrics = _mapping(retrieval.get("metrics"))
    for field, actual in (
        ("case_count", len(raw_scores)),
        ("target_count", target_count),
        ("target_hits_at_20", target_hits),
    ):
        reported = _count(
            metrics,
            ((field,),),
            f"retrieval.metrics.{field}",
            reasons,
        )
        if reported is not None and reported != actual:
            reasons.append(
                f"mismatch:retrieval.{field}:reported_{reported}:recomputed_{actual}"
            )
    if not valid or target_count <= 0:
        return None
    return target_hits / target_count


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
    recomputed_latency = _recompute_latency_observations(
        judge,
        expected_case_ids=raw_case_ids,
        expected_provider_observations=int(
            judge_contract["provider_probe_observation_count"]
        ),
        provider_limit_ms=float(judge_contract["provider_p95_ms_max"]),
        concurrency_limit_ms=float(concurrency_contract["p95_ms_max"]),
        reasons=reasons,
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
        reported = _ratio(judge, ((field,),), f"judge.{field}", reasons)
        actual = recomputed_judge[field]
        if actual is not None and reported is not None and actual != reported:
            reasons.append(
                f"mismatch:judge.{field}:reported_{reported}:recomputed_{actual}"
            )
        _expect_minimum(metrics, f"judge_{field}", actual, minimum, reasons, reason_label=f"judge.{field}")

    reported_recall = _ratio(
        retrieval,
        (("metrics", "target_recall_at_20"), ("recall_at_20",)),
        "retrieval.recall_at_20",
        reasons,
    )
    recall = _recompute_retrieval_recall(retrieval, reasons)
    if (
        recall is not None
        and reported_recall is not None
        and recall != reported_recall
    ):
        reasons.append(
            "mismatch:retrieval.recall_at_20:"
            f"reported_{reported_recall}:recomputed_{recall}"
        )
    _expect_minimum(
        metrics,
        "retrieval_recall_at_20",
        recall,
        float(retrieval_contract["recall_at_20_min"]),
        reasons,
        reason_label="retrieval.recall_at_20",
    )

    reported_concurrency_request_count = _count(
        judge,
        (("concurrency_request_count",), ("concurrency", "request_count")),
        "judge.concurrency_request_count",
        reasons,
    )
    reported_concurrency_error_count = _count(
        judge,
        (("concurrency_error_count",), ("concurrency", "error_count")),
        "judge.concurrency_error_count",
        reasons,
    )
    concurrency_request_count = recomputed_judge["concurrency_request_count"]
    concurrency_error_count = recomputed_judge["concurrency_error_count"]
    for field, reported, actual in (
        (
            "concurrency_request_count",
            reported_concurrency_request_count,
            concurrency_request_count,
        ),
        (
            "concurrency_error_count",
            reported_concurrency_error_count,
            concurrency_error_count,
        ),
    ):
        if actual is not None and reported is not None and actual != reported:
            reasons.append(
                f"mismatch:judge.{field}:reported_{reported}:recomputed_{actual}"
            )
    reported_concurrency_p95 = _latency(
        judge,
        (("concurrency_p95_ms",), ("concurrency", "p95_ms")),
        "judge.concurrency_p95_ms",
        reasons,
    )
    concurrency_p95 = recomputed_latency["concurrency_p95_ms"]
    if (
        concurrency_p95 is not None
        and reported_concurrency_p95 is not None
        and concurrency_p95 != reported_concurrency_p95
    ):
        reasons.append(
            "mismatch:judge.concurrency_p95_ms:"
            f"reported_{reported_concurrency_p95}:recomputed_{concurrency_p95}"
        )
    _expect_equal(metrics, "concurrency_request_count", concurrency_request_count, int(concurrency_contract["request_count"]), reasons, reason_label="judge.concurrency_request_count")
    _expect_equal(metrics, "concurrency_error_count", concurrency_error_count, int(concurrency_contract["error_count"]), reasons, reason_label="judge.concurrency_error_count")
    _expect_maximum(metrics, "concurrency_p95_ms", concurrency_p95, float(concurrency_contract["p95_ms_max"]), reasons, reason_label="judge.concurrency_p95_ms")

    for field in (
        "provider_eligible_root_count",
        "provider_probe_observation_count",
    ):
        actual = _count(judge, ((field,),), f"judge.{field}", reasons)
        _expect_equal(metrics, field, actual, int(judge_contract[field]), reasons, reason_label=f"judge.{field}")
    reported_provider_calls = _count(
        judge, (("provider_call_count",),), "judge.provider_call_count", reasons
    )
    provider_calls = recomputed_latency["provider_call_count"]
    raw_result_provider_calls = recomputed_judge["provider_call_count"]
    if (
        provider_calls is not None
        and reported_provider_calls is not None
        and provider_calls != reported_provider_calls
    ):
        reasons.append(
            "mismatch:judge.provider_call_count:"
            f"reported_{reported_provider_calls}:recomputed_{provider_calls}"
        )
    if (
        provider_calls is not None
        and raw_result_provider_calls is not None
        and provider_calls != raw_result_provider_calls
    ):
        reasons.append(
            "mismatch:judge.results.provider_call_count:"
            f"reported_{raw_result_provider_calls}:recomputed_{provider_calls}"
        )
    _expect_equal(
        metrics,
        "provider_call_count",
        provider_calls,
        int(judge_contract["provider_call_count"]),
        reasons,
        reason_label="judge.provider_call_count",
    )
    reported_provider_p95 = _latency(
        judge, (("provider_p95_ms",),), "judge.provider_p95_ms", reasons
    )
    provider_p95 = recomputed_latency["provider_p95_ms"]
    if (
        provider_p95 is not None
        and reported_provider_p95 is not None
        and provider_p95 != reported_provider_p95
    ):
        reasons.append(
            "mismatch:judge.provider_p95_ms:"
            f"reported_{reported_provider_p95}:recomputed_{provider_p95}"
        )
    _expect_maximum(metrics, "provider_p95_ms", provider_p95, float(judge_contract["provider_p95_ms_max"]), reasons, reason_label="judge.provider_p95_ms")

    zero_counters = contract["zero_counters"]
    assert isinstance(zero_counters, list)
    recomputed_security = _recompute_security_counters(
        judge, raw_case_ids, reasons
    )
    raw_forbidden_calls = recomputed_judge["forbidden_provider_call_count"]
    observed_forbidden_calls = recomputed_latency["forbidden_provider_call_count"]
    if (
        raw_forbidden_calls is not None
        and observed_forbidden_calls is not None
        and raw_forbidden_calls != observed_forbidden_calls
    ):
        reasons.append(
            "mismatch:judge.results.forbidden_provider_call_count:"
            f"reported_{raw_forbidden_calls}:recomputed_{observed_forbidden_calls}"
        )
    combined_forbidden_calls = (
        max(raw_forbidden_calls, observed_forbidden_calls)
        if raw_forbidden_calls is not None and observed_forbidden_calls is not None
        else None
    )
    recomputed_security.update(
        {
            "evaluator_error_count": recomputed_judge["evaluator_error_count"],
            "forbidden_provider_call_count": combined_forbidden_calls,
            "deterministic_provider_call_count": combined_forbidden_calls,
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
