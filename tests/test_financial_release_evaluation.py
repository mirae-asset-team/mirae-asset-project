from __future__ import annotations

import unittest

from disclosure_db.agent_contracts import VerifiedAnswer
from disclosure_db.financial_release_evaluation import (
    build_release_cases,
    evaluate_release_answer,
    measure_concurrent_requests,
    release_hard_gate,
)


class FinancialReleaseEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fact = {
            "account_id": "revenue",
            "account_name_raw": "영업수익",
            "evidence_ids": ["ev-1"],
            "filing_id": "20260310000001",
            "fiscal_year": 2025,
            "issuer_corp_code": "001",
            "listed_name": "테스트금융",
            "scale": 1_000_000,
            "scope": "consolidated",
            "unit_raw": "백만원",
            "value_numeric": "1234",
        }
        self.manifest = {
            "companies": [{
                "aliases": ["테스트금융", "테스트 파이낸셜"],
                "filing_id": "20260310000001",
                "issuer_corp_code": "001",
                "listed_name": "테스트금융",
            }]
        }

    def test_builds_latest_cases_for_every_search_target_and_three_year_cases(self) -> None:
        facts = [
            {**self.fact, "fiscal_year": year, "value_numeric": str(value), "evidence_ids": [f"ev-{year}"]}
            for year, value in ((2025, 3), (2024, 2), (2023, 1))
        ]

        cases = build_release_cases(
            search_targets=["테스트금융", "테스트 파이낸셜", "미수록회사"],
            manifest=self.manifest,
            facts=facts,
            account_labels={"revenue": "매출액"},
        )

        latest = [case for case in cases if case["kind"] == "latest"]
        history = [case for case in cases if case["kind"] == "three_year"]
        self.assertEqual(len(latest), 3)
        self.assertEqual(len(history), 1)
        self.assertEqual(latest[-1]["expected_facts"], [])
        self.assertEqual([row["fiscal_year"] for row in history[0]["expected_facts"]], [2025, 2024, 2023])

    def test_exact_verified_fact_passes_and_metadata_mismatch_fails(self) -> None:
        case = {"case_id": "c1", "expected_facts": [self.fact]}
        answer = VerifiedAnswer(
            "답",
            ["ev-1"],
            True,
            True,
            numeric_values=["1234"],
            financial_facts=[dict(self.fact)],
        )

        passed = evaluate_release_answer(case, answer)
        answer.financial_facts[0]["scale"] = 1
        failed = evaluate_release_answer(case, answer)

        self.assertTrue(passed["passed"])
        self.assertIn("fact_metadata_mismatch", failed["failures"])

    def test_missing_fact_requires_clean_abstention(self) -> None:
        case = {"case_id": "missing", "expected_facts": []}
        clean = VerifiedAnswer("보류", [], False, False)
        unsafe = VerifiedAnswer("1원", ["ev-x"], True, True, numeric_values=["1"])

        self.assertTrue(evaluate_release_answer(case, clean)["passed"])
        self.assertIn("false_numeric_claim", evaluate_release_answer(case, unsafe)["failures"])

    def test_hard_gate_requires_all_cases_and_concurrency_threshold(self) -> None:
        passed, reasons = release_hard_gate(
            [{"passed": True}, {"passed": True}],
            concurrency={"request_count": 20, "error_count": 0, "p95_ms": 1999.9},
        )
        failed, failed_reasons = release_hard_gate(
            [{"passed": True}, {"passed": False}],
            concurrency={"request_count": 20, "error_count": 0, "p95_ms": 2000.1},
        )

        self.assertTrue(passed)
        self.assertEqual(reasons, [])
        self.assertFalse(failed)
        self.assertIn("case_failures", failed_reasons)
        self.assertIn("concurrency_p95_exceeded", failed_reasons)

    def test_measures_exactly_twenty_concurrent_requests_and_errors(self) -> None:
        successful = measure_concurrent_requests(lambda: None)
        failed = measure_concurrent_requests(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        self.assertEqual(successful["request_count"], 20)
        self.assertEqual(successful["error_count"], 0)
        self.assertEqual(len(successful["latencies_ms"]), 20)
        self.assertEqual(failed["error_count"], 20)


if __name__ == "__main__":
    unittest.main()
