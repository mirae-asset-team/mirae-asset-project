from __future__ import annotations

import copy
import unittest

from disclosure_db.stress_generation import (
    canonical_json,
    source_sha256,
    validate_stress_case,
    validate_stress_cases,
)


def valid_case() -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "question_id": "stress_001",
        "question": "테스트의 계약금액은 얼마인가?",
        "question_type": "table_cell",
        "answerability": "answerable",
        "answer": {
            "kind": "numeric",
            "value": "10",
            "unit": "원",
            "scale": 1,
            "filing_id": "f1",
            "evidence_ids": ["ev1"],
        },
        "evidence": [{"evidence_id": "ev1", "filing_id": "f1"}],
        "stress": {
            "oracle": "exact",
            "category": "financial",
            "base_id": "base_001",
            "group_id": "group_001",
            "mutation_id": "base",
            "generator": "deterministic_stress_v1",
            "seed": 20260819,
            "source_sha256": "0" * 64,
            "trust_tier": "human_verified",
        },
    }


class AgentStressContractTests(unittest.TestCase):
    def test_canonical_hash_is_key_order_independent(self) -> None:
        self.assertEqual(source_sha256({"b": 2, "a": 1}), source_sha256({"a": 1, "b": 2}))
        self.assertEqual(canonical_json({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_validate_rejects_duplicate_or_unknown_oracle(self) -> None:
        case = valid_case()
        case["stress"]["oracle"] = "llm_judge"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unknown oracle"):
            validate_stress_case(case)
        duplicate = valid_case()
        with self.assertRaisesRegex(ValueError, "duplicate question_id"):
            validate_stress_cases([duplicate, copy.deepcopy(duplicate)])

    def test_numeric_exact_requires_decimal_unit_and_evidence(self) -> None:
        case = valid_case()
        case["answer"]["unit"] = None  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unit"):
            validate_stress_case(case)
        case = valid_case()
        case["answer"]["evidence_ids"] = []  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_stress_case(case)

    def test_abstention_requires_empty_evidence_and_no_numeric_claim(self) -> None:
        case = valid_case()
        case["answerability"] = "unanswerable"
        case["answer"] = {"kind": "unanswerable", "reason": "근거 없음"}
        case["evidence"] = []
        validate_stress_case(case)


if __name__ == "__main__":
    unittest.main()
