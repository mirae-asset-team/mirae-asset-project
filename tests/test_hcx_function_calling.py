from __future__ import annotations

import io
import json
import unittest
import urllib.error

from disclosure_db.disclosure_tools import build_tool_registry
from disclosure_db.hcx_function_calling import (
    FINAL_ANSWER_TOOL_NAME,
    FUNCTION_CALLING_MAX_TOKENS,
    HcxFunctionCallingError,
    HcxFunctionCallingService,
    HcxGeneratedAnswer,
    HcxProtocolError,
    HcxToolCall,
    HyperClovaFunctionClient,
)
from disclosure_db.hcx_prompts import HCX_FUNCTION_PROMPT_VERSION
from disclosure_db.hybrid_retrieval import RetrievalResult


def _search_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "evidence_id": "ev-1",
        "chunk_id": "chunk-1",
        "filing_id": "20240301000001",
        "report_name": "사업보고서",
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


class StaticHybrid:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    def search(self, question: str, **kwargs: object) -> RetrievalResult:
        self.calls.append((question, dict(kwargs)))
        return RetrievalResult(
            tuple(self.rows), "unavailable", 0, True, "sparse_fallback", False
        )


class FakeHcxClient:
    model = "fake-hcx"

    def __init__(
        self,
        tool_call: HcxToolCall,
        *,
        generated: HcxGeneratedAnswer | None = None,
        configured: bool = True,
        generation_error: Exception | None = None,
    ) -> None:
        self.tool_call = tool_call
        self.generated = generated or HcxGeneratedAnswer("근거 기반 답변입니다.", ("ev-1",))
        self.generation_error = generation_error
        self._configured = configured
        self.selection_calls: list[tuple[str, list[dict[str, object]]]] = []
        self.generation_calls: list[tuple[str, HcxToolCall, dict[str, object]]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    def select_tool(self, question: str, tools: object) -> HcxToolCall:
        self.selection_calls.append((question, list(tools)))  # type: ignore[arg-type]
        return self.tool_call

    def generate_answer(
        self, question: str, tool_call: HcxToolCall, tool_response: object,
    ) -> HcxGeneratedAnswer:
        self.generation_calls.append((question, tool_call, dict(tool_response)))  # type: ignore[arg-type]
        if self.generation_error is not None:
            raise self.generation_error
        return self.generated


class _HttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeUrlOpen:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[object, float, dict[str, object]]] = []

    def __call__(self, request: object, *, timeout: float) -> _HttpResponse:
        payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.requests.append((request, timeout, payload))
        return _HttpResponse(self.responses.pop(0))


class StaticRegistry:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def list_tools(self) -> list[dict[str, object]]:
        return []

    def dispatch(self, name: str, arguments: object) -> dict[str, object]:
        return self.response


