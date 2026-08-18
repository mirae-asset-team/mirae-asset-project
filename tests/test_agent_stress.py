from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from disclosure_db.stress_generation import (
    build_stress_cases,
    canonical_json,
    split_groups,
    source_sha256,
    validate_stress_case,
    validate_stress_cases,
)
from disclosure_db.stress_evaluation import run_fault_case, score_case, score_metamorphic_group, should_skip


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

    def test_build_cases_is_deterministic_and_matches_category_counts(self) -> None:
        contract = {
            "case_count": 9,
            "allocation": {
                "financial": 1, "event": 1, "correction": 1,
                "calculation": 1, "retrieval": 1, "unanswerable": 1,
                "adversarial": 1, "language": 1, "fault": 1,
            },
        }
        records = [valid_case()]
        first = build_stress_cases(records, contract)
        second = build_stress_cases(list(reversed(records)), contract)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(len(first), 9)
        self.assertEqual({case["stress"]["category"] for case in first}, set(contract["allocation"]))
        adversarial = next(case for case in first if case["stress"]["category"] == "adversarial")
        self.assertIn("주가 전망", adversarial["question"])
        validate_stress_cases(first)

    def test_source_unanswerable_is_mutated_to_safe_abstention_prompt(self) -> None:
        record = valid_case()
        record["answerability"] = "unanswerable"
        record["answer"] = {"kind": "unanswerable", "reason": "none"}
        record["evidence"] = []
        cases = build_stress_cases([record], {"case_count": 1, "allocation": {"calculation": 1}})
        self.assertEqual(cases[0]["answerability"], "unanswerable")
        self.assertIn("확인할 수 없는 미래 사실", cases[0]["question"])

    def test_group_split_never_separates_base_and_mutations(self) -> None:
        cases = []
        for index in range(10):
            case = valid_case()
            case["question_id"] = f"stress_{index}"
            case["stress"]["group_id"] = f"group_{index // 2}"  # type: ignore[index]
            cases.append(case)
        train, holdout = split_groups(cases, 0.2)
        self.assertTrue(
            {case["stress"]["group_id"] for case in train}.isdisjoint(
                {case["stress"]["group_id"] for case in holdout}
            )
        )

    def test_exact_numeric_requires_answerability_value_unit_and_required_evidence(self) -> None:
        case = valid_case()
        verified = {"answerable": True, "verified": True, "answer": case["answer"], "citations": ["ev1"]}
        self.assertTrue(score_case(case, verified).passed)
        wrong = copy.deepcopy(verified)
        wrong["answer"]["value"] = "11"  # type: ignore[index]
        self.assertFalse(score_case(case, wrong).passed)

    def test_abstention_rejects_numeric_or_citations(self) -> None:
        case = valid_case()
        case["answerability"] = "unanswerable"
        case["answer"] = {"kind": "unanswerable", "reason": "없음"}
        case["evidence"] = []
        bad = {"answerable": True, "verified": True, "answer": {"kind": "numeric", "value": "1"}, "citations": ["ev1"]}
        score = score_case(case, bad)
        self.assertFalse(score.passed)
        self.assertIn("false_numeric_claim", score.failures)

    def test_metamorphic_group_requires_same_answerability_value_and_core_evidence(self) -> None:
        base = {"answerable": True, "value": "10", "unit": "원", "evidence_ids": ["ev1"]}
        changed_value = {"answerable": True, "value": "11", "unit": "원", "evidence_ids": ["ev1"]}
        self.assertFalse(score_metamorphic_group([base, changed_value]).passed)

    def test_resume_skips_only_matching_case_hash_and_commit(self) -> None:
        completed = {"case_id": "c1", "input_hash": "a", "git_commit": "g1"}
        matching = {"question_id": "c1", "input_hash": "a"}
        changed = {"question_id": "c1", "input_hash": "b"}
        self.assertTrue(should_skip(matching, "g1", completed))
        self.assertFalse(should_skip(changed, "g1", completed))
        self.assertFalse(should_skip(matching, "g2", completed))

    def test_fault_fixture_never_mutates_source_database(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            source.write_bytes(b"fixture-source")
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = run_fault_case(source, Path(directory) / "fault")
            self.assertTrue(result["fault_isolated"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
