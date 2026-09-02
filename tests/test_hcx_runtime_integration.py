from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_db.api import create_app
from disclosure_db.hcx_function_calling import (
    HcxFunctionCallingService,
    HcxGeneratedAnswer,
    HcxToolCall,
    HyperClovaFunctionClient,
)
from disclosure_db.hybrid_retrieval import RetrievalResult
from disclosure_db.runtime import build_function_calling_service
from scripts.smoke_hcx_function_calling import summarize_payload


RCEPT_NO = "20250318000001"


class FakeHybrid:
    def __init__(
        self, *, filing_id: str = RCEPT_NO, include_evidence: bool = True,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.filing_id = filing_id
        self.include_evidence = include_evidence

    def search(self, question: str, **kwargs: object) -> RetrievalResult:
        self.calls.append((question, dict(kwargs)))
        hits = ({
            "evidence_id": "ev-runtime",
            "chunk_id": "chunk-runtime",
            "filing_id": self.filing_id,
            "company": "테스트회사",
            "issuer_corp_code": "00000001",
            "filed_at": "2025-03-18",
            "report_name": "사업보고서",
            "text_normalized": "테스트회사 사업 내용 근거",
            "lineage_status": "root",
            "is_current": 1,
            "matched_index": "unicode",
        },) if self.include_evidence else ()
        return RetrievalResult(hits, "unavailable", 0, True, "sparse_fallback", False)


class FakeClient:
    model = "fake-hcx"
    configured = True

    def __init__(self) -> None:
        self.generation_calls = 0

    def select_tool(self, question: str, tools: object) -> HcxToolCall:
        return HcxToolCall("call-runtime", "search_disclosures", {
            "question": question,
            "company": "테스트회사",
        })

    def generate_answer(self, question: str, tool_call: HcxToolCall, tool_response: object) -> HcxGeneratedAnswer:
        self.generation_calls += 1
        return HcxGeneratedAnswer("테스트회사의 공시 근거가 확인됩니다.", ("ev-runtime",))

    def generate_routed_answer(self, question: str, tool_call: HcxToolCall, tool_response: object) -> HcxGeneratedAnswer:
        return self.generate_answer(question, tool_call, tool_response)


class FakeEvidenceService:
    base_database = Path("unused.sqlite")


class FakeAgent:
    evidence_service = FakeEvidenceService()
    provider_configured = False


class HcxRuntimeIntegrationTests(unittest.TestCase):
    def _service(
        self,
        client: object | None = None,
        *,
        filing_id: str = RCEPT_NO,
        include_evidence: bool = True,
    ) -> tuple[HcxFunctionCallingService, FakeHybrid]:
        hybrid = FakeHybrid(filing_id=filing_id, include_evidence=include_evidence)
        service = build_function_calling_service(
            FakeAgent(),  # type: ignore[arg-type]
            client=client or FakeClient(),  # type: ignore[arg-type]
            hybrid_retriever=hybrid,  # type: ignore[arg-type]
        )
        return service, hybrid

    def test_runtime_composition_connects_hcx_registry_retrieval_and_receipt_gate(self) -> None:
        service, hybrid = self._service()

        result = service.answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.tool_name, "search_disclosures")
        self.assertEqual(result.citations[0]["rcept_no"], RCEPT_NO)
        self.assertIn(RCEPT_NO, result.answer)
        self.assertEqual(hybrid.calls[0][0], "테스트회사 사업 내용은?")
        self.assertEqual(len(service.available_tools()), 5)
        self.assertIsNotNone(service.analysis_executor)

    def test_no_credential_path_is_composed_without_network(self) -> None:
        service, hybrid = self._service(HyperClovaFunctionClient(env={}))

        result = service.answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "provider_unavailable")
        self.assertEqual(hybrid.calls, [])

    def test_deployment_smoke_summary_exposes_required_operational_fields(self) -> None:
        summary = summarize_payload("테스트 질문", {
            "status": "answered",
            "tool_name": "search_disclosures",
            "tool_response": {"evidence_bundle": {"sufficiency": "sufficient"}},
            "answer_allowed": True,
            "recommended_action": "answer",
            "citations": [{
                "evidence_id": "ev-1", "rcept_no": RCEPT_NO,
                "report_name": "사업보고서", "correction_role": None,
            }],
            "answer": "최종 답변",
            "latency_ms": 12.34,
            "warnings": [],
        })

        self.assertEqual(summary["query"], "테스트 질문")
        self.assertEqual(summary["selected_tool"], "search_disclosures")
        self.assertEqual(summary["evidence_status"], "sufficient")
        self.assertTrue(summary["answer_allowed"])
        self.assertEqual(summary["citations"][0]["rcept_no"], RCEPT_NO)  # type: ignore[index]
        self.assertEqual(summary["answer"], "최종 답변")
        self.assertEqual(summary["latency_ms"], 12.34)

    def test_runtime_opens_attested_search_path_and_reuses_agent_evidence_service(self) -> None:
        hybrid = FakeHybrid()

        class Attestation:
            sha256 = "a" * 64
            size_bytes = 123

        class ServingEvidenceService:
            base_database = Path("base.sqlite")
            search_database = Path("search.sqlite")
            attestation = Attestation()

        class ServingAgent:
            evidence_service = ServingEvidenceService()

        with patch("disclosure_db.runtime.SparseRetriever.open", return_value=object()) as open_sparse:
            with patch("disclosure_db.runtime.HybridRetriever", return_value=hybrid) as make_hybrid:
                service = build_function_calling_service(
                    ServingAgent(), client=FakeClient(),  # type: ignore[arg-type]
                )

        open_sparse.assert_called_once_with(
            Path("search.sqlite"), base_sha256="a" * 64, expected_base_size=123,
        )
        make_hybrid.assert_called_once_with(open_sparse.return_value, None)
        self.assertIs(service.registry._definitions["search_disclosures"].handler.__self__.evidence_service, ServingAgent.evidence_service)  # type: ignore[attr-defined]

    def test_separate_fastapi_endpoint_runs_fake_hcx_end_to_end(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:  # pragma: no cover - local optional dependency
            self.skipTest(str(exc))
        service, _ = self._service()
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as base:
            class ReadyEvidenceService:
                base_database = Path(base.name)
                overlay_database = None
                search_database = None
                attestation = None
                corpus_revision = "test-revision"

                @staticmethod
                def company_candidates() -> list[str]:
                    return ["테스트회사"]

            class ReadyAgent:
                evidence_service = ReadyEvidenceService()
                provider_configured = False

            with TestClient(create_app(
                ReadyAgent(),  # type: ignore[arg-type]
                function_calling_service=service,
            )) as api:
                response = api.post(
                    "/v1/hcx/function-answer",
                    json={"question": "테스트회사 사업 내용은?"},
                )
                health = api.get("/health").json()
                openapi = api.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["tool_name"], "search_disclosures")
        self.assertEqual(body["citations"][0]["rcept_no"], RCEPT_NO)
        self.assertIsInstance(body["latency_ms"], float)
        self.assertTrue(health["function_calling_configured"])
        self.assertEqual(openapi.status_code, 200)
        self.assertIn("/v1/hcx/function-answer", openapi.json()["paths"])

    def test_endpoint_blocks_insufficient_evidence_without_final_hcx_call(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:  # pragma: no cover - local optional dependency
            self.skipTest(str(exc))
        fake_client = FakeClient()
        service, _ = self._service(fake_client, include_evidence=False)
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as base:
            class ReadyService:
                base_database = Path(base.name)
                overlay_database = None
                search_database = None
                attestation = None
                corpus_revision = "test-revision"
                company_candidates = staticmethod(lambda: ["테스트회사"])

            agent = type("ReadyAgent", (), {
                "evidence_service": ReadyService(), "provider_configured": False,
            })()
            with TestClient(create_app(agent, function_calling_service=service)) as api:  # type: ignore[arg-type]
                body = api.post(
                    "/v1/hcx/function-answer", json={"question": "근거가 없는 질문"},
                ).json()

        self.assertEqual(body["status"], "abstained")
        self.assertEqual(body["tool_response"]["evidence_bundle"]["sufficiency"], "insufficient")
        self.assertFalse(body["answer_allowed"])
        self.assertEqual(fake_client.generation_calls, 0)

    def test_endpoint_blocks_missing_rcept_no_without_final_hcx_call(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:  # pragma: no cover - local optional dependency
            self.skipTest(str(exc))
        fake_client = FakeClient()
        service, _ = self._service(fake_client, filing_id="invalid-receipt")
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as base:
            class ReadyService:
                base_database = Path(base.name)
                overlay_database = None
                search_database = None
                attestation = None
                corpus_revision = "test-revision"
                company_candidates = staticmethod(lambda: ["테스트회사"])

            agent = type("ReadyAgent", (), {
                "evidence_service": ReadyService(), "provider_configured": False,
            })()
            with TestClient(create_app(agent, function_calling_service=service)) as api:  # type: ignore[arg-type]
                body = api.post(
                    "/v1/hcx/function-answer", json={"question": "접수번호가 없는 근거 질문"},
                ).json()

        self.assertEqual(body["status"], "abstained")
        self.assertFalse(body["answer_allowed"])
        self.assertIn("answer_generation_blocked_by_missing_rcept_no", body["warnings"])
        self.assertEqual(fake_client.generation_calls, 0)


if __name__ == "__main__":
    unittest.main()
