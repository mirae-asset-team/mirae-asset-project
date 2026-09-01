from __future__ import annotations

import unittest

from disclosure_db.hcx_function_calling import (
    HcxFunctionCallingService, HcxGeneratedAnswer, HcxToolCall,
)
from disclosure_db.question_routing import DeterministicQuestionRouter


def response_for_item(item: dict[str, object]) -> dict[str, object]:
    return {
        "status": "success",
        "tool_name": "build_summary_context",
        "data": {"context": "원문", "result_count": 1},
        "evidence_bundle": {
            "question_intent": "build_summary_context", "requested_scope": {},
            "covered_scope": {"company": ["삼성전자"]}, "items": [item],
            "evidence_ids": [item["evidence_id"]], "filing_ids": [item["filing_id"]],
            "issuer_corp_codes": ["00126380"], "quality_warnings": [],
            "correction_status": "policy_applied", "retrieval_status": {"route": "test"},
            "sufficiency": "sufficient",
        },
        "warnings": [],
        "metadata": {"sufficiency_check": {
            "status": "sufficient", "answer_allowed": True, "recommended_action": "answer",
        }},
    }


class Registry:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[str] = []
    def list_tools(self): return []
    def dispatch(self, name, arguments): self.calls.append(name); return self.response


class Client:
    model = "fake"
    configured = True
    def __init__(self, evidence_id: str): self.evidence_id = evidence_id; self.final_calls = 0
    def select_tool(self, question, tools): raise AssertionError("function calling must be zero")
    def generate_answer(self, question, call, response): raise AssertionError("second function calling path forbidden")
    def generate_routed_answer(self, question, call, response):
        self.final_calls += 1
        return HcxGeneratedAnswer("백엔드 표시값 기준 답변", (self.evidence_id,))


def statement_item(*, report: str, start: str, end: str, unit: object = "KRW") -> dict[str, object]:
    return {
        "evidence_id": "ev-cell", "evidence_ids": ["ev-cell"],
        "filing_id": "20260318000001", "rcept_no": "20260318000001",
        "report_name": report, "filed_at": "2026-03-18",
        "period": {"period_start": start, "period_end": end},
        "table_id": "table-1", "locator": {"row_label": "매출총이익", "column_label": "당기"},
        "structured_value": "7332", "scale": 1_000_000, "unit": unit,
        "text": "매출총이익 7,332", "quality_status": "safe_search_admitted",
    }


class FinancialStatementGuardTests(unittest.TestCase):
    def service(self, item: dict[str, object]):
        registry = Registry(response_for_item(item)); client = Client("ev-cell")
        service = HcxFunctionCallingService(
            registry, client, router=DeterministicQuestionRouter(["삼성전자"]),
        )
        return service, registry, client

    def test_fy_uses_fiscal_period_not_filing_year_and_requires_unit(self) -> None:
        service, registry, client = self.service(statement_item(
            report="사업보고서", start="2025-01-01", end="2025-12-31",
        ))

        result = service.answer("2025년 삼성전자 매출총이익은 얼마야?")

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.tool_response["data"]["validated_statement_values"][0]["display_value"], "73억 3,200만 원")
        self.assertEqual(registry.calls, ["build_summary_context"])
        self.assertEqual(client.final_calls, 1)

        missing_unit_service, _, missing_unit_client = self.service(statement_item(
            report="사업보고서", start="2025-01-01", end="2025-12-31", unit=None,
        ))
        blocked = missing_unit_service.answer("2025년 삼성전자 매출총이익은 얼마야?")
        self.assertEqual(blocked.status, "abstained")
        self.assertEqual(missing_unit_client.final_calls, 0)

    def test_q1_is_allowed_but_not_as_fy(self) -> None:
        q1_item = statement_item(report="분기보고서", start="2025-01-01", end="2025-03-31")
        q1_service, _, _ = self.service(q1_item)
        annual_service, _, annual_client = self.service(q1_item)

        q1 = q1_service.answer("삼성전자 2025년 1분기 매출총이익은?")
        annual = annual_service.answer("2025년 삼성전자 매출총이익은 얼마야?")

        self.assertEqual(q1.status, "answered")
        self.assertEqual(annual.status, "abstained")
        self.assertEqual(annual_client.final_calls, 0)


if __name__ == "__main__":
    unittest.main()
