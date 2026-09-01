from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from disclosure_db.disclosure_tools import format_financial_value
from disclosure_db.qa_evaluation import QaCaseStore, QaEvaluator, validate_case
from disclosure_db.hcx_function_calling import (
    FunctionCallingResult, HcxFunctionCallingService, HcxGeneratedAnswer,
)
from disclosure_db.question_routing import DeterministicQuestionRouter


class FakeEvaluationService:
    def __init__(self) -> None:
        self.router = DeterministicQuestionRouter(
            ["에스엠", "하이브"],
            company_aliases={
                "에스엠": ["SM엔터테인먼트", "에스엠엔터테인먼트"],
                "하이브": ["하이브엔터테인먼트"],
            },
        )

    def answer(self, question: str) -> FunctionCallingResult:
        return FunctionCallingResult(
            status="answered",
            answer="에스엠과 하이브의 매출액을 비교했습니다.",
            tool_name="get_financial_facts",
            tool_response={
                "status": "success",
                "tool_name": "get_financial_facts",
                "data": {
                    "facts": [
                        {"display_value": "1,000억 원"},
                        {"display_value": "2,000억 원"},
                    ],
                    "comparison": {"status": "complete", "winner": "하이브"},
                },
                "evidence_bundle": {},
                "warnings": [],
                "metadata": {
                    "executors": ["get_financial_facts", "get_financial_facts"],
                    "sufficiency_check": {"status": "sufficient"},
                },
            },
            answer_allowed=True,
            recommended_action="answer",
            citation_ids=["ev-1", "ev-2"],
            citations=[
                {"evidence_id": "ev-1", "rcept_no": "20260318000001"},
                {"evidence_id": "ev-2", "rcept_no": "20260318000002"},
            ],
            metadata={
                "tool_selection_called": False,
                "final_generation_called": True,
            },
        )


