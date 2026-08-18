"""Reusable evaluation contract for disclosure-agent answers."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .agent import DisclosureAgent


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def quality_gate_passed(summary: dict[str, Any], acceptance: dict[str, Any]) -> bool:
    """Apply every configured threshold; missing/null required metrics fail closed."""
    if any(key not in summary for key in ("error_count", "false_numeric_claim_count", "unsafe_answer_count")):
        return False
    if int(summary["error_count"] or 0) != 0:
        return False
    if int(summary.get("false_numeric_claim_count", 0) or 0) > int(acceptance.get("false_numeric_claims", 0) or 0):
        return False
    if int(summary.get("unsafe_answer_count", 0) or 0) > int(acceptance.get("unsafe_answers", 0) or 0):
        return False
    mapping: dict[str, tuple[str, str]] = {
        "regression_answerability_matches": ("answerability_match_count", "ge"),
        "numeric_exactness": ("numeric_exactness", "ge"),
        "citation_precision": ("citation_precision", "ge"),
        "citation_recall": ("citation_recall", "ge"),
        "holdout_answerability_agreement": ("answerability_agreement", "ge"),
        "retrieval_recall_at_20": ("target_recall_at_20", "ge"),
        "post_rerank_recall_at_8": ("post_rerank_recall_at_8", "ge"),
        "planner_p95_ms": ("planner_p95_ms", "le"),
        "fact_lookup_p95_ms": ("fact_lookup_p95_ms", "le"),
        "local_retrieval_p95_ms": ("local_retrieval_p95_ms", "le"),
        "reranked_retrieval_p95_ms": ("reranked_retrieval_p95_ms", "le"),
        "end_to_end_p95_ms": ("latency_ms_p95", "le"),
    }
    for threshold_name, (metric_name, operator) in mapping.items():
        if threshold_name not in acceptance:
            continue
        threshold = acceptance[threshold_name]
        metric = summary.get(metric_name)
        if threshold_name == "end_to_end_p95_ms" and metric is None:
            metric = summary.get("end_to_end_p95_ms")
        if metric is None:
            return False
        if operator == "ge" and float(metric) < float(threshold):
            return False
        if operator == "le" and float(metric) > float(threshold):
            return False
    handled = set(mapping) | {"false_numeric_claims", "unsafe_answers"}
    if any(key not in handled for key in acceptance):
        return False
    return True


def evaluation_pass(
    *,
    expected_answerable: bool,
    actual_answerable: bool,
    verified: bool,
    citation_recall: float | None,
    numeric_match: bool | None,
    text_match: bool | None,
) -> bool:
    """Return whether an answer satisfies every applicable evaluation gate."""
    if not verified or actual_answerable != expected_answerable:
        return False
    if not expected_answerable:
        return True
    return (
        citation_recall == 1.0
        and numeric_match is not False
        and text_match is not False
    )


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return a deterministic linearly interpolated percentile, or ``None``."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if quantile <= 0:
        return ordered[0]
    if quantile >= 1:
        return ordered[-1]
    # Preserve an observed value for the high-tail metric on short runs.
    if quantile >= 0.95:
        index = min(max(math.ceil(len(ordered) * quantile), 1) - 1, len(ordered) - 1)
        return ordered[index]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    value = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(value, 2)


def _numeric_match(expected_answer: Any, numeric_values: Sequence[Any]) -> bool | None:
    if not isinstance(expected_answer, dict):
        return None
    kind = expected_answer.get("kind")
    if kind == "numeric":
        expected_values = [expected_answer.get("value")]
    elif kind == "multi_numeric":
        values = expected_answer.get("values")
        if not isinstance(values, list):
            return False
        expected_values = [item.get("value") for item in values if isinstance(item, dict)]
    else:
        return None
    try:
        expected = sorted(Decimal(str(value)) for value in expected_values)
        actual = sorted(Decimal(str(value)) for value in numeric_values)
        return len(expected) == len(actual) and expected == actual
    except (InvalidOperation, TypeError, ValueError):
        return False


def _validate_record(record: Any) -> str | None:
    if not isinstance(record, dict):
        return "gold record must be a JSON object"
    required = {"question_id", "question", "answerability", "answer", "evidence"}
    missing = sorted(required - set(record))
    if missing:
        return "required fields missing: " + ", ".join(missing)
    if not isinstance(record["question_id"], str) or not record["question_id"] or not isinstance(record["question"], str) or not record["question"]:
        return "question identity must be a non-empty string"
    if record["answerability"] not in {"answerable", "unanswerable", "ambiguous"}:
        return "answerability is invalid"
    answer = record["answer"]
    if not isinstance(answer, dict) or answer.get("kind") not in {"text", "numeric", "multi_numeric", "unanswerable"}:
        return "answer shape is invalid"
    if (record["answerability"] == "answerable") == (answer.get("kind") == "unanswerable"):
        return "answerability and answer kind disagree"
    if not isinstance(record["evidence"], list) or any(not isinstance(item, dict) or not item.get("evidence_id") for item in record["evidence"]):
        return "evidence must be a list of objects with evidence_id"
    if answer.get("kind") == "multi_numeric":
        values = answer.get("values")
        if not isinstance(values, list) or len(values) < 2 or any(not isinstance(item, dict) or "value" not in item for item in values):
            return "multi_numeric answer shape is invalid"
    return None


def _text_match(expected_text: Any, answer: str) -> bool | None:
    if not expected_text:
        return None
    return str(expected_text).casefold() in answer.casefold()


def evaluate_agent(
    agent: DisclosureAgent,
    gold_path: Path,
    *,
    limit: int = 20,
    contract_path: Path | None = None,
    acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate each Gold JSONL record and return auditable aggregate results."""
    evaluations: list[dict[str, Any]] = []
    for line_no, line in enumerate(gold_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        question_id = f"line-{line_no}"
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise TypeError("gold record must be a JSON object")
            question_id = record.get("question_id", question_id)
            validation_error = _validate_record(record)
            if validation_error:
                evaluations.append({"question_id": question_id, "status": "error", "error": validation_error})
                continue
        except Exception as exc:  # keep malformed rows auditable and continue evaluating
            evaluations.append({"question_id": question_id, "status": "error", "error": str(exc)})
            continue
        started = time.perf_counter()
        try:
            answer = agent.answer(str(record["question"]), as_of=record.get("as_of"), limit=limit)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            expected_answerable = record.get("answerability") == "answerable"
            expected_evidence = {
                str(item.get("evidence_id"))
                for item in record.get("evidence", [])
                if item.get("evidence_id")
            }
            selected_ids = [str(item) for item in getattr(answer, "citation_ids", [])]
            selected_evidence = set(selected_ids)
            citation_recall = (
                len(expected_evidence & selected_evidence) / len(expected_evidence)
                if expected_evidence
                else None
            )
            citation_precision = (
                len(expected_evidence & selected_evidence) / len(selected_evidence)
                if selected_evidence
                else None
            )
            expected_answer = record.get("answer")
            expected_text = expected_answer.get("text") if isinstance(expected_answer, dict) else None
            numeric_values = list(getattr(answer, "numeric_values", []))
            numeric_match = _numeric_match(expected_answer, numeric_values)
            text_match = _text_match(expected_text, str(getattr(answer, "answer", "")))
            answerable = bool(getattr(answer, "answerable", False))
            verified = bool(getattr(answer, "verified", False))
            passed = evaluation_pass(
                expected_answerable=expected_answerable,
                actual_answerable=answerable,
                verified=verified,
                citation_recall=citation_recall,
                numeric_match=numeric_match,
                text_match=text_match,
            )
            evaluations.append({
                "question_id": question_id,
                "expected_answerable": expected_answerable,
                "answerable": answerable,
                "verified": verified,
                "status": "pass" if passed else "review",
                "citation_recall": citation_recall,
                "citation_precision": citation_precision,
                "numeric_match": numeric_match,
                "text_match": text_match,
                "latency_ms": elapsed_ms,
                "reason_codes": list(getattr(answer, "reason_codes", [])),
                "citation_ids": selected_ids,
                "answer": str(getattr(answer, "answer", "")),
                "numeric_values": numeric_values,
            })
        except Exception as exc:  # keep the evaluator auditable even with one malformed record
            evaluations.append({"question_id": question_id, "status": "error", "error": str(exc)})

    answered = [item for item in evaluations if item.get("status") != "error"]
    latencies = [float(item["latency_ms"]) for item in answered if "latency_ms" in item]
    answerability_matches = sum(
        item.get("answerable") == item.get("expected_answerable") for item in answered
    )
    numeric_rows = [item for item in answered if item.get("numeric_match") is not None]
    citation_rows = [item for item in answered if item.get("citation_precision") is not None]
    recall_rows = [item for item in answered if item.get("citation_recall") is not None]
    false_numeric_claim_count = sum(
        bool(item.get("numeric_values")) and (
            not bool(item.get("expected_answerable")) or item.get("numeric_match") is False
        )
        for item in answered
    )
    unsafe_answer_count = sum(
        bool(item.get("answerable")) and (not bool(item.get("verified")) or not bool(item.get("expected_answerable")))
        for item in answered
    )
    result: dict[str, Any] = {
        "count": len(evaluations),
        "pass_count": sum(item.get("status") == "pass" for item in answered),
        "verified_count": sum(bool(item.get("verified")) for item in answered),
        "answerability_match_count": answerability_matches,
        "scored_count": len(answered),
        "answerability_denominator": len(answered),
        "answerability_agreement": _ratio(answerability_matches, len(answered)),
        "numeric_denominator": len(numeric_rows),
        "numeric_exactness": _ratio(sum(item.get("numeric_match") is True for item in numeric_rows), len(numeric_rows)),
        "citation_precision_denominator": len(citation_rows),
        "citation_precision": _ratio(sum(float(item["citation_precision"]) for item in citation_rows), len(citation_rows)),
        "citation_recall_denominator": len(recall_rows),
        "citation_recall": _ratio(sum(float(item["citation_recall"]) for item in recall_rows), len(recall_rows)),
        "false_numeric_claim_count": false_numeric_claim_count,
        "unsafe_answer_count": unsafe_answer_count,
        "error_count": sum(item.get("status") == "error" for item in evaluations),
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "planner_p95_ms": None,
        "fact_lookup_p95_ms": None,
        "local_retrieval_p95_ms": None,
        "reranked_retrieval_p95_ms": None,
        "end_to_end_p95_ms": percentile(latencies, 0.95),
        "evaluations": evaluations,
    }
    if acceptance is None and contract_path is not None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        acceptance = contract.get("serving_vertical_slice_acceptance")
    if acceptance is not None:
        result["quality_gate_passed"] = quality_gate_passed(result, acceptance)
    return result
