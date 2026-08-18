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


def _numeric_match(expected_value: Any, numeric_values: Sequence[Any]) -> bool | None:
    if expected_value is None:
        return None
    try:
        expected = Decimal(str(expected_value))
        return any(expected == Decimal(str(value)) for value in numeric_values)
    except (InvalidOperation, TypeError, ValueError):
        return False


def _text_match(expected_text: Any, answer: str) -> bool | None:
    if not expected_text:
        return None
    return str(expected_text).casefold() in answer.casefold()


def evaluate_agent(agent: DisclosureAgent, gold_path: Path, *, limit: int = 20) -> dict[str, Any]:
    """Evaluate each Gold JSONL record and return auditable aggregate results."""
    evaluations: list[dict[str, Any]] = []
    for line_no, line in enumerate(gold_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        question_id = record.get("question_id", f"line-{line_no}")
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
            selected_evidence = set(answer.citation_ids)
            citation_recall = (
                len(expected_evidence & selected_evidence) / len(expected_evidence)
                if expected_evidence
                else None
            )
            expected_answer = record.get("answer")
            expected_value = expected_answer.get("value") if isinstance(expected_answer, dict) else None
            expected_text = expected_answer.get("text") if isinstance(expected_answer, dict) else None
            numeric_match = _numeric_match(expected_value, answer.numeric_values)
            text_match = _text_match(expected_text, answer.answer)
            passed = evaluation_pass(
                expected_answerable=expected_answerable,
                actual_answerable=answer.answerable,
                verified=answer.verified,
                citation_recall=citation_recall,
                numeric_match=numeric_match,
                text_match=text_match,
            )
            evaluations.append({
                "question_id": question_id,
                "expected_answerable": expected_answerable,
                "answerable": answer.answerable,
                "verified": answer.verified,
                "status": "pass" if passed else "review",
                "citation_recall": citation_recall,
                "numeric_match": numeric_match,
                "text_match": text_match,
                "latency_ms": elapsed_ms,
                "reason_codes": answer.reason_codes,
                "citation_ids": answer.citation_ids,
                "answer": answer.answer,
            })
        except Exception as exc:  # keep the evaluator auditable even with one malformed record
            evaluations.append({"question_id": question_id, "status": "error", "error": str(exc)})

    answered = [item for item in evaluations if item.get("status") != "error"]
    latencies = [float(item["latency_ms"]) for item in answered if "latency_ms" in item]
    return {
        "count": len(evaluations),
        "pass_count": sum(item.get("status") == "pass" for item in answered),
        "verified_count": sum(bool(item.get("verified")) for item in answered),
        "answerability_match_count": sum(
            item.get("answerable") == item.get("expected_answerable") for item in answered
        ),
        "error_count": sum(item.get("status") == "error" for item in evaluations),
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "evaluations": evaluations,
    }
