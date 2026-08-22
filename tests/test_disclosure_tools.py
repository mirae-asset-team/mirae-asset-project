from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from disclosure_db.agent_contracts import EvidenceBundle, EvidenceRef
from disclosure_db.disclosure_tools import build_tool_registry
from disclosure_db.hybrid_retrieval import RetrievalResult


def _search_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "evidence_id": "ev-1",
        "chunk_id": "chunk-1",
        "filing_id": "f-1",
        "company": "테스트회사",
        "issuer_corp_code": "00000001",
        "filed_at": "2024-03-01",
        "locator_json": '{"section":"사업의 내용"}',
        "text_normalized": "테스트회사 공시 근거 문장",
        "lineage_status": "root",
        "is_current": 1,
        "matched_index": "unicode",
        "score": -0.2,
    }
    row.update(updates)
    return row


class FakeHybrid:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def search(self, question: str, **kwargs: object) -> RetrievalResult:
        self.calls.append((question, dict(kwargs)))
        return self.result


class FakeEvidenceService:
    def __init__(self, bundle: EvidenceBundle) -> None:
        self.bundle = bundle
        self.calls: list[tuple[object, int]] = []

    def search(self, plan: object, *, limit: int = 20) -> EvidenceBundle:
        self.calls.append((plan, limit))
        return self.bundle


def _result(
    rows: list[dict[str, object]],
    *,
    dense_status: str = "full_corpus",
    dense_corpus_size: int = 1000,
    fallback_used: bool = False,
    retrieval_mode: str = "hybrid_rrf",
    smoke_only: bool = False,
) -> RetrievalResult:
    return RetrievalResult(
        tuple(rows), dense_status, dense_corpus_size, fallback_used, retrieval_mode, smoke_only
    )


