from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from disclosure_db.agent_contracts import VerifiedAnswer
from disclosure_db.agent_evaluation import evaluate_agent, evaluation_pass, percentile, quality_gate_passed
from scripts.evaluate_agent import load_stage_metrics


class AgentEvaluationTests(unittest.TestCase):
    @staticmethod
    def _record(*, answerability: str = "unanswerable", answer: dict[str, object] | None = None, evidence: list[dict[str, str]] | None = None) -> dict[str, object]:
        return {
            "question_id": "q1",
            "question": "테스트 질문",
            "answerability": answerability,
            "answer": answer or {"kind": "unanswerable", "reason": "없음"},
            "evidence": evidence or [],
        }

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
            gold.write_text('{"question": "broken"\n{"question_id":"safe", "question": "safe", "answerability": "unanswerable", "answer":{"kind":"unanswerable","reason":"none"}, "evidence":[]}\n', encoding="utf-8")
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
            gold.write_text('[]\n{"question_id":"safe", "question": "safe", "answerability": "unanswerable", "answer":{"kind":"unanswerable","reason":"none"}, "evidence":[]}\n', encoding="utf-8")
            result = evaluate_agent(FakeAgent(), gold)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["evaluations"][0]["status"], "error")
        self.assertEqual(result["evaluations"][0]["question_id"], "line-1")
        self.assertEqual(result["evaluations"][1]["status"], "pass")

    def test_zero_denominator_metrics_are_null(self) -> None:
        class FakeAgent:
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                return VerifiedAnswer("검증 가능한 근거가 충분하지 않아 답변할 수 없습니다.", [], True, False)

        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text('{"question_id":"safe","question":"미래 전망?","answerability":"unanswerable","answer":{"kind":"unanswerable"},"evidence":[]}\n', encoding="utf-8")
            result = evaluate_agent(FakeAgent(), gold)

        self.assertEqual(result["answerability_agreement"], 1.0)
        self.assertIsNone(result["numeric_exactness"])
        self.assertIsNone(result["citation_precision"])
        self.assertIsNone(result["citation_recall"])
        self.assertEqual(result["false_numeric_claim_count"], 0)
        self.assertEqual(result["unsafe_answer_count"], 0)

    def test_quality_gate_boundary_passes_and_fails(self) -> None:
        summary = {
            "error_count": 0,
            "false_numeric_claim_count": 0,
            "unsafe_answer_count": 0,
            "answerability_match_count": 31,
            "answerability_agreement": 1.0,
            "numeric_exactness": 1.0,
            "citation_precision": 1.0,
            "citation_recall": 0.9,
            "false_numeric_claim_count": 0,
            "unsafe_answer_count": 0,
            "latency_ms_p95": 20.0,
            "planner_p95_ms": 20.0,
        }
        acceptance = {
            "regression_answerability_matches": 31,
            "numeric_exactness": 1.0,
            "citation_precision": 1.0,
            "citation_recall": 0.9,
            "false_numeric_claims": 0,
            "unsafe_answers": 0,
            "planner_p95_ms": 20,
        }
        self.assertTrue(quality_gate_passed(summary, acceptance))
        summary["numeric_exactness"] = 0.99
        self.assertFalse(quality_gate_passed(summary, acceptance))

    def test_quality_gate_fails_when_any_configured_metric_is_missing_or_null(self) -> None:
        acceptance = {
            "regression_answerability_matches": 1,
            "numeric_exactness": 1.0,
            "citation_precision": 1.0,
            "retrieval_recall_at_20": 1.0,
            "post_rerank_recall_at_8": 0.9,
            "citation_recall": 0.9,
            "holdout_answerability_agreement": 0.9,
            "false_numeric_claims": 0,
            "unsafe_answers": 0,
            "planner_p95_ms": 20,
            "fact_lookup_p95_ms": 200,
            "local_retrieval_p95_ms": 800,
            "reranked_retrieval_p95_ms": 5000,
            "end_to_end_p95_ms": 10000,
        }
        summary = {"error_count": 0, "false_numeric_claim_count": 0, "unsafe_answer_count": 0}
        for key in acceptance:
            if key in {"false_numeric_claims", "unsafe_answers"}:
                continue
            summary[key] = 1.0 if not key.endswith("_ms") else 1.0
        self.assertFalse(quality_gate_passed(summary, acceptance))
        summary["planner_p95_ms"] = None
        self.assertFalse(quality_gate_passed(summary, acceptance))

    def test_quality_gate_requires_zero_errors_and_exact_threshold(self) -> None:
        acceptance = {"numeric_exactness": 1.0}
        self.assertTrue(quality_gate_passed({"numeric_exactness": 1.0, "error_count": 0, "false_numeric_claim_count": 0, "unsafe_answer_count": 0}, acceptance))
        self.assertFalse(quality_gate_passed({"numeric_exactness": 1.0, "error_count": 1, "false_numeric_claim_count": 0, "unsafe_answer_count": 0}, acceptance))
        self.assertFalse(quality_gate_passed({"numeric_exactness": 0.999, "error_count": 0, "false_numeric_claim_count": 0, "unsafe_answer_count": 0}, acceptance))

    def test_numeric_exactness_rejects_extra_duplicate_and_unanswerable_values(self) -> None:
        class FakeAgent:
            def __init__(self, values: list[str]):
                self.values = values
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                return VerifiedAnswer("답", ["ev1"], True, True, numeric_values=self.values)

        records = [self._record(answerability="answerable", answer={"kind": "numeric", "value": "1", "unit": "원", "scale": 1}, evidence=[{"evidence_id": "ev1"}])]
        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text("\n".join(__import__("json").dumps(row) for row in records) + "\n", encoding="utf-8")
            equivalent = evaluate_agent(FakeAgent(["1.0"]), gold)
            extra = evaluate_agent(FakeAgent(["1", "2"]), gold)
            duplicate = evaluate_agent(FakeAgent(["1", "1"]), gold)
        self.assertEqual(equivalent["numeric_exactness"], 1.0)
        self.assertEqual(extra["numeric_exactness"], 0.0)
        self.assertEqual(extra["false_numeric_claim_count"], 1)
        self.assertEqual(duplicate["numeric_exactness"], 0.0)

    def test_multi_numeric_requires_exact_decimal_multiset(self) -> None:
        class FakeAgent:
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                return VerifiedAnswer("답", ["ev1", "ev2"], True, True, numeric_values=["1.0", "2"])

        record = self._record(answerability="answerable", answer={"kind": "multi_numeric", "values": [{"label": "a", "value": "1", "unit": "원", "scale": 1, "evidence_ids": ["ev1"]}, {"label": "b", "value": "2", "unit": "원", "scale": 1, "evidence_ids": ["ev2"]}]}, evidence=[{"evidence_id": "ev1"}, {"evidence_id": "ev2"}])
        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text(__import__("json").dumps(record) + "\n", encoding="utf-8")
            result = evaluate_agent(FakeAgent(), gold)
        self.assertEqual(result["numeric_exactness"], 1.0)

    def test_invalid_evaluation_record_is_error_and_blocks_gate(self) -> None:
        class FailingAgent:
            def answer(self, question: str, *, as_of: str | None, limit: int) -> VerifiedAnswer:
                raise AssertionError("agent must not receive malformed row")

        with TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text('{"question":"missing answerability","evidence":[]}\n', encoding="utf-8")
            result = evaluate_agent(FailingAgent(), gold, acceptance={"numeric_exactness": 0.0})
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["evaluations"][0]["status"], "error")
        self.assertFalse(result["quality_gate_passed"])

    def test_quality_gate_rejects_nonfinite_and_invalid_numeric_types_without_raising(self) -> None:
        base = {"error_count": 0, "false_numeric_claim_count": 0, "unsafe_answer_count": 0, "numeric_exactness": 1.0}
        self.assertFalse(quality_gate_passed({**base, "numeric_exactness": float("nan")}, {"numeric_exactness": 1.0}))
        self.assertFalse(quality_gate_passed({**base, "numeric_exactness": float("inf")}, {"numeric_exactness": 1.0}))
        self.assertFalse(quality_gate_passed({**base, "numeric_exactness": True}, {"numeric_exactness": 1.0}))
        self.assertFalse(quality_gate_passed({**base, "error_count": "0"}, {"numeric_exactness": 1.0}))
        self.assertFalse(quality_gate_passed(base, {"numeric_exactness": float("nan")}))

    def test_stage_metrics_artifact_requires_finite_numeric_p95_values(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stages.json"
            path.write_text('{"planner_p95_ms": 1, "fact_lookup_p95_ms": 2, "local_retrieval_p95_ms": 3, "reranked_retrieval_p95_ms": 4}\n', encoding="utf-8")
            values, errors = load_stage_metrics(path)
            self.assertEqual(values["planner_p95_ms"], 1.0)
            self.assertEqual(errors, [])
            path.write_text('{"planner_p95_ms": NaN, "fact_lookup_p95_ms": true}\n', encoding="utf-8")
            values, errors = load_stage_metrics(path)
        self.assertEqual(values, {})
        self.assertTrue(errors)

    def test_stage_metrics_missing_artifact_is_auditable_and_gate_fails(self) -> None:
        values, errors = load_stage_metrics(None)
        self.assertEqual(values, {})
        self.assertIn("stage_metrics_missing", errors)

    def test_complete_stage_metrics_can_satisfy_stage_latency_thresholds(self) -> None:
        summary = {"error_count": 0, "false_numeric_claim_count": 0, "unsafe_answer_count": 0, "answerability_match_count": 31, "numeric_exactness": 1.0, "citation_precision": 1.0, "target_recall_at_20": 1.0, "post_rerank_recall_at_8": 0.9, "citation_recall": 0.9, "answerability_agreement": 0.9, "planner_p95_ms": 20.0, "fact_lookup_p95_ms": 200.0, "local_retrieval_p95_ms": 800.0, "reranked_retrieval_p95_ms": 5000.0, "latency_ms_p95": 10000.0}
        acceptance = {"regression_answerability_matches": 31, "numeric_exactness": 1.0, "citation_precision": 1.0, "retrieval_recall_at_20": 1.0, "post_rerank_recall_at_8": 0.9, "citation_recall": 0.9, "holdout_answerability_agreement": 0.9, "false_numeric_claims": 0, "unsafe_answers": 0, "planner_p95_ms": 20, "fact_lookup_p95_ms": 200, "local_retrieval_p95_ms": 800, "reranked_retrieval_p95_ms": 5000, "end_to_end_p95_ms": 10000}
        self.assertTrue(quality_gate_passed(summary, acceptance))


if __name__ == "__main__":
    unittest.main()
