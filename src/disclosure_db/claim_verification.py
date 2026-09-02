"""Deterministic claim admission and verification for public HCX answers.

Provider prose is never authoritative.  This module exposes only identifiers
for already-admitted facts, calculations, citations, and evidence slots, then
checks the provider's typed claims against those identifiers before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Mapping, Sequence

from .calculator import calculate


CLAIM_CONTRACT_VERSION = "claim-verification-v1"
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
)
_NONFINITE = re.compile(r"(?<![A-Za-z0-9_])[+-]?(?:nan|inf(?:inity)?)(?![A-Za-z0-9_])", re.I)
_FACT_ID_FIELDS = ("financial_fact_id", "event_fact_id", "fact_id")
_CALCULATION_ID_FIELDS = ("calculation_id", "calculation_ref")
_NUMERIC_FIELDS = frozenset({
    "value_numeric", "normalized_value", "structured_value", "display_value",
    "value", "count", "total_count", "period", "period_start", "period_end",
    "instant_date", "filed_at", "from_period", "to_period", "fiscal_year",
})
_TRACE_CHECKS = ("citations", "evidence_slots", "numeric_values", "calculations", "policy")
_VALIDATED_STATEMENT_ROW_PATH = re.compile(r"\.validated_statement_values\[\d+\]\Z")


def _ordered(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values if item))


def _evidence_ids(value: Mapping[str, object]) -> tuple[str, ...]:
    raw = value.get("evidence_ids")
    result = [str(item) for item in raw if item] if isinstance(raw, (list, tuple)) else []
    if value.get("evidence_id"):
        result.insert(0, str(value["evidence_id"]))
    return _ordered(result)


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return result if result.is_finite() else None


def _numeric_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            if str(key) in _NUMERIC_FIELDS:
                result.extend(_numeric_tokens(item))
        return _ordered(result)
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_numeric_tokens(item))
        return _ordered(result)
    if value is None or isinstance(value, bool):
        return ()
    return _ordered(tuple(match.group(0).replace(",", "") for match in _NUMBER.finditer(str(value))))


def extract_prose_numeric_values(text: str) -> tuple[str, ...]:
    """Return canonical finite decimal tokens without interpreting prose."""

    return tuple(
        token for token in _numeric_tokens(text) if _decimal(token) is not None
    )


def _direct_id(value: Mapping[str, object], fields: Sequence[str]) -> str | None:
    for field_name in fields:
        if value.get(field_name):
            return str(value[field_name])
    return None


def _walk_mappings(value: object, path: str = "data"):
    if isinstance(value, Mapping):
        yield path, value
        for key, item in value.items():
            yield from _walk_mappings(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk_mappings(item, f"{path}[{index}]")


def _is_validated_statement_row(path: str, value: Mapping[str, object]) -> bool:
    return bool(
        _VALIDATED_STATEMENT_ROW_PATH.search(path)
        and value.get("metric")
        and value.get("period")
        and value.get("display_value")
    )


@dataclass(frozen=True, slots=True)
class HcxAnswerClaim:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...]
    fact_refs: tuple[str, ...] = ()
    calculation_refs: tuple[str, ...] = ()
    evidence_slot_ids: tuple[str, ...] = ()
    numeric_values: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: object) -> "HcxAnswerClaim":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("hcx_claim_invalid")
        expected = {
            "claim_id", "text", "citation_ids", "fact_refs", "calculation_refs",
            "evidence_slot_ids", "numeric_values",
        }
        if set(value) != expected:
            raise ValueError("hcx_claim_schema_mismatch")

        def strings(name: str) -> tuple[str, ...]:
            raw = value.get(name)
            if not isinstance(raw, (list, tuple)) or not all(
                isinstance(item, str) and item for item in raw
            ):
                raise ValueError(f"hcx_claim_{name}_invalid")
            return _ordered(tuple(raw))

        claim_id = value.get("claim_id")
        text = value.get("text")
        if not isinstance(claim_id, str) or not claim_id or len(claim_id) > 128:
            raise ValueError("hcx_claim_id_invalid")
        if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
            raise ValueError("hcx_claim_text_invalid")
        citations = strings("citation_ids")
        slots = strings("evidence_slot_ids")
        if not citations:
            raise ValueError("hcx_claim_citation_ids_invalid")
        if not slots:
            raise ValueError("hcx_claim_evidence_slot_ids_invalid")
        return cls(
            claim_id=claim_id,
            text=text.strip(),
            citation_ids=citations,
            fact_refs=strings("fact_refs"),
            calculation_refs=strings("calculation_refs"),
            evidence_slot_ids=slots,
            numeric_values=strings("numeric_values"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "citation_ids": list(self.citation_ids),
            "fact_refs": list(self.fact_refs),
            "calculation_refs": list(self.calculation_refs),
            "evidence_slot_ids": list(self.evidence_slot_ids),
            "numeric_values": list(self.numeric_values),
        }


@dataclass(frozen=True, slots=True)
class AdmittedFact:
    fact_ref: str
    evidence_ids: tuple[str, ...]
    evidence_slot_ids: tuple[str, ...]
    numeric_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdmittedCalculation:
    calculation_ref: str
    operation: str
    value: str
    operands: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evidence_slot_ids: tuple[str, ...]
    numeric_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClaimAdmission:
    evidence_ids: tuple[str, ...]
    evidence_slots: Mapping[str, tuple[str, ...]]
    facts: Mapping[str, AdmittedFact]
    calculations: Mapping[str, AdmittedCalculation]
    rejected_facts: Mapping[str, tuple[str, ...]]
    rejected_calculations: Mapping[str, tuple[str, ...]]

    def public_contract(self) -> dict[str, object]:
        return {
            "schema_version": CLAIM_CONTRACT_VERSION,
            "evidence_slots": [
                {"slot_id": slot_id, "evidence_ids": list(evidence_ids)}
                for slot_id, evidence_ids in self.evidence_slots.items()
            ],
            "facts": [
                {
                    "fact_ref": fact.fact_ref,
                    "evidence_ids": list(fact.evidence_ids),
                    "evidence_slot_ids": list(fact.evidence_slot_ids),
                    "numeric_values": list(fact.numeric_values),
                }
                for fact in self.facts.values()
            ],
            "calculations": [
                {
                    "calculation_ref": calculation.calculation_ref,
                    "operation": calculation.operation,
                    "value": calculation.value,
                    "operands": list(calculation.operands),
                    "evidence_ids": list(calculation.evidence_ids),
                    "evidence_slot_ids": list(calculation.evidence_slot_ids),
                    "numeric_values": list(calculation.numeric_values),
                }
                for calculation in self.calculations.values()
            ],
        }


def _slot_map(data: Mapping[str, object], admitted: set[str]) -> dict[str, tuple[str, ...]]:
    has_explicit_slot_contract = "evidence_slots" in data
    raw_slots = data.get("evidence_slots")
    slots: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_slots, list):
        for raw in raw_slots:
            if not isinstance(raw, Mapping) or not raw.get("slot_id") or raw.get("complete") is False:
                continue
            ids = _evidence_ids(raw)
            if ids and set(ids).issubset(admitted):
                slots[str(raw["slot_id"])] = ids
    if not has_explicit_slot_contract and admitted:
        slots["tool_evidence"] = tuple(sorted(admitted))
    return slots


def _owned_slots(
    evidence_ids: Sequence[str], slots: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    required = set(evidence_ids)
    return tuple(
        slot_id for slot_id, slot_evidence in slots.items()
        if required and required.issubset(set(slot_evidence))
    )


def build_claim_admission(tool_response: Mapping[str, object]) -> ClaimAdmission:
    bundle = tool_response.get("evidence_bundle")
    raw_admitted = bundle.get("evidence_ids") if isinstance(bundle, Mapping) else None
    evidence_ids = _ordered(tuple(
        str(item) for item in raw_admitted if item
    )) if isinstance(raw_admitted, (list, tuple)) else ()
    admitted = set(evidence_ids)
    data = tool_response.get("data")
    data = data if isinstance(data, Mapping) else {}
    slots = _slot_map(data, admitted)

    facts: dict[str, AdmittedFact] = {}
    rejected_facts: dict[str, tuple[str, ...]] = {}
    synthetic_index = 0
    for path, value in _walk_mappings(data):
        if "operation" in value and "operands" in value:
            continue
        direct = _direct_id(value, _FACT_ID_FIELDS)
        if direct is None and not _is_validated_statement_row(path, value):
            continue
        ids = _evidence_ids(value)
        numeric_values = _numeric_tokens(value)
        if direct is None and not numeric_values:
            continue
        synthetic_index += 1
        fact_ref = direct or f"fact:{synthetic_index}"
        if not set(ids).issubset(admitted):
            rejected_facts[fact_ref] = ("fact_support_not_admitted",)
            continue
        owned = _owned_slots(ids, slots)
        if not owned:
            rejected_facts[fact_ref] = ("fact_slot_unowned",)
            continue
        unique_ref = fact_ref
        suffix = 2
        while unique_ref in facts or unique_ref in rejected_facts:
            unique_ref = f"{fact_ref}#{suffix}"
            suffix += 1
        facts[unique_ref] = AdmittedFact(unique_ref, ids, owned, numeric_values)

    calculations: dict[str, AdmittedCalculation] = {}
    rejected_calculations: dict[str, tuple[str, ...]] = {}
    calculation_index = 0
    for path, value in _walk_mappings(data):
        if "operation" not in value or "operands" not in value or "value" not in value:
            continue
        calculation_index += 1
        calculation_ref = _direct_id(value, _CALCULATION_ID_FIELDS) or f"calculation:{calculation_index}"
        reasons: list[str] = []
        operation = str(value.get("operation") or "")
        raw_operands = value.get("operands")
        operands = tuple(str(item) for item in raw_operands) if isinstance(raw_operands, (list, tuple)) else ()
        ids = _evidence_ids(value)
        parsed_operands = [_decimal(item) for item in operands]
        declared = _decimal(value.get("value"))
        if not ids or not set(ids).issubset(admitted):
            reasons.append("calculation_support_not_admitted")
        if not operands or any(item is None for item in parsed_operands) or declared is None:
            reasons.append("calculation_numeric_invalid")
        else:
            try:
                recomputed = calculate(operation, parsed_operands)
            except (TypeError, ValueError):
                reasons.append("calculation_operation_invalid")
            else:
                if recomputed.value != declared:
                    reasons.append("calculation_result_mismatch")
        operand_evidence: set[str] = set()
        for operand in parsed_operands:
            if operand is None:
                continue
            matching = [
                fact for fact in facts.values()
                if any(_decimal(number) == operand for number in fact.numeric_values)
                and set(fact.evidence_ids).issubset(set(ids))
            ]
            if not matching:
                reasons.append("calculation_operand_evidence_missing")
                continue
            operand_evidence.update(matching[0].evidence_ids)
        if parsed_operands and not operand_evidence.issubset(set(ids)):
            reasons.append("calculation_operand_evidence_missing")
        owned = _owned_slots(ids, slots)
        if not owned:
            reasons.append("calculation_slot_unowned")
        if reasons:
            rejected_calculations[calculation_ref] = _ordered(tuple(reasons))
            continue
        calculations[calculation_ref] = AdmittedCalculation(
            calculation_ref,
            operation,
            str(declared),
            tuple(str(item) for item in parsed_operands),
            ids,
            owned,
            _numeric_tokens(value),
        )
    return ClaimAdmission(
        evidence_ids=evidence_ids,
        evidence_slots=slots,
        facts=facts,
        calculations=calculations,
        rejected_facts=rejected_facts,
        rejected_calculations=rejected_calculations,
    )


@dataclass(frozen=True, slots=True)
class ClaimVerification:
    verified: bool
    claims: tuple[HcxAnswerClaim, ...]
    limitations: tuple[str, ...]
    failure_codes: tuple[str, ...]
    check_status: Mapping[str, str]

    def trace(self, status: str) -> dict[str, object]:
        return {
            "schema_version": CLAIM_CONTRACT_VERSION,
            "status": status,
            "claim_count": len(self.claims),
            "verified_claim_count": len(self.claims) if self.verified else 0,
            "checks": [
                {"check": check, "status": self.check_status[check]}
                for check in _TRACE_CHECKS
            ],
            "failure_codes": list(self.failure_codes),
        }


def verify_generated_claims(
    *,
    answer: str,
    citation_ids: Sequence[str],
    raw_claims: Sequence[object],
    raw_limitations: Sequence[str],
    admission: ClaimAdmission,
    policy_ok: bool = True,
    conclusion_ok: bool = True,
) -> ClaimVerification:
    reasons: list[str] = []
    categories: dict[str, list[str]] = {item: [] for item in _TRACE_CHECKS}
    claims: list[HcxAnswerClaim] = []
    try:
        claims = [HcxAnswerClaim.from_value(item) for item in raw_claims]
    except ValueError:
        reasons.append("claim_contract_invalid")
        categories["citations"].append("claim_contract_invalid")
    if not claims:
        reasons.append("claim_missing")
        categories["citations"].append("claim_missing")

    admitted_ids = set(admission.evidence_ids)
    top_ids = set(str(item) for item in citation_ids)
    claim_ids: set[str] = set()
    union_claim_citations: set[str] = set()
    union_claim_numbers: set[Decimal] = set()
    for claim in claims:
        if claim.claim_id in claim_ids:
            reasons.append("claim_id_duplicate")
            categories["citations"].append("claim_id_duplicate")
        claim_ids.add(claim.claim_id)
        citations = set(claim.citation_ids)
        union_claim_citations.update(citations)
        if not citations or not citations.issubset(admitted_ids):
            reasons.append("claim_citation_not_admitted")
            categories["citations"].append("claim_citation_not_admitted")

        selected_slots = [
            admission.evidence_slots.get(slot_id)
            for slot_id in claim.evidence_slot_ids
        ]
        slot_ids = set().union(*(set(item or ()) for item in selected_slots))
        if any(item is None for item in selected_slots) or not citations.issubset(slot_ids):
            reasons.append("evidence_slot_mismatch")
            categories["evidence_slots"].append("evidence_slot_mismatch")

        grounded_numbers: set[Decimal] = set()
        support_evidence: set[str] = set()
        for fact_ref in claim.fact_refs:
            fact = admission.facts.get(fact_ref)
            if fact is None:
                reasons.append("fact_ref_not_admitted")
                categories["numeric_values"].append("fact_ref_not_admitted")
                continue
            support_evidence.update(fact.evidence_ids)
            grounded_numbers.update(
                number for value in fact.numeric_values if (number := _decimal(value)) is not None
            )
        for calculation_ref in claim.calculation_refs:
            calculation = admission.calculations.get(calculation_ref)
            if calculation is None:
                reasons.extend(admission.rejected_calculations.get(
                    calculation_ref, ("calculation_ref_not_admitted",)
                ))
                categories["calculations"].extend(admission.rejected_calculations.get(
                    calculation_ref, ("calculation_ref_not_admitted",)
                ))
                continue
            support_evidence.update(calculation.evidence_ids)
            grounded_numbers.update(
                number
                for value in calculation.numeric_values
                if (number := _decimal(value)) is not None
            )
        if not support_evidence.issubset(citations) or not support_evidence.issubset(slot_ids):
            reasons.append("evidence_slot_mismatch")
            categories["evidence_slots"].append("evidence_slot_mismatch")

        declared_numbers: set[Decimal] = set()
        if _NONFINITE.search(claim.text):
            reasons.append("numeric_value_not_finite")
            categories["numeric_values"].append("numeric_value_not_finite")
        for value in claim.numeric_values:
            parsed = _decimal(value)
            if parsed is None:
                reasons.append("numeric_value_not_finite")
                categories["numeric_values"].append("numeric_value_not_finite")
                continue
            declared_numbers.add(parsed)
            union_claim_numbers.add(parsed)
            if parsed not in grounded_numbers:
                reasons.append("numeric_value_not_grounded")
                categories["numeric_values"].append("numeric_value_not_grounded")
        prose_numbers = {
            parsed for token in _numeric_tokens(claim.text)
            if (parsed := _decimal(token)) is not None
        }
        if not prose_numbers.issubset(declared_numbers):
            reasons.append("prose_number_unlisted")
            categories["numeric_values"].append("prose_number_unlisted")

    if not top_ids or not top_ids.issubset(admitted_ids) or top_ids != union_claim_citations:
        reasons.append("top_level_citations_mismatch")
        categories["citations"].append("top_level_citations_mismatch")
    answer_numbers = {
        parsed for token in _numeric_tokens(answer)
        if (parsed := _decimal(token)) is not None
    }
    if _NONFINITE.search(answer):
        reasons.append("numeric_value_not_finite")
        categories["numeric_values"].append("numeric_value_not_finite")
    if not answer_numbers.issubset(union_claim_numbers):
        reasons.append("prose_number_unlisted")
        categories["numeric_values"].append("prose_number_unlisted")
    if not policy_ok:
        reasons.append("policy_violation")
        categories["policy"].append("policy_violation")
    if not conclusion_ok:
        reasons.append("conclusion_mismatch")
        categories["policy"].append("conclusion_mismatch")

    stable_reasons = _ordered(tuple(reasons))
    return ClaimVerification(
        verified=not stable_reasons,
        claims=tuple(claims),
        limitations=_ordered(tuple(str(item) for item in raw_limitations if item)),
        failure_codes=stable_reasons,
        check_status={
            check: "failed" if categories[check] else "passed"
            for check in _TRACE_CHECKS
        },
    )


__all__ = [
    "CLAIM_CONTRACT_VERSION",
    "ClaimAdmission",
    "ClaimVerification",
    "HcxAnswerClaim",
    "build_claim_admission",
    "verify_generated_claims",
]
