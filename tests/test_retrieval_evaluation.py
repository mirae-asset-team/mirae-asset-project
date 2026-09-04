from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from disclosure_db.retrieval_evaluation import (
    audit_financial_fact_coverage,
    evaluate_hybrid_retrieval,
    evaluate_retrieval,
)
from scripts.evaluate_retrieval import parse_args


class RetrievalEvaluationTests(unittest.TestCase):
    def test_retrieval_cli_rejects_partial_structured_configuration(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--overlay", "overlay.sqlite"])
        with self.assertRaises(SystemExit):
            parse_args(["--attestation", "attestation.json"])
        with self.assertRaises(SystemExit):
            parse_args(["--search-index", "search.sqlite"])
        with self.assertRaises(SystemExit):
            parse_args(["--overlay", "overlay.sqlite", "--attestation", "attestation.json"])

    def test_retrieval_cli_accepts_complete_hybrid_and_inventory_configuration(self) -> None:
        args = parse_args([
            "--database", "base.sqlite", "--gold", "gold.jsonl",
            "--overlay", "overlay.sqlite", "--attestation", "attestation.json",
            "--search-index", "search.sqlite", "--financial-seed", "seed.jsonl",
            "--inventory-output", "inventory.json", "--output", "result.json",
        ])

        self.assertEqual(args.overlay, Path("overlay.sqlite"))
        self.assertEqual(args.attestation, Path("attestation.json"))
        self.assertEqual(args.search_index, Path("search.sqlite"))
        self.assertEqual(args.financial_seed, Path("seed.jsonl"))
        self.assertEqual(args.inventory_output, Path("inventory.json"))

    def test_retrieval_cli_rejects_input_output_and_output_output_collisions(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--database", "same.sqlite", "--output", "same.sqlite"])
        with self.assertRaises(SystemExit):
            parse_args([
                "--overlay", "overlay.sqlite", "--attestation", "attestation.json",
                "--search-index", "search.sqlite", "--output", "overlay.sqlite",
            ])
        with self.assertRaises(SystemExit):
            parse_args([
                "--output", "same.json", "--inventory-output", "same.json",
            ])
        with self.assertRaises(SystemExit):
            parse_args([
                "--gold", "gold.jsonl", "--inventory-output", "gold.jsonl",
            ])

    def test_hybrid_evaluation_preserves_sparse_metrics_and_attributes_actual_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            rows = [
                {
                    "question_id": "q-fin", "question": "회사 2025년 연결 매출액은 얼마인가?",
                    "question_type": "table_cell", "answerability": "answerable",
                    "company_resolution": {"issuer_name": "회사"},
                    "candidate_filing_ids": ["f1"], "evidence": [{"evidence_id": "ev-fin"}],
                },
                {
                    "question_id": "q-text", "question": "회사 공시 제목은 무엇인가?",
                    "question_type": "single_filing_fact", "answerability": "answerable",
                    "company_resolution": {"issuer_name": "회사"},
                    "candidate_filing_ids": ["f2"], "evidence": [{"evidence_id": "ev-text"}],
                },
            ]
            gold.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            class Service:
                def search(self, plan, *, limit):
                    evidence_id = "ev-fin" if "매출액" in plan.question else "ev-text"
                    return SimpleNamespace(
                        evidence=[SimpleNamespace(evidence_id=evidence_id)], answerable=True,
                        reason_codes=[],
                        financial_facts=[{"evidence_ids": ["ev-fin"]}] if evidence_id == "ev-fin" else [],
                        event_facts=[],
                    )

            sparse = {"target_recall_at_k": 0.5, "evaluations": [{"question_id": "sparse-kept"}]}
            with patch("disclosure_db.retrieval_evaluation.evaluate_retrieval", return_value=sparse):
                result = evaluate_hybrid_retrieval(
                    database=Path("base.sqlite"), gold_path=gold, evidence_service=Service(), limit=20,
                )

        self.assertEqual(result["target_recall_at_k"], 0.5)
        self.assertEqual(result["evaluations"], [{"question_id": "sparse-kept"}])
        self.assertEqual(result["hybrid"]["target_recall_at_k"], 1.0)
        self.assertEqual(result["hybrid"]["question_complete_recall_at_k"], 1.0)
        self.assertEqual(result["hybrid"]["route_counts"], {"sparse_text": 1, "structured_financial": 1})
        self.assertEqual(result["hybrid"]["residual_targets"], [])

    def test_hybrid_evaluation_fails_closed_when_service_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text(json.dumps({
                "question_id": "q1", "question": "회사 공시 제목", "question_type": "single_filing_fact",
                "answerability": "answerable", "company_resolution": {"issuer_name": "회사"},
                "candidate_filing_ids": ["f1"], "evidence": [{"evidence_id": "ev1"}],
            }) + "\n", encoding="utf-8")

            class BrokenService:
                def search(self, plan, *, limit):
                    raise RuntimeError("attestation mismatch")

            sparse = {"target_recall_at_k": 0.25, "evaluations": []}
            with patch("disclosure_db.retrieval_evaluation.evaluate_retrieval", return_value=sparse):
                result = evaluate_hybrid_retrieval(
                    database=Path("base.sqlite"), gold_path=gold, evidence_service=BrokenService(), limit=20,
                )

        self.assertEqual(result["target_recall_at_k"], 0.25)
        self.assertIsNone(result["hybrid"]["target_recall_at_k"])
        self.assertEqual(result["hybrid"]["reason_code_counts"], {"hybrid_service_error": 1})
        self.assertEqual(result["hybrid"]["residual_targets"][0]["evidence_id"], "ev1")

    def test_hybrid_evaluation_invalidates_metrics_on_dependency_reason_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gold = Path(directory) / "gold.jsonl"
            gold.write_text(json.dumps({
                "question_id": "q1", "question": "회사 2025년 연결 매출액", "question_type": "table_cell",
                "answerability": "answerable", "company_resolution": {"issuer_name": "회사"},
                "candidate_filing_ids": ["f1"], "evidence": [{"evidence_id": "ev1"}],
            }) + "\n", encoding="utf-8")

            class InvalidDependencyService:
                def search(self, plan, *, limit):
                    return SimpleNamespace(
                        evidence=[SimpleNamespace(evidence_id="ev1")], answerable=True,
                        reason_codes=["overlay_base_attestation_failed"],
                        financial_facts=[{"evidence_ids": ["ev1"]}], event_facts=[],
                    )

            with patch("disclosure_db.retrieval_evaluation.evaluate_retrieval", return_value={"evaluations": []}):
                result = evaluate_hybrid_retrieval(
                    database=Path("base.sqlite"), gold_path=gold,
                    evidence_service=InvalidDependencyService(), limit=20,
                )

        self.assertEqual(result["hybrid"]["status"], "failed_closed")
        self.assertIsNone(result["hybrid"]["target_recall_at_k"])
        self.assertIsNone(result["hybrid"]["question_complete_recall_at_k"])
        self.assertEqual(result["hybrid"]["dependency_failure_count"], 1)

    def test_financial_fact_coverage_requires_exact_gold_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed.jsonl"
            gold = root / "gold.jsonl"
            seed_rows = [
                {
                    "financial_fact_id": "ff1", "filing_id": "f1", "value_numeric": "1000",
                    "scale": 1, "evidence_ids": ["ev1"], "validation_status": "validated",
                },
                {
                    "financial_fact_id": "ff2", "filing_id": "f2", "value_numeric": "2000",
                    "scale": 1, "evidence_ids": ["ev2"], "validation_status": "validated",
                },
            ]
            gold_rows = [{
                "question_id": "agent_financial_ff1", "answerability": "answerable",
                "candidate_filing_ids": ["f1"],
                "answer": {"kind": "numeric", "value": "1000", "scale": 1},
                "evidence": [{"evidence_id": "ev1", "filing_id": "f1"}],
                "audit": {"state": "agent_audited"},
            }]
            seed.write_text("\n".join(json.dumps(row) for row in seed_rows) + "\n", encoding="utf-8")
            gold.write_text("\n".join(json.dumps(row) for row in gold_rows) + "\n", encoding="utf-8")

            with patch("disclosure_db.retrieval_evaluation.validate_record_contract", return_value=[]):
                result = audit_financial_fact_coverage(seed_path=seed, gold_path=gold)

        self.assertEqual(result["scope"], "checked_in_validated_seed_vs_audited_gold")
        self.assertEqual(result["validated_seed_count"], 2)
        self.assertEqual(result["covered_seed_count"], 1)
        self.assertEqual(result["gap_seed_count"], 1)
        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["coverage_by_trust_tier"], {"agent_audited": 1})
        self.assertFalse(result["corpus_wide_complete"])
        self.assertEqual(result["facts"][0]["status"], "covered")
        self.assertEqual(result["facts"][1]["reason_codes"], ["missing_gold"])

    def test_financial_fact_coverage_rejects_duplicates_and_field_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed.jsonl"
            gold = root / "gold.jsonl"
            seed.write_text(json.dumps({
                "financial_fact_id": "ff1", "filing_id": "f1", "value_numeric": "1000.00",
                "scale": 1, "evidence_ids": ["ev1", "ev2"], "validation_status": "validated",
            }) + "\n", encoding="utf-8")
            bad = {
                "question_id": "agent_financial_ff1", "answerability": "answerable",
                "candidate_filing_ids": ["other"],
                "answer": {"kind": "numeric", "value": "999", "scale": 1000},
                "evidence": [{"evidence_id": "wrong", "filing_id": "other"}],
                "audit": {"state": "agent_audited"},
            }
            gold.write_text(json.dumps(bad) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")

            with patch("disclosure_db.retrieval_evaluation.validate_record_contract", return_value=[]):
                result = audit_financial_fact_coverage(seed_path=seed, gold_path=gold)

        self.assertEqual(result["covered_seed_count"], 0)
        self.assertEqual(
            result["facts"][0]["reason_codes"],
            ["duplicate_gold", "evidence_filing_mismatch", "evidence_mismatch", "filing_mismatch", "scale_mismatch", "value_mismatch"],
        )

    def test_financial_fact_coverage_rejects_duplicate_seed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed.jsonl"
            gold = root / "gold.jsonl"
            seed_row = {
                "financial_fact_id": "ff1", "filing_id": "f1", "value_numeric": "1000",
                "scale": 1, "evidence_ids": ["ev1"], "validation_status": "validated",
            }
            gold_row = {
                "question_id": "agent_financial_ff1", "answerability": "answerable",
                "candidate_filing_ids": ["f1"],
                "answer": {"kind": "numeric", "value": "1000", "scale": 1},
                "evidence": [{"evidence_id": "ev1", "filing_id": "f1"}],
                "audit": {"state": "agent_audited"},
            }
            seed.write_text(json.dumps(seed_row) + "\n" + json.dumps(seed_row) + "\n", encoding="utf-8")
            gold.write_text(json.dumps(gold_row) + "\n", encoding="utf-8")

            with patch("disclosure_db.retrieval_evaluation.validate_record_contract", return_value=[]):
                result = audit_financial_fact_coverage(seed_path=seed, gold_path=gold)

        self.assertEqual(result["covered_seed_count"], 0)
        self.assertEqual(result["gap_seed_count"], 2)
        self.assertTrue(all("duplicate_seed" in fact["reason_codes"] for fact in result["facts"]))

    def test_financial_fact_coverage_rejects_malformed_gold_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed.jsonl"
            gold = root / "gold.jsonl"
            seed.write_text(json.dumps({
                "financial_fact_id": "ff1", "filing_id": "f1", "value_numeric": "1000",
                "scale": 1, "evidence_ids": ["ev1"], "validation_status": "validated",
            }) + "\n", encoding="utf-8")
            gold.write_text(json.dumps({
                "question_id": "agent_financial_ff1", "answerability": "answerable",
                "candidate_filing_ids": ["f1"], "answer": {"kind": "numeric", "value": "1000", "scale": 1},
                "evidence": [{"evidence_id": "ev1", "filing_id": "f1"}],
                "review": {"status": "approved"},
            }) + "\n", encoding="utf-8")

            result = audit_financial_fact_coverage(seed_path=seed, gold_path=gold)

        self.assertEqual(result["covered_seed_count"], 0)
        self.assertIn("invalid_gold_contract", result["facts"][0]["reason_codes"])

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
