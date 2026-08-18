from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from disclosure_db.agent_contracts import VerifiedAnswer
from disclosure_db.agent_evaluation import evaluate_agent, evaluation_pass, percentile


class AgentEvaluationTests(unittest.TestCase):
    def test_verified_abstention_is_not_a_pass_for_answerable_gold(self) -> None:
        self.assertFalse(evaluation_pass(
            expected_answerable=True,
            actual_answerable=False,
            verified=True,
            citation_recall=0.0,
            numeric_match=False,
            text_match=None,
        ))

    def test_unanswerable_pass_requires_safe_abstention(self) -> None:
        self.assertTrue(evaluation_pass(
            expected_answerable=False,
            actual_answerable=False,
            verified=True,
            citation_recall=None,
            numeric_match=None,
            text_match=None,
        ))
        self.assertFalse(evaluation_pass(
            expected_answerable=False,
            actual_answerable=True,
            verified=True,
            citation_recall=None,
            numeric_match=None,
            text_match=None,
        ))

    def test_answerable_pass_requires_complete_expected_checks(self) -> None:
        self.assertTrue(evaluation_pass(
            expected_answerable=True,
            actual_answerable=True,
            verified=True,
            citation_recall=1.0,
            numeric_match=True,
            text_match=None,
        ))
        self.assertFalse(evaluation_pass(
            expected_answerable=True,
            actual_answerable=True,
            verified=True,
            citation_recall=0.5,
            numeric_match=True,
            text_match=None,
        ))

    def test_percentile_is_deterministic_for_short_runs(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 4.0)
        self.assertIsNone(percentile([], 0.95))

    def test_malformed_json_row_is_audited_and_evaluation_continues(self) -> None:
        class FakeAgent:
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                return VerifiedAnswer("검증 가능한 근거가 충분하지 않아 답변할 수 없습니다.", [], True, False)

        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text('{"question": "broken"\n{"question": "safe", "answerability": "unanswerable"}\n', encoding="utf-8")
            result = evaluate_agent(FakeAgent(), gold)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["evaluations"][0]["status"], "error")
        self.assertEqual(result["evaluations"][0]["question_id"], "line-1")
        self.assertEqual(result["evaluations"][1]["status"], "pass")

    def test_non_object_json_row_is_audited_and_evaluation_continues(self) -> None:
        class FakeAgent:
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                return VerifiedAnswer("검증 가능한 근거가 충분하지 않아 답변할 수 없습니다.", [], True, False)

        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text('[]\n{"question": "safe", "answerability": "unanswerable"}\n', encoding="utf-8")
            result = evaluate_agent(FakeAgent(), gold)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["evaluations"][0]["status"], "error")
        self.assertEqual(result["evaluations"][0]["question_id"], "line-1")
        self.assertEqual(result["evaluations"][1]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