def _create_metadata_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE filing(
            filing_id TEXT PRIMARY KEY,
            issuer_corp_code TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            issuer_name TEXT NOT NULL,
            listed_name TEXT NOT NULL,
            reporter_name TEXT NOT NULL,
            doc_group TEXT NOT NULL,
            doc_subtype_normalized TEXT,
            report_name_raw TEXT NOT NULL,
            filed_at TEXT NOT NULL,
            is_correction INTEGER NOT NULL
        );
        CREATE TABLE filing_version(
            filing_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            parent_filing_id TEXT,
            lineage_status TEXT NOT NULL,
            lineage_confidence TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            is_current INTEGER NOT NULL,
            rationale TEXT NOT NULL
        );
        CREATE TABLE fragment(
            evidence_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            locator_json TEXT NOT NULL
        );
        """
    )
    filings = [
        ("f-1", "00000001", "000001", "테스트회사", "테스트회사", "테스트회사", "periodic", "annual", "사업보고서", "2024-01-01", 0),
        ("f-2", "00000001", "000001", "테스트회사", "테스트회사", "테스트회사", "periodic", "annual", "정정 사업보고서", "2024-01-15", 1),
        ("f-3", "00000001", "000001", "테스트회사", "테스트회사", "테스트회사", "major", "contract", "주요사항보고서", "2024-02-01", 0),
    ]
    connection.executemany("INSERT INTO filing VALUES(?,?,?,?,?,?,?,?,?,?,?)", filings)
    versions = [
        ("f-1", "event-1", 1, None, "root", "high", "2024-01-01", "2024-01-15", 0, "original"),
        ("f-2", "event-1", 2, "f-1", "unresolved", "none", "2024-01-15", None, 1, "uncertain match"),
        ("f-3", "event-2", 1, None, "root", "high", "2024-02-01", None, 1, "original"),
    ]
    connection.executemany("INSERT INTO filing_version VALUES(?,?,?,?,?,?,?,?,?,?)", versions)
    connection.executemany(
        "INSERT INTO fragment VALUES(?,?,?,?)",
        [("ev-1", "f-1", 1, '{"section":"title"}'),
         ("ev-2", "f-2", 1, '{"section":"title"}'),
         ("ev-3", "f-3", 1, '{"section":"title"}')],
    )
    connection.commit()
    connection.close()


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hybrid = FakeHybrid(_result([_search_row()]))
        self.registry = build_tool_registry(hybrid_retriever=self.hybrid)  # type: ignore[arg-type]

    def test_registry_lists_fixed_public_contracts(self) -> None:
        contracts = self.registry.list_tools()
        self.assertEqual(
            [item["name"] for item in contracts],
            [
                "analyze_disclosure_trend",
                "build_summary_context",
                "get_correction_lineage",
                "get_financial_facts",
                "search_disclosures",
            ],
        )
        search = next(item for item in contracts if item["name"] == "search_disclosures")
        self.assertIn("input_schema", search)
        self.assertIn("output_schema", search)
        self.assertNotIn("handler", search)
        output_data = search["output_schema"]["properties"]["data"]
        self.assertEqual(
            set(output_data["required"]),
            {"results", "dense_status", "dense_corpus_size", "fallback_used", "retrieval_mode"},
        )

    def test_dispatch_blocks_unknown_invalid_and_excessive_input_before_execution(self) -> None:
        cases = [
            ("not_registered", {}),
            ("search_disclosures", {}),
            ("search_disclosures", {"question": "q", "top_k": 101}),
            ("search_disclosures", {"question": "q", "top_k": "10"}),
            ("search_disclosures", {"question": "q", "start_date": "2024-02-01", "end_date": "2024-01-01"}),
            ("search_disclosures", {"question": "q", "sql": "SELECT * FROM filing"}),
        ]
        for name, payload in cases:
            with self.subTest(name=name, payload=payload):
                response = self.registry.dispatch(name, payload)
                self.assertEqual(response["status"], "invalid_request")
        self.assertEqual(self.hybrid.calls, [])

    def test_search_dispatch_preserves_fallback_filters_evidence_and_common_shape(self) -> None:
        self.hybrid.result = _result(
            [_search_row()], dense_status="unavailable", dense_corpus_size=0,
            fallback_used=True, retrieval_mode="sparse_fallback",
        )
        response = self.registry.dispatch("search_disclosures", {
            "question": "사업 내용", "company": "테스트회사",
            "start_date": "2024-01-01", "end_date": "2024-12-31", "top_k": 5,
        })
        self.assertEqual(response["status"], "success")
        self.assertEqual(set(response), {"status", "tool_name", "data", "evidence_bundle", "warnings", "metadata"})
        self.assertTrue(response["data"]["fallback_used"])
        self.assertEqual(response["data"]["retrieval_mode"], "sparse_fallback")
        self.assertEqual(response["evidence_bundle"]["evidence_ids"], ["ev-1"])
        self.assertEqual(response["evidence_bundle"]["items"][0]["chunk_id"], "chunk-1")
        self.assertEqual(self.hybrid.calls[0][1]["start_date"], "2024-01-01")
        self.assertEqual(self.hybrid.calls[0][1]["end_date"], "2024-12-31")

    def test_dense_smoke_only_is_partial_and_warned(self) -> None:
        self.hybrid.result = _result(
            [_search_row()], dense_status="smoke_only", dense_corpus_size=100,
            smoke_only=True,
        )
        response = self.registry.dispatch("search_disclosures", {"question": "사업 내용"})
        self.assertEqual(response["status"], "partial")
        self.assertIn("dense_smoke_only", response["warnings"])
        self.assertEqual(response["evidence_bundle"]["sufficiency"], "partial")
        self.assertTrue(response["metadata"]["sufficiency_check"]["answer_allowed"])

    def test_company_and_period_mismatch_is_insufficient(self) -> None:
        self.hybrid.result = _result([_search_row(company="다른회사", filed_at="2023-12-31")])
        response = self.registry.dispatch("search_disclosures", {
            "question": "사업 내용", "company": "테스트회사",
            "start_date": "2024-01-01", "end_date": "2024-12-31",
        })
        self.assertEqual(response["status"], "insufficient")
        missing = response["metadata"]["sufficiency_check"]["missing_requirements"]
        self.assertIn("scope_mismatch:company", missing)
        self.assertIn("scope_mismatch:start_date", missing)


class FinancialToolTests(unittest.TestCase):
    def _registry(self, facts: list[dict[str, object]]) -> tuple[object, FakeEvidenceService]:
        refs = [
            EvidenceRef(
                "ev-fin", "f-fin", "source-fin", "매출액 100 원",
                {"table_id": "table-1", "row": 1}, "resolved", 1.0,
            )
        ]
        service = FakeEvidenceService(EvidenceBundle(
            question="매출액", evidence=refs, answerable=bool(facts), financial_facts=facts
        ))
        registry = build_tool_registry(
            hybrid_retriever=FakeHybrid(_result([])), evidence_service=service  # type: ignore[arg-type]
        )
        return registry, service

    def test_structured_validated_fact_succeeds_with_evidence(self) -> None:
        facts = [{
            "financial_fact_id": "ff-1", "filing_id": "f-fin", "account_id": "revenue",
            "account_name_raw": "매출액", "value_numeric": "100", "scale": 1,
            "currency": "KRW", "unit_raw": "원", "scope": "consolidated",
            "period_type": "duration", "period_start": "2024-01-01", "period_end": "2024-12-31",
            "instant_date": None, "validation_status": "validated", "evidence_ids": ["ev-fin"],
            "issuer_name": "테스트회사", "issuer_corp_code": "00000001",
        }]
        registry, service = self._registry(facts)
        response = registry.dispatch("get_financial_facts", {
            "company": "테스트회사", "account": "매출액",
            "start_date": "2024-01-01", "end_date": "2024-12-31",
            "scope": "consolidated",
        })
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["data"]["facts"][0]["unit"], "KRW")
        self.assertEqual(response["data"]["facts"][0]["support_level"], "structured")
        self.assertEqual(response["evidence_bundle"]["evidence_ids"], ["ev-fin"])
        self.assertEqual(len(service.calls), 1)

    def test_non_structured_accounts_are_blocked_before_evidence_service(self) -> None:
        registry, service = self._registry([])
        for account, warning in (
            ("매출이익", "financial_account_clarification_required"),
            ("매출총이익", "financial_account_retrieval_only_not_structured"),
            ("주당순자산", "financial_account_unsupported"),
            ("영업이익률", "derived_metric_not_implemented"),
        ):
            with self.subTest(account=account):
                response = registry.dispatch("get_financial_facts", {
                    "company": "테스트회사", "account": account,
                })
                self.assertEqual(response["status"], "insufficient")
                self.assertIn(warning, response["warnings"])
                self.assertEqual(response["data"]["facts"], [])
        self.assertEqual(service.calls, [])

    def test_structured_fact_grain_mismatch_fails_closed(self) -> None:
        facts = [{
            "financial_fact_id": "ff-1", "filing_id": "f-fin", "account_id": "revenue",
            "value_numeric": "100", "scale": 1, "currency": "KRW", "scope": "separate",
            "period_type": "duration", "period_start": "2024-01-01", "period_end": "2024-12-31",
            "validation_status": "validated", "evidence_ids": ["ev-fin"], "issuer_name": "테스트회사",
        }]
        registry, _ = self._registry(facts)
        response = registry.dispatch("get_financial_facts", {
            "company": "테스트회사", "account": "매출액", "scope": "consolidated",
        })
        self.assertEqual(response["status"], "insufficient")
        self.assertEqual(response["data"]["facts"], [])
        self.assertIn("financial_fact_grain_mismatch_or_missing", response["warnings"])


class MetadataToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "fixture.sqlite3"
        _create_metadata_database(self.database)
        self.hybrid = FakeHybrid(_result([_search_row(text_normalized="가" * 400)]))
        self.registry = build_tool_registry(
            hybrid_retriever=self.hybrid, base_database=self.database  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_trend_aggregation_and_partial_period(self) -> None:
        complete = self.registry.dispatch("analyze_disclosure_trend", {
            "start_date": "2024-01-01", "end_date": "2024-02-01",
            "company": "테스트회사", "granularity": "month",
        })
        self.assertEqual(complete["status"], "success")
        self.assertEqual(complete["data"]["total_count"], 3)
        self.assertEqual(complete["data"]["count_by_period"], [
            {"period": "2024-01", "count": 2}, {"period": "2024-02", "count": 1}
        ])
        self.assertEqual(complete["evidence_bundle"]["evidence_ids"], ["ev-1", "ev-2", "ev-3"])

        partial = self.registry.dispatch("analyze_disclosure_trend", {
            "start_date": "2023-01-01", "end_date": "2024-12-31",
            "company": "테스트회사", "granularity": "month",
        })
        self.assertEqual(partial["status"], "partial")
        self.assertFalse(partial["data"]["coverage_complete"])
        self.assertIn("complete_requested_period", partial["metadata"]["sufficiency_check"]["missing_requirements"])

    def test_uncertain_correction_lineage_is_partial(self) -> None:
        response = self.registry.dispatch("get_correction_lineage", {"filing_id": "f-2"})
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["data"]["original_filing"]["filing_id"], "f-1")
        self.assertEqual(response["data"]["current_filing"]["filing_id"], "f-2")
        self.assertEqual(response["data"]["connection_status"], "uncertain")
        self.assertIn("correction_lineage_uncertain", response["warnings"])
        self.assertEqual(response["evidence_bundle"]["evidence_ids"], ["ev-1", "ev-2"])

    def test_summary_context_is_deterministically_bounded(self) -> None:
        response = self.registry.dispatch("build_summary_context", {
            "question": "요약 근거", "company": "테스트회사", "max_chars": 100,
        })
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["data"]["context_chars"], 100)
        self.assertEqual(len(response["data"]["context"]), 100)
        self.assertTrue(response["data"]["context"].startswith("[f-1|ev-1] "))


if __name__ == "__main__":
    unittest.main()
