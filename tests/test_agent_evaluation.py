from __future__ import annotations

import unittest

from disclosure_db.agent_evaluation import evaluation_pass, percentile


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


if __name__ == "__main__":
    unittest.main()
