"""Small JSONL-backed QA evaluation loop for the existing Agent runtime."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import tempfile
from time import perf_counter
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4


QA_CATEGORIES = frozenset({
    "financial_direct", "financial_multi_period", "financial_derived",
    "financial_comparison", "financial_statement", "search", "summary",
    "trend", "event", "correction", "complex", "ambiguous", "typo_alias",
    "unavailable",
})
FAILURE_LAYERS = frozenset({
    "RESOLVER", "EXECUTOR", "RETRIEVAL", "FINANCIAL_DATA", "CALCULATION",
    "EVIDENCE", "HCX_FINAL", "INFRA",
})
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{2,99}\Z")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_case(raw: Mapping[str, object]) -> dict[str, object]:
    identifier = str(raw.get("id") or "")
    question = str(raw.get("question") or "").strip()
    category = str(raw.get("category") or "")
    expected = raw.get("expected")
    forbidden = raw.get("forbidden_phrases", [])
    if not _CASE_ID.fullmatch(identifier):
        raise ValueError("qa_case_id_invalid")
    if not question or len(question) > 2_000:
        raise ValueError("qa_question_invalid")
    if category not in QA_CATEGORIES:
        raise ValueError("qa_category_invalid")
    if not isinstance(expected, Mapping):
        raise ValueError("qa_expected_invalid")
    if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
        raise ValueError("qa_forbidden_phrases_invalid")
    return {
        "id": identifier,
        "question": question,
        "category": category,
        "expected": dict(expected),
        "forbidden_phrases": list(dict.fromkeys(forbidden)),
    }


class QaCaseStore:
    def __init__(self, cases_path: Path, results_directory: Path) -> None:
        self.cases_path = Path(cases_path)
        self.results_directory = Path(results_directory)

    def list_cases(self) -> list[dict[str, object]]:
        if not self.cases_path.is_file():
            return []
        cases: list[dict[str, object]] = []
        with self.cases_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"qa_jsonl_invalid:{line_number}") from exc
                if not isinstance(decoded, Mapping):
                    raise ValueError(f"qa_case_not_object:{line_number}")
                cases.append(validate_case(decoded))
        identifiers = [str(case["id"]) for case in cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("qa_case_id_duplicate")
        return cases

    def save_cases(self, cases: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
        validated = [validate_case(case) for case in cases]
        identifiers = [str(case["id"]) for case in validated]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("qa_case_id_duplicate")
        payload = "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in validated)
        _atomic_text(self.cases_path, payload)
        return validated

    def create(self, case: Mapping[str, object]) -> dict[str, object]:
        validated = validate_case(case)
        cases = self.list_cases()
        if any(row["id"] == validated["id"] for row in cases):
            raise ValueError("qa_case_id_duplicate")
        self.save_cases([*cases, validated])
        return validated

    def update(self, identifier: str, case: Mapping[str, object]) -> dict[str, object]:
        validated = validate_case({**dict(case), "id": identifier})
        cases = self.list_cases()
        if not any(row["id"] == identifier for row in cases):
            raise KeyError(identifier)
        self.save_cases(validated if row["id"] == identifier else row for row in cases)
        return validated

    def delete(self, identifier: str) -> None:
        cases = self.list_cases()
        remaining = [row for row in cases if row["id"] != identifier]
        if len(remaining) == len(cases):
            raise KeyError(identifier)
        self.save_cases(remaining)

    def save_result(self, result: Mapping[str, object]) -> Path:
        run_id = str(result.get("run_id") or "")
        if not _CASE_ID.fullmatch(run_id):
            raise ValueError("qa_run_id_invalid")
        path = self.results_directory / f"{run_id}.json"
        _atomic_text(path, json.dumps(dict(result), ensure_ascii=False, indent=2) + "\n")
        return path

    def list_results(self) -> list[dict[str, object]]:
        if not self.results_directory.is_dir():
            return []
        results: list[dict[str, object]] = []
        for path in sorted(self.results_directory.glob("*.json"), reverse=True):
            try:
                decoded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(decoded, Mapping):
                results.append(dict(decoded))
        return results

    def get_result(self, run_id: str) -> dict[str, object]:
        path = self.results_directory / f"{run_id}.json"
        if not path.is_file():
            raise KeyError(run_id)
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, Mapping):
            raise ValueError("qa_result_invalid")
        return dict(decoded)


def _route_trace(route: object | None) -> dict[str, object]:
    if route is None:
        return {
            "companies": [], "period": None, "metric": None,
            "workflow": "function_calling_fallback", "ambiguity": None,
        }
    arguments = getattr(route, "arguments", {})
    context = getattr(route, "context", {})
    companies = context.get("companies") if isinstance(context, Mapping) else None
    if not isinstance(companies, list):
        company = arguments.get("company") if isinstance(arguments, Mapping) else None
        companies = [company] if company else []
    period = (context.get("period") or context.get("fiscal_year")) if isinstance(context, Mapping) else None
    if period is None and isinstance(arguments, Mapping):
        period = str(arguments.get("end_date") or "")[:4] or None
    metric = context.get("metric") if isinstance(context, Mapping) else None
    if metric is None and isinstance(arguments, Mapping):
        metric = arguments.get("account")
    return {
        "companies": list(companies),
        "period": period,
        "metric": metric,
        "workflow": getattr(route, "workflow", "single"),
        "ambiguity": dict(context) if getattr(route, "kind", "") == "clarification" and isinstance(context, Mapping) else None,
        "corrections": list(getattr(route, "corrections", ())),
    }


def build_eval_trace(
    route: object | None, result: Mapping[str, object], latency_ms: float,
) -> dict[str, object]:
    tool_response = result.get("tool_response")
    tool_response = tool_response if isinstance(tool_response, Mapping) else {}
    tool_metadata = tool_response.get("metadata")
    tool_metadata = tool_metadata if isinstance(tool_metadata, Mapping) else {}
    executors = tool_metadata.get("executors")
    if not isinstance(executors, list):
        tool_name = result.get("tool_name")
        executors = [tool_name] if tool_name else []
    data = tool_response.get("data")
    data = data if isinstance(data, Mapping) else {}
    result_count = data.get("result_count")
    if result_count is None and isinstance(data.get("facts"), list):
        result_count = len(data["facts"])
    if result_count is None:
        result_count = data.get("total_count")
    sufficiency = tool_metadata.get("sufficiency_check")
    sufficiency = sufficiency if isinstance(sufficiency, Mapping) else {}
    citations = result.get("citations")
    citations = citations if isinstance(citations, list) else []
    dense_calls = sum(name in {"search_disclosures", "build_summary_context"} for name in executors)
    metadata = result.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {
        "resolver": _route_trace(route),
        "executors": {"tools": list(executors), "result_count": result_count},
        "evidence": {
            "status": sufficiency.get("status"),
            "answer_allowed": result.get("answer_allowed") is True,
            "citation_count": len(citations),
            "rcept_no_present": bool(citations) and all(bool(item.get("rcept_no")) for item in citations if isinstance(item, Mapping)),
        },
        "calls": {
            "dense": dense_calls,
            "function_calling": 1 if metadata.get("tool_selection_called") else 0,
            "hcx_final": 1 if metadata.get("final_generation_called") else 0,
        },
        "latency_ms": round(latency_ms, 2),
    }


def _first_failure(
    case: Mapping[str, object], result: Mapping[str, object], trace: Mapping[str, object],
) -> tuple[str | None, object, object, str | None]:
    expected = case["expected"]
    assert isinstance(expected, Mapping)
    resolver = trace["resolver"]
    executors = trace["executors"]
    evidence = trace["evidence"]
    calls = trace["calls"]
    assert all(isinstance(item, Mapping) for item in (resolver, executors, evidence, calls))
    for key in ("companies", "period", "metric", "workflow"):
        if key in expected and expected[key] != resolver.get(key):
            return "RESOLVER", expected[key], resolver.get(key), f"resolver_{key}_mismatch"
    expected_tools = expected.get("executor_tools")
    if isinstance(expected_tools, list) and expected_tools != executors.get("tools"):
        return "EXECUTOR", expected_tools, executors.get("tools"), "executor_sequence_mismatch"
    expected_count = expected.get("executor_count")
    if isinstance(expected_count, int) and expected_count != len(executors.get("tools") or []):
        return "EXECUTOR", expected_count, len(executors.get("tools") or []), "executor_count_mismatch"
    for call_name, expected_key in (
        ("dense", "dense_max_calls"),
        ("function_calling", "function_calling_max_calls"),
        ("hcx_final", "hcx_final_max_calls"),
    ):
        maximum = expected.get(expected_key)
        actual = calls.get(call_name)
        if isinstance(maximum, int) and isinstance(actual, int) and actual > maximum:
            return "RETRIEVAL" if call_name == "dense" else "EXECUTOR", maximum, actual, f"{call_name}_call_limit_exceeded"
    tool_response = result.get("tool_response")
    data = tool_response.get("data") if isinstance(tool_response, Mapping) else {}
    data = data if isinstance(data, Mapping) else {}
    if expected.get("comparison_complete") is True:
        comparison = data.get("comparison")
        actual = comparison.get("status") if isinstance(comparison, Mapping) else None
        if actual != "complete":
            return "FINANCIAL_DATA", "complete", actual, "comparison_input_missing"
    if expected.get("backend_display_value_required") is True:
        facts = data.get("facts")
        values = data.get("validated_statement_values")
        rows = facts if isinstance(facts, list) else values if isinstance(values, list) else []
        if not rows or any(not isinstance(row, Mapping) or not row.get("display_value") for row in rows):
            return "CALCULATION", "backend display_value", rows, "deterministic_financial_format_missing"
    if expected.get("citation_required") is True and evidence.get("citation_count", 0) == 0:
        return "EVIDENCE", "citation", 0, "citation_missing"
    if expected.get("answer_allowed") is not None and expected.get("answer_allowed") != evidence.get("answer_allowed"):
        return "EVIDENCE", expected.get("answer_allowed"), evidence.get("answer_allowed"), "answer_gate_mismatch"
    answer = str(result.get("answer") or "")
    for phrase in case.get("forbidden_phrases", []):
        if phrase and phrase in answer:
            return "HCX_FINAL", f"not contains {phrase}", phrase, "forbidden_phrase_present"
    if result.get("status") in {"error", "provider_unavailable"}:
        return "INFRA", "successful runtime", result.get("status"), "runtime_or_provider_unavailable"
    return None, None, None, None


class QaEvaluator:
    def __init__(self, function_calling_service: object, store: QaCaseStore) -> None:
        self.service = function_calling_service
        self.store = store

    def run(self, cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        for case in cases:
            route = self.service.router.route(str(case["question"])) if getattr(self.service, "router", None) else None
            started = perf_counter()
            result_object = self.service.answer(str(case["question"]))
            latency_ms = (perf_counter() - started) * 1_000
            result = result_object.to_dict() if hasattr(result_object, "to_dict") else dict(result_object)
            trace = build_eval_trace(route, result, latency_ms)
            layer, expected, actual, reason = _first_failure(case, result, trace)
            rows.append({
                "qa_id": case["id"], "question": case["question"], "category": case["category"],
                "status": "PASS" if layer is None else "FAIL",
                "failure_layer": layer, "expected": expected, "actual": actual,
                "short_reason": reason, "trace": trace,
            })
        passed = sum(row["status"] == "PASS" for row in rows)
        run = {
            "run_id": f"qa-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}",
            "created_at": datetime.now(UTC).isoformat(),
            "total": len(rows), "pass": passed, "fail": len(rows) - passed,
            "pass_rate": round((passed / len(rows) * 100) if rows else 0, 2),
            "results": rows,
        }
        self.store.save_result(run)
        return run


__all__ = [
    "FAILURE_LAYERS", "QA_CATEGORIES", "QaCaseStore", "QaEvaluator",
    "build_eval_trace", "validate_case",
]
