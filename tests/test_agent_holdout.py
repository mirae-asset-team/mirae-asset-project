from __future__ import annotations

import copy
import unittest

from scripts.build_agent_holdout import build_holdout


def source_record(question_id: str, *, event_id: str = "event1") -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "question_id": question_id,
        "question": "테스트 계약금액은 얼마인가?",
        "question_type": "table_cell",
        "answerability": "answerable",
        "candidate_filing_ids": ["f1"],
        "version_evidence": [{"event_id": event_id, "filing_id": "f1"}],
        "company_resolution": {"query_name": "테스트"},
        "answer": {"kind": "numeric", "value": "2000", "unit": "원", "scale": 1},
        "evidence": [{"evidence_id": "ev1", "filing_id": "f1"}],
        "source_evidence": [{"source_id": "src1", "filing_id": "f1", "parse_status": "success"}],
        "review": {"status": "agent_audited", "annotator": "fixture", "reviewer": None},
        "answer_origin": "model_generated",
    }


class AgentHoldoutTests(unittest.TestCase):
    def test_related_questions_share_one_filing_group_split(self) -> None:
        records = [
            source_record("q1"),
            {**source_record("q2"), "question": "테스트 계약 상대는?", "question_type": "single_filing_fact", "answer": {"kind": "text", "text": "상대방"}},
        ]
        output = build_holdout(records)
        self.assertTrue(output)
        self.assertEqual({row["split_group"] for row in output}, {"event1"})
        self.assertEqual(len({row["split"] for row in output}), 1)

    def test_generation_is_order_independent_and_does_not_mutate_gold(self) -> None:
        records = [source_record("q2"), source_record("q1")]
        before = copy.deepcopy(records)
        first = build_holdout(records)
        second = build_holdout(list(reversed(records)))
        self.assertEqual(first, second)
        self.assertEqual(records, before)

    def test_negative_variants_clear_evidence_and_keep_unanswerable_shape(self) -> None:
        output = build_holdout([source_record("q1")])
        negatives = [row for row in output if row.get("holdout_variant") in {"future_negative", "adversarial_negative"}]
        self.assertEqual({row["answerability"] for row in negatives}, {"unanswerable"})
        self.assertTrue(all(row["evidence"] == [] for row in negatives))
        self.assertTrue(all(row["answer"]["kind"] == "unanswerable" for row in negatives))

    def test_generated_rows_are_not_human_verified(self) -> None:
        output = build_holdout([source_record("q1")])
        self.assertTrue(output)
        self.assertTrue(all(row.get("answer_origin") != "human_verified" for row in output))
        self.assertTrue(all(row.get("review", {}).get("status") != "human_verified" for row in output))

    def test_fact_variant_copies_expected_answer_and_evidence(self) -> None:
        source = source_record("q1")
        positive = next(row for row in build_holdout([source]) if row["holdout_variant"] == "financial_numeric")
        self.assertEqual(positive["answer"], source["answer"])
        self.assertEqual(positive["evidence"], source["evidence"])
        self.assertNotEqual(positive["question_id"], source["question_id"])

    def test_malformed_or_unreviewed_rows_are_skipped_audibly(self) -> None:
        malformed = {"question_id": "bad", "question": "누구?"}
        unreviewed = source_record("candidate")
        unreviewed["review"] = {"status": "candidate"}
        result = build_holdout([malformed, unreviewed])
        self.assertEqual(result, [])
        self.assertTrue(getattr(build_holdout, "last_rejections", []))


if __name__ == "__main__":
    unittest.main()