class HcxFunctionCallingTests(unittest.TestCase):
    def _registry(self, rows: list[dict[str, object]] | None = None):
        hybrid = StaticHybrid(list(rows or []))
        return build_tool_registry(hybrid_retriever=hybrid), hybrid  # type: ignore[arg-type]

    @staticmethod
    def _search_call(**updates: object) -> HcxToolCall:
        arguments: dict[str, object] = {"question": "사업 내용", "company": "테스트회사"}
        arguments.update(updates)
        return HcxToolCall("call-1", "search_disclosures", arguments)

    def test_exposes_all_five_registry_schemas_without_duplication(self) -> None:
        registry, _ = self._registry()
        client = FakeHcxClient(self._search_call())
        schemas = HcxFunctionCallingService(registry, client).available_tools()
        registry_contracts = {item["name"]: item for item in registry.list_tools()}

        self.assertEqual(
            [item["function"]["name"] for item in schemas],
            [
                "analyze_disclosure_trend",
                "build_summary_context",
                "get_correction_lineage",
                "get_financial_facts",
                "search_disclosures",
            ],
        )
        for item in schemas:
            function = item["function"]
            self.assertEqual(
                function["parameters"], registry_contracts[function["name"]]["input_schema"]
            )

    def test_sparse_fallback_tool_call_generates_only_after_sufficient_evidence(self) -> None:
        registry, hybrid = self._registry([_search_row()])
        client = FakeHcxClient(self._search_call())
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "answered")
        self.assertTrue(result.answer_allowed)
        self.assertEqual(result.tool_name, "search_disclosures")
        self.assertEqual(result.citation_ids, ["ev-1"])
        self.assertEqual(result.citations[0]["rcept_no"], "20240301000001")
        self.assertIn("접수번호: 20240301000001", result.answer)
        self.assertEqual(result.metadata["prompt_version"], HCX_FUNCTION_PROMPT_VERSION)
        self.assertEqual(result.tool_response["data"]["retrieval_mode"], "sparse_fallback")
        self.assertEqual(len(client.selection_calls), 1)
        self.assertEqual(len(client.generation_calls), 1)
        self.assertEqual(len(hybrid.calls), 1)
        self.assertEqual(hybrid.calls[0][0], "테스트회사 사업 내용은?")

    def test_unknown_tool_and_invalid_arguments_are_blocked_without_final_generation(self) -> None:
        registry, hybrid = self._registry([_search_row()])
        cases = [
            (HcxToolCall("call-1", "not_registered", {}), "tool_not_registered"),
            (self._search_call(top_k=101), "value_too_large:top_k"),
        ]
        for call, warning in cases:
            with self.subTest(call=call):
                client = FakeHcxClient(call)
                result = HcxFunctionCallingService(registry, client).answer("질문")
                self.assertEqual(result.status, "invalid_request")
                self.assertFalse(result.answer_allowed)
                self.assertEqual(client.generation_calls, [])
                self.assertIn(warning, result.tool_response["warnings"])
        self.assertEqual(hybrid.calls, [])

    def test_insufficient_evidence_blocks_hcx_final_call_in_backend_control_flow(self) -> None:
        registry, hybrid = self._registry([])
        client = FakeHcxClient(self._search_call())
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "abstained")
        self.assertFalse(result.answer_allowed)
        self.assertEqual(result.recommended_action, "abstain")
        self.assertEqual(client.generation_calls, [])
        self.assertEqual(len(hybrid.calls), 1)
        self.assertFalse(result.metadata["final_generation_called"])

    def test_recommended_clarification_is_returned_without_final_generation(self) -> None:
        registry, _ = self._registry([])
        client = FakeHcxClient(HcxToolCall(
            "call-1", "get_financial_facts", {"company": "테스트회사", "account": "매출이익"}
        ))
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 매출이익은?")

        self.assertEqual(result.status, "abstained")
        self.assertEqual(result.recommended_action, "ask_clarification")
        self.assertIn("구체적으로", result.answer)
        self.assertEqual(client.generation_calls, [])

    def test_unknown_final_citation_fails_closed(self) -> None:
        registry, _ = self._registry([_search_row()])
        client = FakeHcxClient(
            self._search_call(), generated=HcxGeneratedAnswer("잘못된 인용", ("unknown",))
        )
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "error")
        self.assertFalse(result.answer_allowed)
        self.assertEqual(result.recommended_action, "abstain")
        self.assertEqual(result.citation_ids, [])
        self.assertEqual(len(client.generation_calls), 1)

    def test_missing_valid_rcept_no_blocks_final_generation_before_hcx_call(self) -> None:
        registry, _ = self._registry([_search_row(filing_id="not-a-dart-receipt")])
        client = FakeHcxClient(self._search_call())
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "abstained")
        self.assertFalse(result.answer_allowed)
        self.assertEqual(result.recommended_action, "abstain")
        self.assertEqual(client.generation_calls, [])
        self.assertIn("answer_generation_blocked_by_missing_rcept_no", result.warnings)

    def test_provider_cannot_render_or_invent_receipt_number_in_answer_body(self) -> None:
        registry, _ = self._registry([_search_row()])
        client = FakeHcxClient(
            self._search_call(),
            generated=HcxGeneratedAnswer("임의 접수번호 20240101999999", ("ev-1",)),
        )
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.citations, [])
        self.assertNotIn("20240101999999", result.answer)

    def test_fourteen_digit_financial_value_is_not_mistaken_for_receipt_number(self) -> None:
        registry, _ = self._registry([_search_row()])
        client = FakeHcxClient(
            self._search_call(),
            generated=HcxGeneratedAnswer("공시 수치는 12345678901234원입니다.", ("ev-1",)),
        )
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 공시 수치는?")

        self.assertEqual(result.status, "answered")
        self.assertIn("12345678901234원", result.answer)

    def test_correction_answer_lists_original_and_corrected_receipts_with_roles(self) -> None:
        original = "20240301000001"
        corrected = "20240315000002"
        response: dict[str, object] = {
            "status": "success",
            "tool_name": "get_correction_lineage",
            "data": {
                "original_filing": {"filing_id": original},
                "corrected_filings": [{"filing_id": corrected}],
                "current_filing": {"filing_id": corrected},
            },
            "evidence_bundle": {
                "evidence_ids": ["ev-original", "ev-corrected"],
                "items": [
                    {"evidence_id": "ev-original", "evidence_ids": ["ev-original"], "filing_id": original,
                     "rcept_no": original, "report_name": "사업보고서"},
                    {"evidence_id": "ev-corrected", "evidence_ids": ["ev-corrected"], "filing_id": corrected,
                     "rcept_no": corrected, "report_name": "정정 사업보고서"},
                ],
            },
            "warnings": [],
            "metadata": {"sufficiency_check": {
                "status": "sufficient", "recommended_action": "answer", "answer_allowed": True,
            }},
        }
        client = FakeHcxClient(
            HcxToolCall("call-1", "get_correction_lineage", {"filing_id": corrected}),
            generated=HcxGeneratedAnswer("정정 계보가 확인됩니다.", ("ev-corrected",)),
        )
        result = HcxFunctionCallingService(StaticRegistry(response), client).answer("정정 내역은?")  # type: ignore[arg-type]

        self.assertEqual(result.status, "answered")
        self.assertEqual([item["rcept_no"] for item in result.citations], [original, corrected])
        self.assertIn(f"최초공시 사업보고서 — 접수번호: {original}", result.answer)
        self.assertIn(f"정정공시 정정 사업보고서 — 접수번호: {corrected}", result.answer)

    def test_unconfigured_real_client_never_attempts_network_or_tool_execution(self) -> None:
        registry, hybrid = self._registry([_search_row()])

        def forbidden_urlopen(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("network must not be called")

        client = HyperClovaFunctionClient(env={}, urlopen=forbidden_urlopen)
        result = HcxFunctionCallingService(registry, client).answer("테스트회사 사업 내용은?")

        self.assertFalse(client.configured)
        self.assertEqual(result.status, "provider_unavailable")
        self.assertFalse(result.answer_allowed)
        self.assertEqual(hybrid.calls, [])

    def test_concrete_adapter_uses_documented_openai_compatible_tool_messages(self) -> None:
        opener = FakeUrlOpen([
            {
                "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {
                        "name": "search_disclosures",
                        "arguments": '{"question":"사업 내용"}',
                    },
                }]}}],
            },
            {
                "choices": [{"message": {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": "call-final", "type": "function",
                        "function": {
                            "name": FINAL_ANSWER_TOOL_NAME,
                            "arguments": '{"answer":"근거 기반 답변","citation_ids":["ev-1"]}',
                        },
                    }],
                }}],
            },
        ])
        client = HyperClovaFunctionClient(api_key="in-memory-test-key", urlopen=opener)
        registry, _ = self._registry()
        tools = HcxFunctionCallingService(registry, client).available_tools()

        call = client.select_tool("사업 내용은?", tools)
        generated = client.generate_answer(
            "사업 내용은?", call,
            {"evidence_bundle": {"evidence_ids": ["ev-1"]}, "data": {"results": []}},
        )

        self.assertEqual(call.name, "search_disclosures")
        self.assertEqual(generated.citation_ids, ("ev-1",))
        selection_payload = opener.requests[0][2]
        final_payload = opener.requests[1][2]
        self.assertEqual(selection_payload["tool_choice"], "auto")
        self.assertEqual(selection_payload["max_tokens"], FUNCTION_CALLING_MAX_TOKENS)
        self.assertEqual(len(selection_payload["tools"]), 5)
        self.assertIn("DART", selection_payload["messages"][0]["content"])
        self.assertIn("접수번호", final_payload["messages"][0]["content"])
        self.assertEqual(
            final_payload["tool_choice"],
            {"type": "function", "function": {"name": FINAL_ANSWER_TOOL_NAME}},
        )
        self.assertEqual(final_payload["tools"][0]["function"]["name"], FINAL_ANSWER_TOOL_NAME)
        citation_schema = final_payload["tools"][0]["function"]["parameters"]["properties"]["citation_ids"]
        self.assertEqual(citation_schema["items"]["enum"], ["ev-1"])
        self.assertEqual(
            [message["role"] for message in final_payload["messages"]],
            ["system", "user", "assistant", "tool"],
        )
        self.assertIsInstance(final_payload["messages"][-2]["tool_calls"][0]["function"]["arguments"], str)
        self.assertEqual(final_payload["messages"][-1]["role"], "tool")
        self.assertEqual(final_payload["messages"][-1]["tool_call_id"], "call-1")
        self.assertIsInstance(final_payload["messages"][-1]["content"], str)

    def test_plain_text_final_response_is_rejected_at_named_response_stage(self) -> None:
        opener = FakeUrlOpen([{
            "choices": [{"message": {
                "role": "assistant", "content": "JSON이 아닌 일반 최종 답변",
            }}],
        }])
        client = HyperClovaFunctionClient(api_key="in-memory-test-key", urlopen=opener)

        with self.assertRaises(HcxProtocolError) as raised:
            client.generate_answer(
                "사업 내용은?", self._search_call(),
                {"evidence_bundle": {"evidence_ids": ["ev-1"]}, "data": {"results": []}},
            )

        self.assertEqual(raised.exception.code, "hcx_requires_exactly_one_tool_call")
        self.assertEqual(raised.exception.stage, "final_generation_response")

    def test_final_failure_logs_only_structured_diagnostics_and_keeps_receipt_gate(self) -> None:
        registry, _ = self._registry([_search_row()])
        client = FakeHcxClient(
            self._search_call(),
            generation_error=HcxFunctionCallingError(
                "hcx_http_error", stage="final_generation_request",
                http_status=400, provider_error_code="40001",
            ),
        )

        with self.assertLogs("disclosure_db.hcx_function_calling", level="WARNING") as captured:
            result = HcxFunctionCallingService(registry, client).answer(
                "로그에 포함되면 안 되는 질문"
            )

        self.assertEqual(result.status, "error")
        self.assertFalse(result.answer_allowed)
        self.assertEqual(result.citations, [])
        self.assertEqual(result.metadata["available_citation_count"], 1)
        self.assertEqual(result.metadata["valid_rcept_no_count"], 1)
        self.assertEqual(result.metadata["error_stage"], "final_generation_request")
        self.assertEqual(result.metadata["error_code"], "hcx_http_error")
        self.assertEqual(result.metadata["http_status"], 400)
        self.assertEqual(result.metadata["provider_error_code"], "40001")
        log_output = "\n".join(captured.output)
        self.assertIn('"error_stage":"final_generation_request"', log_output)
        self.assertNotIn("로그에 포함되면 안 되는 질문", log_output)
        self.assertNotIn("테스트회사 공시 근거 문장", log_output)

    def test_http_error_keeps_only_safe_provider_code(self) -> None:
        def http_error_urlopen(*_args: object, **_kwargs: object) -> object:
            payload = json.dumps({
                "error": {"code": "40001", "message": "raw provider detail must not escape"},
            }).encode("utf-8")
            raise urllib.error.HTTPError(
                "https://provider.invalid", 400, "Bad Request", {}, io.BytesIO(payload)
            )

        client = HyperClovaFunctionClient(
            api_key="in-memory-test-key", urlopen=http_error_urlopen
        )
        with self.assertRaises(HcxFunctionCallingError) as raised:
            client.generate_answer(
                "사업 내용은?", self._search_call(),
                {"evidence_bundle": {"evidence_ids": ["ev-1"]}, "data": {"results": []}},
            )

        self.assertEqual(raised.exception.code, "hcx_http_error")
        self.assertEqual(raised.exception.stage, "final_generation_request")
        self.assertEqual(raised.exception.http_status, 400)
        self.assertEqual(raised.exception.provider_error_code, "40001")
        self.assertNotIn("raw provider detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
