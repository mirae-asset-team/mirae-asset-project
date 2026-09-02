from __future__ import annotations

import unittest

from disclosure_db.question_routing import DeterministicQuestionRouter


class DeterministicQuestionRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = DeterministicQuestionRouter(["삼성전자", "SK하이닉스", "현대자동차", "미래에셋증권"])

    def test_structured_financial_lookup_skips_provider_selection(self) -> None:
        route = self.router.route("삼성전자 2025년 매출액은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "get_financial_facts")
        self.assertEqual(route.metric_kind, "DIRECT")
        self.assertEqual(route.arguments["company"], "삼성전자")
        self.assertEqual(route.arguments["start_date"], "2025-01-01")
        self.assertEqual(route.arguments["end_date"], "2025-12-31")

    def test_ambiguous_financial_term_returns_immediate_clarification(self) -> None:
        route = self.router.route("삼성전자 2025년 매출이익은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.kind, "clarification")
        self.assertIn("매출총이익", route.message)
        self.assertIn("영업이익", route.message)

    def test_retrieval_only_account_uses_summary_context_with_one_provider_call(self) -> None:
        route = self.router.route("삼성전자 2025년 매출총이익은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "build_summary_context")
        self.assertEqual(route.response_mode, "provider")
        self.assertEqual(route.arguments["account"], "매출총이익")
        self.assertEqual(route.arguments["fiscal_year"], 2025)
        self.assertEqual(route.arguments["period_kind"], "annual")

        quarter = self.router.route("삼성전자 2025년 1분기 매출총이익은?")
        self.assertIsNotNone(quarter)
        self.assertEqual(quarter.arguments["account"], "매출총이익")
        self.assertEqual(quarter.arguments["fiscal_year"], 2025)
        self.assertEqual(quarter.arguments["period_kind"], "quarter")
        self.assertEqual(quarter.arguments["quarter"], 1)

    def test_statement_cell_route_stays_narrow_and_preserves_safe_fallbacks(self) -> None:
        separate = self.router.route("삼성전자 2025년 별도 매출총이익은?")
        self.assertIsNotNone(separate)
        self.assertEqual(separate.arguments["scope"], "separate")

        fallback_questions = (
            "삼성전자 매출총이익은?",
            "삼성전자 2025년 3월 매출총이익은?",
            "삼성전자 2024년과 2025년 매출총이익 차이는?",
            "삼성전자 2025년 2분기 매출총이익은?",
            "삼성전자 2025년 4분기 매출총이익은?",
            "삼성전자 2025년 영업활동현금흐름은?",
            "삼성전자 2025년 유동자산은?",
            "삼성전자 2025년 기본주당이익은?",
            "삼성전자 2025년 최초 공시 매출총이익은?",
        )
        for question in fallback_questions:
            with self.subTest(question=question):
                route = self.router.route(question)
                self.assertIsNotNone(route)
                self.assertEqual(route.tool_name, "build_summary_context")
                self.assertNotIn("account", route.arguments)
                self.assertNotIn("fiscal_year", route.arguments)
                self.assertNotIn("period_kind", route.arguments)

    def test_explicit_trend_uses_aggregation_tool(self) -> None:
        route = self.router.route("삼성전자 2026년도 공시 트렌드를 알려줘")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "analyze_disclosure_trend")
        self.assertEqual(route.arguments["start_date"], "2026-01-01")
        self.assertEqual(route.arguments["end_date"], "2026-12-31")
        self.assertEqual(route.arguments["granularity"], "month")
        self.assertEqual(route.workflow, "trend_with_content")

        typo_route = self.router.route("삼성전자 2026년도 공시트랜드를 알려줘")
        self.assertIsNotNone(typo_route)
        self.assertEqual(typo_route.tool_name, "analyze_disclosure_trend")

    def test_receipt_correction_question_uses_lineage_tool(self) -> None:
        route = self.router.route("20250102000001 정정공시에서 무엇이 바뀌었어?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "get_correction_lineage")
        self.assertEqual(route.arguments, {"filing_id": "20250102000001"})

    def test_unclear_question_stays_with_function_calling(self) -> None:
        self.assertIsNone(self.router.route("요즘 상황 어때?"))

    def test_unique_company_typo_is_corrected_before_routing(self) -> None:
        route = self.router.route("삼선전자 2025년 매출액은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "get_financial_facts")
        self.assertEqual(route.arguments["company"], "삼성전자")
        self.assertEqual(route.normalized_question, "삼성전자 2025년 매출액은?")
        self.assertEqual(route.corrections, ("삼선전자->삼성전자",))

    def test_multi_account_each_request_routes_to_multi_metric_workflow(self) -> None:
        route = self.router.route("삼성전자의 2025년 연결 매출액과 영업이익을 각각 알려주세요.")
        self.assertIsNotNone(route)
        self.assertEqual(route.kind, "tool")
        self.assertEqual(route.tool_name, "get_financial_facts")
        self.assertEqual(route.workflow, "financial_comparison")
        requirements = route.context["requirements"]
        self.assertEqual(len(requirements), 2)
        self.assertEqual([item["account"] for item in requirements], ["매출액", "영업이익"])
        self.assertTrue(all(item["company"] == "삼성전자" for item in requirements))
        self.assertTrue(all(item["period"] == "2025" for item in requirements))
        self.assertEqual(route.context["intent"], "financial_multi_metric")

    def test_ambiguous_accounts_without_each_still_ask_clarification(self) -> None:
        route = self.router.route("삼성전자 2025년 매출액과 영업이익은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.kind, "clarification")

    def test_event_disclosure_questions_route_to_search_deterministically(self) -> None:
        cases = (
            "삼성전자의 가장 최근 주식 대량보유상황보고서에서 보고자와 보유비율을 알려주세요.",
            "삼성전자의 대량보유상황보고서에서 직전 보고 대비 지분율이 어떻게 변했나요?",
            "삼성전자의 유상증자 결정 공시에서 신주 발행 규모를 알려주세요.",
            "삼성전자의 자기주식 취득 결정에서 취득 예정 금액이 궁금합니다.",
            "삼성전자의 신규 시설투자 결정에서 투자금액을 알려주세요.",
        )
        for question in cases:
            with self.subTest(question=question):
                route = self.router.route(question)
                self.assertIsNotNone(route)
                self.assertEqual(route.tool_name, "search_disclosures")
                self.assertEqual(route.arguments["company"], "삼성전자")

    def test_correction_wording_still_wins_over_event_markers(self) -> None:
        route = self.router.route("삼성전자 유상증자 정정공시에서 변경 전 금액을 알려주세요.")
        self.assertIsNotNone(route)
        self.assertEqual(route.workflow, "correction_search_then_lineage")

    def test_spaced_company_name_is_collapsed_before_routing(self) -> None:
        route = self.router.route("삼 성 전 자의 2025년 연결 매출액은 얼마인가요?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "get_financial_facts")
        self.assertEqual(route.arguments["company"], "삼성전자")
        self.assertIn("spacing:삼 성 전 자->삼성전자", route.corrections)

    def test_partially_spaced_company_name_is_collapsed_before_routing(self) -> None:
        route = self.router.route("현대 자동차의 2025년 연결 매출액은 얼마인가요?")
        self.assertIsNotNone(route)
        self.assertEqual(route.arguments["company"], "현대자동차")
        self.assertIn("spacing:현대 자동차->현대자동차", route.corrections)

    def test_exact_company_name_records_no_spacing_correction(self) -> None:
        route = self.router.route("삼성전자 2025년 매출액은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.corrections, ())

    def test_exact_company_is_not_changed_to_one_edit_neighbor(self) -> None:
        router = DeterministicQuestionRouter(["삼성전자", "삼성전기", "현대자동차"])

        route = router.route("삼성전자 2025년 매출액은?")

        self.assertIsNotNone(route)
        self.assertEqual(route.arguments["company"], "삼성전자")
        self.assertIsNone(route.normalized_question)
        self.assertEqual(route.corrections, ())

    def test_unique_account_typo_is_corrected_to_safe_retrieval_route(self) -> None:
        route = self.router.route("삼성전자 2025년 매출총익은?")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "build_summary_context")
        self.assertIn("매출총익->매출총이익", route.corrections)
        self.assertEqual(route.arguments["question"], "삼성전자 2025년 매출총이익은?")

    def test_target_question_types_use_safe_deterministic_paths(self) -> None:
        cases = {
            "삼성전자 2024년 매출액은?": ("get_financial_facts", "single", "DIRECT"),
            "SK하이닉스 최근 3년 매출액은?": ("get_financial_facts", "single", "DIRECT"),
            "삼성전자 최근 3년 영업이익 증가율은?": ("get_financial_facts", "financial_derived", "DERIVED"),
            "삼성전자 2026년 공시 트렌드는?": ("analyze_disclosure_trend", "trend_with_content", "SEARCH"),
            "삼성전자 공급계약 금액은?": ("search_disclosures", "single", "SEARCH"),
            "삼성전자 최초 공시와 정정공시 차이는?": ("search_disclosures", "correction_search_then_lineage", "SEARCH"),
            "삼성전자 반도체 투자 공시 찾아줘": ("search_disclosures", "single", "SEARCH"),
            "삼성전자 최근 투자 관련 공시를 요약해줘": ("build_summary_context", "single", "SEARCH"),
            "삼성전자 2026년 매출액이 증가한 이유는?": ("get_financial_facts", "financial_change_reason", "DERIVED"),
            "삼성전자 2026년 사업보고서 요약해줘": ("build_summary_context", "document_summary_check", "SEARCH"),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                route = self.router.route(question)
                self.assertIsNotNone(route)
                self.assertEqual((route.tool_name, route.workflow, route.metric_kind), expected)

        ambiguous = self.router.route("삼성전자 26년 매출이익은?")
        self.assertIsNotNone(ambiguous)
        self.assertEqual((ambiguous.kind, ambiguous.metric_kind), ("clarification", "AMBIGUOUS"))

        typo = self.router.route("삼싱전자 2025년 매출액 알려조")
        self.assertIsNotNone(typo)
        self.assertEqual(typo.tool_name, "get_financial_facts")
        self.assertEqual(typo.arguments["company"], "삼성전자")

    def test_multi_period_financial_comparison_is_company_agnostic(self) -> None:
        cases = {
            "SK하이닉스 24년 대비 25년 매출 변화": ("SK하이닉스", ["2024", "2025"]),
            "삼성전자 2023년과 2024년 매출 비교": ("삼성전자", ["2023", "2024"]),
            "미래에셋증권 2024년 대비 2025년 매출 변화": ("미래에셋증권", ["2024", "2025"]),
            "삼성전자 2025년 매출은 2024년보다 늘었어?": ("삼성전자", ["2024", "2025"]),
            "SK하이닉스 2023, 2024, 2025 매출 추이": ("SK하이닉스", ["2023", "2024", "2025"]),
        }
        for question, (company, periods) in cases.items():
            with self.subTest(question=question):
                route = self.router.route(question)
                self.assertIsNotNone(route)
                self.assertEqual(route.workflow, "financial_comparison")
                self.assertEqual(route.tool_name, "get_financial_facts")
                self.assertEqual(route.context["companies"], [company])
                self.assertEqual(route.context["periods"], periods)
                self.assertEqual(route.context["metric_id"], "revenue")
                self.assertEqual(len(route.context["requirements"]), len(periods))
                self.assertEqual(route.arguments["start_date"], f"{periods[0]}-01-01")
                self.assertEqual(route.arguments["end_date"], f"{periods[0]}-12-31")

        single = self.router.route("SK하이닉스 2025년 매출액")
        self.assertIsNotNone(single)
        self.assertEqual(single.workflow, "single")
        self.assertEqual(single.arguments["end_date"], "2025-12-31")


if __name__ == "__main__":
    unittest.main()
