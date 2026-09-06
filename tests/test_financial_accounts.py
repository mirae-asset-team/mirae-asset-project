from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from disclosure_db.agent import DisclosureAgent
from disclosure_db.agent_contracts import EvidenceBundle, EvidenceRef
from disclosure_db.evidence_service import EvidenceService
from disclosure_db.financial_accounts import (
    FinancialAccountCatalog,
    load_financial_account_catalog,
    resolve_financial_account,
)
from disclosure_db.financial_extraction import canonical_account_id
from disclosure_db.generation import DeterministicGenerator
from disclosure_db.query_planner import plan_query


class FinancialAccountResolutionTests(unittest.TestCase):
    def test_only_validated_extraction_accounts_are_structured(self) -> None:
        catalog = load_financial_account_catalog()
        self.assertEqual(
            catalog.structured_account_ids(),
            (
                "revenue",
                "operating_income",
                "net_income",
                "total_assets",
                "total_liabilities",
                "total_equity",
            ),
        )
        self.assertEqual(
            catalog.structured_extraction_aliases()["total_equity"],
            frozenset({"자본총계"}),
        )

    def test_required_resolution_matrix(self) -> None:
        expected = {
            "매출액": ("resolved", "revenue", "structured"),
            "영업수익": ("resolved", "revenue", "structured"),
            "매출원가": ("resolved", "cost_of_sales", "retrieval_only"),
            "매출총이익": ("resolved", "gross_profit", "retrieval_only"),
            "매출이익": ("ambiguous", None, "ambiguous"),
            "영업이익": ("resolved", "operating_income", "structured"),
            "당기순이익": ("resolved", "net_income", "structured"),
            "유동자산": ("resolved", "current_assets", "retrieval_only"),
            "비유동자산": ("resolved", "noncurrent_assets", "retrieval_only"),
            "유동부채": ("resolved", "current_liabilities", "retrieval_only"),
            "영업활동현금흐름": (
                "resolved",
                "cash_flows_from_operating_activities",
                "retrieval_only",
            ),
            "부채비율": ("resolved", "debt_ratio", "derived"),
            "영업이익률": ("resolved", "operating_margin", "derived"),
            "등록되지않은재무계정": ("unknown", None, "unsupported"),
        }
        for expression, wanted in expected.items():
            with self.subTest(expression=expression):
                resolution = resolve_financial_account(expression)
                self.assertEqual(
                    (resolution.status, resolution.canonical_id, resolution.support_level),
                    wanted,
                )

    def test_longer_accounts_shadow_shorter_substrings(self) -> None:
        self.assertEqual(resolve_financial_account("매출총이익").canonical_id, "gross_profit")
        self.assertNotEqual(resolve_financial_account("매출총이익").canonical_id, "revenue")
        self.assertEqual(resolve_financial_account("유동자산").canonical_id, "current_assets")
        self.assertNotEqual(resolve_financial_account("유동자산").canonical_id, "total_assets")
        self.assertEqual(
            resolve_financial_account("영업활동현금흐름").canonical_id,
            "cash_flows_from_operating_activities",
        )

    def test_unicode_spacing_and_symbols_are_normalized(self) -> None:
        resolution = resolve_financial_account("영 업-이 익")
        self.assertEqual(resolution.canonical_id, "operating_income")

    def test_english_labels_resolve_like_aliases(self) -> None:
        self.assertEqual(resolve_financial_account("revenue는 얼마인가요").canonical_id, "revenue")
        self.assertEqual(
            resolve_financial_account("삼성전자의 FY2025 연결 revenue는 얼마인가요?").canonical_id,
            "revenue",
        )
        self.assertEqual(resolve_financial_account("Operating Income은?").canonical_id, "operating_income")
        self.assertEqual(
            resolve_financial_account("What was Samsung Electronics revenue in 2025?").canonical_id,
            "revenue",
        )
        self.assertEqual(
            resolve_financial_account("What was Samsung Electronics operating income in 2025?").canonical_id,
            "operating_income",
        )

    def test_colloquial_tails_do_not_hide_the_account(self) -> None:
        self.assertEqual(resolve_financial_account("매출액 좀 알려줄래").canonical_id, "revenue")
        self.assertEqual(resolve_financial_account("영업이익 궁금해").canonical_id, "operating_income")
        self.assertEqual(resolve_financial_account("당기순이익 어때?").canonical_id, "net_income")
        self.assertEqual(
            resolve_financial_account("삼성전자 2025년 연결 매출액 좀 알려줄래?").canonical_id,
            "revenue",
        )
        # Boundary guard must keep shadowing longer account words.
        self.assertEqual(resolve_financial_account("매출총이익 좀 알려줘").canonical_id, "gross_profit")

    def test_fuzzy_typo_is_never_inferred(self) -> None:
        self.assertEqual(resolve_financial_account("매출총익").status, "unknown")
        self.assertIsNone(canonical_account_id("매출총이익"))

    def test_only_registered_xbrl_and_typo_aliases_resolve(self) -> None:
        revenue = load_financial_account_catalog().by_id["revenue"]
        catalog = FinancialAccountCatalog(
            [replace(revenue, xbrl_concepts=("ifrs-full:Revenue",), typo_aliases=("매출앳",))],
            [],
            {},
        )
        self.assertEqual(catalog.resolve("ifrs-full:Revenue").match_type, "exact_xbrl_concept")
        self.assertEqual(catalog.resolve("매출앳").match_type, "typo_alias")
        self.assertEqual(catalog.resolve("매출엑").status, "unknown")

    def test_multiple_distinct_accounts_require_clarification(self) -> None:
        resolution = resolve_financial_account("매출액과 영업이익")
        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual(set(resolution.candidates), {"revenue", "operating_income"})

    def test_mixed_language_distinct_accounts_are_not_collapsed(self) -> None:
        first = resolve_financial_account("삼성전자 revenue와 영업이익을 알려줘")
        second = resolve_financial_account("삼성전자 매출액과 operating income을 알려줘")

        self.assertEqual(first.status, "ambiguous")
        self.assertEqual(set(first.candidates), {"revenue", "operating_income"})
        self.assertEqual(second.status, "ambiguous")
        self.assertEqual(set(second.candidates), {"revenue", "operating_income"})


