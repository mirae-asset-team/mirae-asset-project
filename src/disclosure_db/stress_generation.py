"""Schema and deterministic provenance helpers for stress-evaluation cases."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable


ALLOWED_ORACLES = {"exact", "abstention", "metamorphic", "lineage", "fault"}
ALLOWED_CATEGORIES = {
    "financial", "event", "correction", "calculation", "retrieval",
    "unanswerable", "adversarial", "language", "fault",
}
ALLOWED_TRUST_TIERS = {"human_verified", "agent_audited"}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def source_sha256(record: object) -> str:
    return hashlib.sha256(canonical_json(record)).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _evidence(case: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, str]]:
    raw = case.get("evidence")
    if not isinstance(raw, list):
        raise ValueError("evidence must be a list")
    evidence: list[dict[str, object]] = []
    filings: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("evidence item must be an object")
        evidence_id = _text(item.get("evidence_id"), "evidence_id")
        filing_id = _text(item.get("filing_id"), "evidence filing_id")
        if evidence_id in filings:
            raise ValueError("duplicate evidence_id")
        filings[evidence_id] = filing_id
        evidence.append(item)
    return evidence, filings


def _numeric_answer(answer: dict[str, object], evidence_filings: dict[str, str]) -> None:
    try:
        number = Decimal(str(answer.get("value")))
    except InvalidOperation as exc:
        raise ValueError("numeric value must be Decimal-parseable") from exc
    if not number.is_finite():
        raise ValueError("numeric value must be finite")
    _text(answer.get("unit"), "numeric unit")
    scale = answer.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
        raise ValueError("numeric scale must be positive")
    filing_id = _text(answer.get("filing_id"), "numeric filing_id")
    evidence_ids = answer.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise ValueError("numeric evidence_ids must be non-empty")
    for evidence_id in evidence_ids:
        evidence_key = _text(evidence_id, "numeric evidence_id")
        if evidence_key not in evidence_filings:
            raise ValueError("numeric evidence_id is unknown")
        if evidence_filings[evidence_key] != filing_id:
            raise ValueError("numeric evidence filing mismatch")


def validate_stress_case(case: dict[str, object]) -> None:
    if not isinstance(case, dict):
        raise ValueError("stress case must be an object")
    _text(case.get("schema_version"), "schema_version")
    _text(case.get("question_id"), "question_id")
    _text(case.get("question"), "question")
    _text(case.get("question_type"), "question_type")
    answerability = _text(case.get("answerability"), "answerability")
    if answerability not in {"answerable", "unanswerable"}:
        raise ValueError("answerability must be answerable or unanswerable")
    stress = case.get("stress")
    if not isinstance(stress, dict):
        raise ValueError("stress must be an object")
    oracle = _text(stress.get("oracle"), "oracle")
    if oracle not in ALLOWED_ORACLES:
        raise ValueError(f"unknown oracle: {oracle}")
    category = _text(stress.get("category"), "category")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    for field in ("base_id", "group_id", "mutation_id", "generator"):
        _text(stress.get(field), field)
    if stress.get("generator") != "deterministic_stress_v1":
        raise ValueError("unsupported stress generator")
    if stress.get("seed") != 20260819:
        raise ValueError("stress seed must be 20260819")
    source_hash = _text(stress.get("source_sha256"), "source_sha256")
    if _SHA256.fullmatch(source_hash) is None:
        raise ValueError("source_sha256 must be a 64-character hex hash")
    if stress.get("trust_tier") not in ALLOWED_TRUST_TIERS:
        raise ValueError("invalid trust tier")
    _, evidence_filings = _evidence(case)
    answer = case.get("answer")
    if not isinstance(answer, dict):
        raise ValueError("answer must be an object")
    if answerability == "unanswerable":
        if answer.get("kind") != "unanswerable":
            raise ValueError("unanswerable case must have an unanswerable answer")
        _text(answer.get("reason"), "abstention reason")
        if evidence_filings:
            raise ValueError("abstention evidence must be empty")
        if answer.get("evidence_ids") not in (None, []):
            raise ValueError("abstention cannot contain evidence")
        if "value" in answer or "values" in answer:
            raise ValueError("abstention cannot contain numeric claims")
        return
    if answer.get("kind") == "numeric":
        _numeric_answer(answer, evidence_filings)
    elif answer.get("kind") == "text":
        _text(answer.get("text"), "text answer")
        evidence_ids = answer.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("text evidence_ids must be non-empty")
        for evidence_id in evidence_ids:
            if _text(evidence_id, "text evidence_id") not in evidence_filings:
                raise ValueError("text evidence_id is unknown")
    elif answer.get("kind") == "multi_numeric":
        values = answer.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("multi_numeric values must be non-empty")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("multi_numeric value must be an object")
            _numeric_answer(value, evidence_filings)
    else:
        raise ValueError("answerable case has unsupported answer kind")


def validate_stress_cases(cases: Iterable[dict[str, object]]) -> None:
    seen: set[str] = set()
    for case in cases:
        validate_stress_case(case)
        question_id = str(case["question_id"])
        if question_id in seen:
            raise ValueError(f"duplicate question_id: {question_id}")
        seen.add(question_id)


__all__ = [
    "ALLOWED_CATEGORIES", "ALLOWED_ORACLES", "canonical_json", "source_sha256",
    "validate_stress_case", "validate_stress_cases",
]