class QaEvaluationTests(unittest.TestCase):
    def test_company_aliases_are_canonical_and_not_reported_as_typos(self) -> None:
        router = FakeEvaluationService().router

        route = router.route(
            "SM엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?"
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.workflow, "financial_comparison")
        self.assertEqual(route.context["companies"], ["에스엠", "하이브"])
        self.assertEqual(route.context["period"], "2025")
        self.assertTrue(all(item.startswith("alias:") for item in route.corrections))

    def test_backend_formats_value_scale_contract_without_hcx_math(self) -> None:
        self.assertEqual(format_financial_value("2649870246", 1, "KRW"), "26억 4,987만 246원")
        self.assertEqual(
            format_financial_value("11749341815900000", 1, "KRW"),
            "11,749조 3,418억 1,590만 원",
        )
        self.assertIsNone(format_financial_value("7332", 1, None))

    def test_real_comparison_workflow_executes_both_companies_and_blocks_missing_value(self) -> None:
        def financial(company: str, value: str, index: int) -> dict[str, object]:
            evidence_id = f"ev-{index}"; receipt = f"2026031800000{index}"
            fact = {
                "value_numeric": value, "scale": 1, "normalized_value": value,
                "display_value": format_financial_value(value, 1, "KRW"), "unit": "KRW",
                "period": {"period_start": "2025-01-01", "period_end": "2025-12-31"},
                "scope": "consolidated", "support_level": "structured",
                "validation_status": "validated", "evidence_ids": [evidence_id],
            }
            return {
                "status": "success", "tool_name": "get_financial_facts",
                "data": {"facts": [fact]},
                "evidence_bundle": {
                    "question_intent": "get_financial_facts", "requested_scope": {},
                    "covered_scope": {"company": [company], "account": ["매출액"]},
                    "items": [{"evidence_id": evidence_id, "evidence_ids": [evidence_id], "filing_id": receipt, "rcept_no": receipt, "report_name": "사업보고서", "quality_status": "validated_structured_evidence"}],
                    "evidence_ids": [evidence_id], "filing_ids": [receipt], "issuer_corp_codes": [],
                    "quality_warnings": [], "correction_status": "policy_applied",
                    "retrieval_status": {}, "sufficiency": "sufficient",
                },
                "warnings": [], "metadata": {"sufficiency_check": {"status": "sufficient", "answer_allowed": True, "recommended_action": "answer"}},
            }

        class Registry:
            def __init__(self, missing_second: bool = False): self.calls = []; self.missing_second = missing_second
            def list_tools(self): return []
            def dispatch(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if self.missing_second and arguments["company"] == "하이브":
                    response = financial("하이브", "0", 2); response["status"] = "insufficient"; response["data"]["facts"] = []; response["metadata"]["sufficiency_check"].update({"status": "insufficient", "answer_allowed": False, "recommended_action": "abstain"}); return response
                return financial(arguments["company"], "100" if arguments["company"] == "에스엠" else "200", 1 if arguments["company"] == "에스엠" else 2)
        class Client:
            model = "fake"; configured = True
            def __init__(self): self.final_calls = 0
            def select_tool(self, question, tools): raise AssertionError
            def generate_answer(self, question, call, response): raise AssertionError
            def generate_routed_answer(self, question, call, response): self.final_calls += 1; return HcxGeneratedAnswer("하이브가 더 높습니다.", ("ev-1", "ev-2"))

        router = FakeEvaluationService().router
        registry = Registry(); client = Client()
        result = HcxFunctionCallingService(registry, client, router=router).answer(
            "에스엠엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?"
        )
        self.assertEqual(result.status, "answered")
        self.assertEqual(result.tool_response["data"]["comparison"]["winner"], "하이브")
        self.assertEqual([call[1]["company"] for call in registry.calls], ["에스엠", "하이브"])
        self.assertEqual(client.final_calls, 1)

        blocked_client = Client()
        blocked = HcxFunctionCallingService(Registry(missing_second=True), blocked_client, router=router).answer(
            "에스엠엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?"
        )
        self.assertEqual(blocked.status, "abstained")
        self.assertIsNone(blocked.tool_response["data"]["comparison"]["winner"])
        self.assertEqual(blocked_client.final_calls, 0)

    def test_jsonl_store_is_structural_and_result_failure_is_classified(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = QaCaseStore(root / "qa_cases.jsonl", root / "qa_results")
            case = validate_case({
                "id": "financial_compare_001",
                "question": "에스엠엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?",
                "category": "financial_comparison",
                "expected": {
                    "companies": ["에스엠", "하이브"],
                    "period": "2025",
                    "metric": "매출액",
                    "workflow": "financial_comparison",
                    "executor_tools": ["get_financial_facts", "get_financial_facts"],
                    "comparison_complete": True,
                    "backend_display_value_required": True,
                    "citation_required": True,
                    "dense_max_calls": 0,
                    "function_calling_max_calls": 0,
                },
                "forbidden_phrases": [],
            })
            store.save_cases([case])

            run = QaEvaluator(FakeEvaluationService(), store).run(store.list_cases())

            self.assertEqual((run["total"], run["pass"], run["fail"]), (1, 1, 0))
            self.assertEqual(run["results"][0]["trace"]["calls"], {
                "dense": 0, "function_calling": 0, "hcx_final": 1,
            })
            saved = json.loads((root / "qa_results" / f"{run['run_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["results"][0]["status"], "PASS")

    def test_resolver_mismatch_is_classified_before_downstream_layers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = QaCaseStore(root / "qa_cases.jsonl", root / "qa_results")
            case = {
                "id": "resolver_failure_001",
                "question": "SM엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?",
                "category": "financial_comparison",
                "expected": {"companies": ["잘못된기대", "하이브"]},
                "forbidden_phrases": [],
            }

            run = QaEvaluator(FakeEvaluationService(), store).run([case])

            failure = run["results"][0]
            self.assertEqual(failure["failure_layer"], "RESOLVER")
            self.assertEqual(failure["short_reason"], "resolver_companies_mismatch")

    def test_runtime_exception_is_a_sanitized_infra_failure(self) -> None:
        class FailingService(FakeEvaluationService):
            def answer(self, question: str) -> FunctionCallingResult:
                raise RuntimeError("database password and stack details")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = QaCaseStore(root / "qa_cases.jsonl", root / "qa_results")
            case = {
                "id": "infra_failure_001", "question": "실제 Agent 질문",
                "category": "search", "expected": {}, "forbidden_phrases": [],
            }
            run = QaEvaluator(FailingService(), store).run([case])

        failure = run["results"][0]
        self.assertEqual(failure["status"], "FAIL")
        self.assertEqual(failure["failure_layer"], "INFRA")
        self.assertEqual(failure["failure_reason"], "agent_execution_failed")
        self.assertNotIn("password", str(failure))

    def test_first_regression_jsonl_runs_as_four_structural_mock_cases(self) -> None:
        class RegressionService(FakeEvaluationService):
            def __init__(self) -> None:
                self.router = DeterministicQuestionRouter(["에스엠", "하이브", "삼성전자"])

            def answer(self, question: str) -> FunctionCallingResult:
                route = self.router.route(question)
                if route is not None and route.workflow == "financial_comparison":
                    return super().answer(question)
                return FunctionCallingResult(
                    status="answered", answer="검증된 재무제표 표시값입니다.",
                    tool_name="build_summary_context",
                    tool_response={
                        "status": "success", "tool_name": "build_summary_context",
                        "data": {"validated_statement_values": [{"display_value": "73억 3,200만 원"}]},
                        "evidence_bundle": {}, "warnings": [],
                        "metadata": {"executors": ["build_summary_context"], "sufficiency_check": {"status": "sufficient"}},
                    },
                    answer_allowed=True, recommended_action="answer", citation_ids=["ev-cell"],
                    citations=[{"evidence_id": "ev-cell", "rcept_no": "20260318000001"}],
                    metadata={"tool_selection_called": False, "final_generation_called": True},
                )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = QaCaseStore(Path("eval/qa_cases.jsonl"), root / "qa_results")
            run = QaEvaluator(RegressionService(), store).run(store.list_cases())

        self.assertEqual((run["total"], run["pass"], run["fail"]), (4, 4, 0))


if __name__ == "__main__":
    unittest.main()