class FinancialAccountRoutingTests(unittest.TestCase):
    def test_query_planner_routes_each_support_level(self) -> None:
        structured = plan_query("테스트회사 매출액은?", company_candidates=["테스트회사"])
        retrieval = plan_query("테스트회사 매출총이익은?", company_candidates=["테스트회사"])
        ambiguous = plan_query("테스트회사 매출이익은?", company_candidates=["테스트회사"])
        derived = plan_query("테스트회사 부채비율은?", company_candidates=["테스트회사"])

        self.assertEqual((structured.account_id, structured.fact_domain), ("revenue", "financial"))
        self.assertEqual(
            (retrieval.account_id, retrieval.fact_domain, retrieval.account_retrieval_route),
            ("gross_profit", "text", "evidence_search"),
        )
        self.assertEqual((ambiguous.account_id, ambiguous.fact_domain), (None, "none"))
        self.assertIn("financial_account_clarification_required", ambiguous.reason_codes)
        self.assertEqual(derived.account_id, "debt_ratio")
        self.assertEqual(derived.fact_domain, "financial_derived")
        self.assertEqual(derived.required_account_ids, ["total_liabilities", "total_equity"])

    def test_supported_growth_rate_routes_to_its_raw_source_account(self) -> None:
        plan = plan_query(
            "테스트회사 매출액 증가율은?",
            company_candidates=["테스트회사"],
        )
        self.assertEqual(plan.account_id, "revenue_growth_rate")
        self.assertEqual(plan.required_account_ids, ["revenue"])
        self.assertEqual(plan.operation, "growth_rate")
        self.assertEqual(plan.fact_domain, "financial")

    def test_evidence_service_uses_required_raw_account_for_supported_growth(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary) / "base.sqlite"
            overlay = Path(temporary) / "overlay.sqlite"
            base.touch()
            overlay.touch()
            service = EvidenceService(base, overlay, attestation=Mock())
            plan = plan_query(
                "테스트회사 매출액 증가율은?",
                company_candidates=["테스트회사"],
            )
            fact = {"financial_fact_id": "ff1", "evidence_ids": ["ev1"]}
            ref = EvidenceRef("ev1", "f1", "s1", "매출액 100")
            with patch.object(service, "_base_identity_valid", return_value=True), patch(
                "disclosure_db.evidence_service.overlay_matches_base", return_value=True,
            ), patch(
                "disclosure_db.evidence_service.fetch_overlay_facts", return_value=[fact],
            ) as fetch_facts, patch.object(
                service, "_hydrate_facts", return_value=([fact], [ref]),
            ):
                service.search(plan)

        self.assertEqual(fetch_facts.call_args.kwargs["account_id"], "revenue")
        self.assertNotEqual(fetch_facts.call_args.kwargs["account_id"], "revenue_growth_rate")

    def test_agent_stops_ambiguous_account_before_evidence_lookup(self) -> None:
        class Service:
            base_database = None
            overlay_database = None
            search_database = None
            attestation = None
            called = False

            def company_candidates(self):
                return ["테스트회사"]

            def search(self, plan, limit=20):
                self.called = True
                raise AssertionError("ambiguous accounts must not reach evidence lookup")

        service = Service()
        answer = DisclosureAgent(
            evidence_service=service,
            generator=DeterministicGenerator(),
        ).answer("테스트회사 매출이익은?")

        self.assertFalse(service.called)
        self.assertFalse(answer.answerable)
        self.assertIn("financial_account_clarification_required", answer.reason_codes)

    def test_agent_passes_retrieval_only_account_to_text_evidence_route(self) -> None:
        class Service:
            base_database = None
            overlay_database = None
            search_database = None
            attestation = None
            plan = None

            def company_candidates(self):
                return ["테스트회사"]

            def search(self, plan, limit=20):
                self.plan = plan
                return EvidenceBundle(
                    question=plan.question,
                    answerable=False,
                    reason_codes=list(plan.reason_codes),
                )

        service = Service()
        DisclosureAgent(
            evidence_service=service,
            generator=DeterministicGenerator(),
        ).answer("테스트회사 매출총이익은?")

        self.assertIsNotNone(service.plan)
        self.assertEqual(service.plan.fact_domain, "text")
        self.assertEqual(service.plan.account_id, "gross_profit")

    def test_agent_stops_explicit_unknown_financial_account_before_lookup(self) -> None:
        class Service:
            base_database = None
            overlay_database = None
            search_database = None
            attestation = None
            called = False

            def company_candidates(self):
                return ["테스트회사"]

            def search(self, plan, limit=20):
                self.called = True
                raise AssertionError("unknown financial accounts must not reach evidence lookup")

        service = Service()
        answer = DisclosureAgent(
            evidence_service=service,
            generator=DeterministicGenerator(),
        ).answer("테스트회사 미등록재무계정은 얼마인가?")

        self.assertFalse(service.called)
        self.assertFalse(answer.answerable)
        self.assertIn("financial_account_unknown", answer.reason_codes)


