from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_QUESTION_TYPE_FIELDS = {"id", "description", "required_evidence", "primary_metrics"}
REQUIRED_GATE_GROUPS = {"structure_fail", "answer_block", "not_evaluated"}
REQUIRED_GOLD_RECORD_FIELDS = {
    "schema_version",
    "question_id",
    "question_type",
    "question",
    "answerability",
    "answer",
    "answer_origin",
    "candidate_filing_ids",
    "company_resolution",
    "period",
    "scope",
    "as_of",
    "version_basis",
    "formula",
    "attack_label",
    "evidence",
    "version_evidence",
    "source_evidence",
    "review",
}
REQUIRED_GOLD_REVIEW_FIELDS = {"status", "annotator", "reviewer", "reviewed_at", "notes"}
REQUIRED_EVIDENCE_BY_QUESTION_TYPE = {
    "single_filing_fact": {"filing_id", "evidence_id"},
    "table_cell": {"table_id", "cell_evidence_id", "unit", "period"},
    "period_comparison": {"operand_evidence_ids", "formula"},
    "cross_company_comparison": {"company_resolution", "operand_evidence_ids", "formula"},
    "correction_aware": {"event_id", "version_filing_ids", "as_of"},
    "multi_filing_synthesis": {"complete_evidence_set"},
    "unanswerable": set(),
    "adversarial": {"attack_label"},
}
RULE_MANIFEST_OR_SOURCE_MISSING = "manifest_or_source_missing"
RULE_FACT_WITHOUT_EVIDENCE = "fact_without_evidence"
RULE_EVIDENCE_ID_COLLISION = "evidence_id_collision"
RULE_EVENT_CURRENT_CARDINALITY = "event_without_exactly_one_current_version"
RULE_SILENT_PARSE_FAILURE = "silent_parse_failure"
RULE_LINEAGE_UNRESOLVED = "correction_lineage_unresolved"
RULE_MISSING_ORIGINAL = "missing_original"
RULE_PDF_TABLE_UNVALIDATED = "pdf_table_structure_unvalidated"
RULE_IMAGE_REVIEW = "image_reference_requires_review"

REQUIRED_RULES_BY_GATE = {
    "structure_fail": {
        RULE_MANIFEST_OR_SOURCE_MISSING,
        RULE_FACT_WITHOUT_EVIDENCE,
        RULE_EVIDENCE_ID_COLLISION,
        RULE_EVENT_CURRENT_CARDINALITY,
        RULE_SILENT_PARSE_FAILURE,
    },
    "answer_block": {"fact_not_validated", RULE_LINEAGE_UNRESOLVED, RULE_MISSING_ORIGINAL},
    "warning": {"recovery_parser_used", RULE_PDF_TABLE_UNVALIDATED, RULE_IMAGE_REVIEW},
}


def load_evaluation_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    schema_name = contract.get("gold_annotation_schema")
    if not isinstance(schema_name, str) or not schema_name:
        raise ValueError("evaluation contract requires gold_annotation_schema")
    schema_path = path.parent / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    missing_gold_fields = REQUIRED_GOLD_RECORD_FIELDS - set(schema.get("required", []))
    if missing_gold_fields:
        raise ValueError(f"gold annotation schema missing required fields: {sorted(missing_gold_fields)}")
    review_schema = schema.get("properties", {}).get("review", {})
    missing_review_fields = REQUIRED_GOLD_REVIEW_FIELDS - set(review_schema.get("required", []))
    if missing_review_fields:
        raise ValueError(f"gold annotation review schema missing fields: {sorted(missing_review_fields)}")
    if not isinstance(contract.get("question_types"), list) or not contract["question_types"]:
        raise ValueError("evaluation contract requires non-empty question_types")
    question_ids: list[str] = []
    for index, item in enumerate(contract["question_types"]):
        missing = REQUIRED_QUESTION_TYPE_FIELDS - set(item)
        if missing:
            raise ValueError(f"question_types[{index}] missing fields: {sorted(missing)}")
        question_id = str(item["id"])
        question_ids.append(question_id)
        if question_id not in REQUIRED_EVIDENCE_BY_QUESTION_TYPE:
            raise ValueError(f"unsupported question type: {question_id}")
        for field in ("required_evidence", "primary_metrics"):
            values = item[field]
            if not isinstance(values, list) or len(values) != len(set(values)) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise ValueError(f"question_types[{index}].{field} must be a unique string list")
        missing_evidence = REQUIRED_EVIDENCE_BY_QUESTION_TYPE[question_id] - set(item["required_evidence"])
        if missing_evidence:
            raise ValueError(
                f"question type {question_id} missing required evidence: {sorted(missing_evidence)}"
            )
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("evaluation contract question type ids must be unique")
    missing_question_types = set(REQUIRED_EVIDENCE_BY_QUESTION_TYPE) - set(question_ids)
    if missing_question_types:
        raise ValueError(f"evaluation contract missing question types: {sorted(missing_question_types)}")
    gate_groups = set(contract.get("quality_gates", {}))
    missing_gates = REQUIRED_GATE_GROUPS - gate_groups
    if missing_gates:
        raise ValueError(f"evaluation contract missing gate groups: {sorted(missing_gates)}")
    for gate, required_rules in REQUIRED_RULES_BY_GATE.items():
        values = contract["quality_gates"].get(gate)
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ValueError(f"evaluation contract gate {gate} must be a unique list")
        missing_rules = required_rules - set(values)
        if missing_rules:
            raise ValueError(f"evaluation contract gate {gate} missing rules: {sorted(missing_rules)}")
    return contract
