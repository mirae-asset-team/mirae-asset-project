"""Deterministic scoring and failure classification for stress cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import shutil
from pathlib import Path
from typing import Iterable


CAUSE_ORDER = (
    "runtime_integrity", "planner", "entity_resolution", "structured_fact",
    "lineage", "retrieval", "reranker", "generation", "verification",
    "citation", "evaluation_contract", "unclassified",
)


@dataclass(slots=True)
class CaseScore:
    question_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    primary_cause: str = "unclassified"
    metrics: dict[str, float | int | bool | None] = field(default_factory=dict)


def _field(answer: object, name: str, default: object = None) -> object:
    if isinstance(answer, dict):
        return answer.get(name, default)
    return getattr(answer, name, default)


def _actual_answer(result: object) -> dict[str, object]:
    nested = _field(result, "answer")
    if isinstance(nested, dict):
        return nested
    return {}


def _actual_citations(result: object) -> set[str]:
    values = _field(result, "citations", _field(result, "evidence_ids", []))
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _expected_evidence(case: dict[str, object]) -> set[str]:
    answer = case.get("answer")
    if not isinstance(answer, dict):
        return set()
    if answer.get("kind") == "multi_numeric":
        return {str(item) for value in answer.get("values", []) if isinstance(value, dict) for item in value.get("evidence_ids", [])}
    return {str(item) for item in answer.get("evidence_ids", [])}


def _metric(actual: object, expected: object, key: str) -> bool:
    if key == "answerable":
        return bool(actual) == bool(expected)
    if key == "numeric":
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            return False
        return (
            _decimal(actual.get("value")) == _decimal(expected.get("value"))
            and actual.get("unit") == expected.get("unit")
            and actual.get("scale") == expected.get("scale")
        )
    if key == "text":
        return isinstance(actual, dict) and isinstance(expected, dict) and actual.get("text") == expected.get("text")
    return False


def score_case(case: dict[str, object], answer: object) -> CaseScore:
    question_id = str(case.get("question_id", "unknown"))
    expected_answerable = case.get("answerability") == "answerable"
    expected = case.get("answer") if isinstance(case.get("answer"), dict) else {}
    actual_answerable = bool(_field(answer, "answerable", False))
    actual = _actual_answer(answer)
    citations = _actual_citations(answer)
    expected_citations = _expected_evidence(case)
    failures: list[str] = []
    if actual_answerable != expected_answerable:
        failures.append("answerability_mismatch")
    if bool(_field(answer, "unsafe", False)):
        failures.append("unsafe_answer")
    if expected_answerable:
        if not bool(_field(answer, "verified", False)):
            failures.append("unverified_answer")
        kind = expected.get("kind")
        if kind == "numeric" and not _metric(actual, expected, "numeric"):
            failures.append("numeric_mismatch")
        elif kind == "text" and not _metric(actual, expected, "text"):
            failures.append("text_mismatch")
        elif kind == "multi_numeric":
            expected_values = expected.get("values", [])
            actual_values = actual.get("values", []) if isinstance(actual, dict) else []
            if not isinstance(actual_values, list) or len(actual_values) != len(expected_values):
                failures.append("numeric_mismatch")
            else:
                pairs = sorted((str(item.get("label")), str(item.get("value")), str(item.get("unit"))) for item in actual_values if isinstance(item, dict))
                expected_pairs = sorted((str(item.get("label")), str(item.get("value")), str(item.get("unit"))) for item in expected_values if isinstance(item, dict))
                if pairs != expected_pairs:
                    failures.append("numeric_mismatch")
        precision = len(citations & expected_citations) / len(citations) if citations else 0.0
        recall = len(citations & expected_citations) / len(expected_citations) if expected_citations else 1.0
    else:
        if actual_answerable or actual.get("kind") in {"numeric", "multi_numeric"} or "value" in actual or "values" in actual:
            failures.append("false_numeric_claim")
        if citations:
            failures.append("unexpected_citation")
        precision = None
        recall = None
    if citations - expected_citations and expected_answerable:
        failures.append("unknown_citation")
    filing_id = actual.get("filing_id") if isinstance(actual, dict) else None
    expected_filing = expected.get("filing_id") if isinstance(expected, dict) else None
    if filing_id is not None and expected_filing is not None and str(filing_id) != str(expected_filing):
        failures.append("cross_filing_citation")
    metrics = {
        "answerability_match": actual_answerable == expected_answerable,
        "numeric_exact": "numeric_mismatch" not in failures if expected.get("kind") == "numeric" else None,
        "citation_precision": precision,
        "citation_recall": recall,
        "false_numeric_claim": "false_numeric_claim" in failures,
        "unsafe_answer": "unsafe_answer" in failures,
        "unknown_citation": "unknown_citation" in failures,
        "cross_filing_citation": "cross_filing_citation" in failures,
    }
    return CaseScore(question_id, not failures, failures, classify_failure(failures), metrics)


def score_metamorphic_group(results: Iterable[dict[str, object]]) -> CaseScore:
    values = list(results)
    if not values:
        return CaseScore("metamorphic", False, ["empty_metamorphic_group"], "evaluation_contract")
    first = values[0]
    signature = (bool(first.get("answerable")), str(first.get("value")), first.get("unit"), tuple(sorted(str(item) for item in first.get("evidence_ids", []))))
    failures = []
    for item in values[1:]:
        candidate = (bool(item.get("answerable")), str(item.get("value")), item.get("unit"), tuple(sorted(str(value) for value in item.get("evidence_ids", []))))
        if candidate != signature:
            failures.append("metamorphic_inconsistency")
            break
    return CaseScore(str(first.get("question_id", "metamorphic")), not failures, failures, classify_failure(failures))


def should_skip(case: dict[str, object], git_commit: str, completed: dict[str, object]) -> bool:
    return (
        str(case.get("question_id")) == str(completed.get("case_id"))
        and str(case.get("input_hash")) == str(completed.get("input_hash"))
        and str(git_commit) == str(completed.get("git_commit"))
    )


def run_fault_case(source: Path, temporary_root: Path) -> dict[str, object]:
    source = Path(source)
    temporary_root = Path(temporary_root)
    temporary_root.mkdir(parents=True, exist_ok=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    copied = temporary_root / source.name
    shutil.copy2(source, copied)
    copied.write_bytes(b"fault-fixture")
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "fault_isolated": before == after and hashlib.sha256(copied.read_bytes()).hexdigest() != before,
        "source_sha256": before,
        "copy_sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
    }


def classify_failure(failures: Iterable[str]) -> str:
    names = set(failures)
    mapping = {
        "unsafe_answer": "verification", "false_numeric_claim": "verification",
        "unknown_citation": "citation", "cross_filing_citation": "citation",
        "numeric_mismatch": "structured_fact", "text_mismatch": "structured_fact",
        "answerability_mismatch": "generation", "unverified_answer": "verification",
        "metamorphic_inconsistency": "generation", "unexpected_citation": "citation",
        "empty_metamorphic_group": "evaluation_contract",
    }
    for cause in CAUSE_ORDER:
        if cause in names or any(value == cause for key, value in mapping.items() if key in names):
            return cause
    return "unclassified"


def aggregate_scores(scores: Iterable[CaseScore], contract: dict[str, object]) -> dict[str, object]:
    rows = list(scores)
    count = len(rows)
    def fraction(name: str) -> float | None:
        values = [row.metrics.get(name) for row in rows if row.metrics.get(name) is not None]
        return (sum(bool(value) for value in values) / len(values)) if values else None
    summary: dict[str, object] = {
        "count": count,
        "pass_count": sum(row.passed for row in rows),
        "error_count": sum("evaluator_error" in row.failures for row in rows),
        "false_numeric_claim_count": sum(bool(row.metrics.get("false_numeric_claim")) for row in rows),
        "unsafe_answer_count": sum(bool(row.metrics.get("unsafe_answer")) for row in rows),
        "unknown_citation_count": sum(bool(row.metrics.get("unknown_citation")) for row in rows),
        "cross_filing_citation_count": sum(bool(row.metrics.get("cross_filing_citation")) for row in rows),
        "answerability_agreement": fraction("answerability_match"),
        "numeric_exactness": fraction("numeric_exact"),
        "citation_precision": fraction("citation_precision"),
        "citation_recall": fraction("citation_recall"),
        "failure_causes": {cause: sum(row.primary_cause == cause for row in rows) for cause in CAUSE_ORDER},
    }
    hard = contract.get("hard_gates", {}) if isinstance(contract.get("hard_gates"), dict) else {}
    hard_reasons = []
    for field in ("unsafe_answer_count", "false_numeric_claim_count", "unknown_citation_count", "cross_filing_citation_count", "evaluator_error_count"):
        if int(summary.get(field, 0)) > int(hard.get(field, 0)):
            hard_reasons.append(field)
    summary["hard_gate_passed"] = not hard_reasons
    summary["hard_gate_reasons"] = hard_reasons
    return summary


__all__ = [
    "CAUSE_ORDER", "CaseScore", "aggregate_scores", "classify_failure", "run_fault_case",
    "score_case", "score_metamorphic_group", "should_skip",
]