if __name__ == "__main__":
    unittest.main()


class KoreanParticleTests(unittest.TestCase):
    def test_particle_follows_the_final_sound_of_account_labels(self) -> None:
        from disclosure_db.financial_accounts import attach_particle

        cases = {
            ("자본총계", "은"): "자본총계는",
            ("부채총계", "은"): "부채총계는",
            ("매출액", "은"): "매출액은",
            ("영업이익", "은"): "영업이익은",
            ("부채비율", "이"): "부채비율이",
            ("영업이익률", "이"): "영업이익률이",
            ("삼성전자 2025년 연결 매출액 (주30)(연결)", "은"): "삼성전자 2025년 연결 매출액 (주30)(연결)은",
            ("333조 6,059억 3,800만 원", "으로"): "333조 6,059억 3,800만 원으로",
            ("2조 원", "으로"): "2조 원으로",
            ("25%", "은"): "25%는",
            ("ROE", "은"): "ROE는",
            ("EBITDA", "은"): "EBITDA는",
            ("2024", "은"): "2024는",
            ("2025년 3분기", "은"): "2025년 3분기는",
            ("2021", "으로"): "2021로",
            ("Q3", "으로"): "Q3으로",
        }
        for (word, particle), expected in cases.items():
            with self.subTest(word=word, particle=particle):
                self.assertEqual(attach_particle(word, particle), expected)

    def test_unreadable_ending_shows_both_forms_instead_of_guessing(self) -> None:
        from disclosure_db.financial_accounts import attach_particle

        self.assertEqual(attach_particle("…", "은"), "…은(는)")
        with self.assertRaises(ValueError):
            attach_particle("매출액", "에서")
