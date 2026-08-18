from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.retrieval_evaluation import evaluate_retrieval


class RetrievalEvaluationTests(unittest.TestCase):
    def test_metrics_use_distinct_evidence_and_question_denominators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "empty.sqlite"
            sqlite3.connect(database).close()
            gold = root / "gold.jsonl"
            gold.write_text(json.dumps({
                "question_id": "q1",
                "question": "테스트 근거",
                "question_type": "single_filing_fact",
                "answerability": "answerable",
                "evidence": [{"evidence_id": "ev1"}, {"evidence_id": "ev2"}],
                "candidate_filing_ids": ["f1"],
                "company_resolution": {},
            }) + "\n", encoding="utf-8")
            ranked = [{"evidence_id": "frag1"}] + [{"evidence_id": f"noise{i}"} for i in range(7)] + [{"evidence_id": "frag2"}]
            class Reranker:
                def rerank(self, question, candidates, *, limit):
                    return type("RerankResult", (), {"evidence_ids": ["frag2", "frag1"], "used_provider": True, "reason_codes": []})()

            with patch("disclosure_db.retrieval_evaluation.retrieval_tokens", return_value=["term"]), \
                 patch("disclosure_db.retrieval_evaluation._rrf_term_search", return_value=ranked), \
                 patch("disclosure_db.retrieval_evaluation._target_fragments", side_effect=[["frag1"], ["frag2"]]):
                result = evaluate_retrieval(database=database, gold_path=gold, limit=20, reranker=Reranker())

        self.assertEqual(result["target_recall_at_k"], 1.0)
        self.assertEqual(result["target_recall_at_20"], 1.0)
        self.assertEqual(result["question_complete_recall_at_k"], 1.0)
        self.assertEqual(result["mrr_at_k"], 1.0)
        self.assertEqual(result["post_rerank_recall_at_8"], 1.0)

    def test_missing_reranker_reports_null_post_metric_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "empty.sqlite"
            sqlite3.connect(database).close()
            gold = root / "gold.jsonl"
            gold.write_text(json.dumps({"question_id": "q1", "question": "테스트", "question_type": "single_filing_fact", "answerability": "answerable", "evidence": [{"evidence_id": "ev1"}], "candidate_filing_ids": ["f1"], "company_resolution": {}}) + "\n", encoding="utf-8")
            with patch("disclosure_db.retrieval_evaluation.retrieval_tokens", return_value=["term"]), \
                 patch("disclosure_db.retrieval_evaluation._rrf_term_search", return_value=[{"evidence_id": "frag1"}]), \
                 patch("disclosure_db.retrieval_evaluation._target_fragments", return_value=["frag1"]):
                result = evaluate_retrieval(database=database, gold_path=gold, limit=20)
        self.assertIsNone(result["post_rerank_recall_at_8"])
        self.assertEqual(result["post_rerank_reason"], "reranker_not_configured")

    def test_unavailable_reranker_does_not_relabel_rrf_as_post_rerank(self) -> None:
        class Unavailable:
            def rerank(self, question, candidates, *, limit):
                return type("RerankResult", (), {"evidence_ids": ["frag1"], "used_provider": False, "reason_codes": ["reranker_timeout"]})()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "empty.sqlite"
            sqlite3.connect(database).close()
            gold = root / "gold.jsonl"
            gold.write_text(json.dumps({"question_id": "q1", "question": "테스트", "question_type": "single_filing_fact", "answerability": "answerable", "evidence": [{"evidence_id": "ev1"}], "candidate_filing_ids": ["f1"], "company_resolution": {}}) + "\n", encoding="utf-8")
            with patch("disclosure_db.retrieval_evaluation.retrieval_tokens", return_value=["term"]), \
                 patch("disclosure_db.retrieval_evaluation._rrf_term_search", return_value=[{"evidence_id": "frag1"}]), \
                 patch("disclosure_db.retrieval_evaluation._target_fragments", return_value=["frag1"]):
                result = evaluate_retrieval(database=database, gold_path=gold, limit=20, reranker=Unavailable())
        self.assertIsNone(result["post_rerank_recall_at_8"])
        self.assertEqual(result["post_rerank_reason"], "reranker_timeout")

    def test_mixed_reranker_success_and_failure_keeps_failed_question_in_denominator(self) -> None:
        class Mixed:
            def __init__(self):
                self.calls = 0
            def rerank(self, question, candidates, *, limit):
                self.calls += 1
                if self.calls == 1:
                    return {"evidence_ids": ["frag1"], "used_provider": True, "reason_codes": []}
                raise TimeoutError("provider timeout")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "empty.sqlite"
            sqlite3.connect(database).close()
            gold = root / "gold.jsonl"
            rows = [{"question_id": f"q{i}", "question": "테스트", "question_type": "single_filing_fact", "answerability": "answerable", "evidence": [{"evidence_id": f"ev{i}"}], "candidate_filing_ids": ["f1"], "company_resolution": {}} for i in (1, 2)]
            gold.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            with patch("disclosure_db.retrieval_evaluation.retrieval_tokens", return_value=["term"]), \
                 patch("disclosure_db.retrieval_evaluation._rrf_term_search", side_effect=[[{"evidence_id": "frag1"}], [{"evidence_id": "frag2"}]]), \
                 patch("disclosure_db.retrieval_evaluation._target_fragments", side_effect=[["frag1"], ["frag2"]]):
                result = evaluate_retrieval(database=database, gold_path=gold, limit=20, reranker=Mixed())
        self.assertEqual(result["post_rerank_recall_at_8"], 0.5)
        self.assertEqual(result["post_rerank_attempted_count"], 2)
        self.assertEqual(result["post_rerank_success_count"], 1)
        self.assertEqual(result["post_rerank_failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
