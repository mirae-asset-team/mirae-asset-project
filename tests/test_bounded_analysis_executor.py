from __future__ import annotations

from disclosure_db.agent_contracts import EvidenceRef
from disclosure_db.bounded_analysis import BoundedAnalysisExecutor
from disclosure_db.freeform_retrieval import AnalysisRetrieval, SlotRetrieval
from disclosure_db.hcx_function_calling import (
    HcxFunctionCallingService,
    HcxGeneratedAnswer,
)


def _evidence(*, issuer: str = "삼성전자", filed_at: str = "2024-03-01") -> EvidenceRef:
    return EvidenceRef(
        evidence_id="ev-risk",
        filing_id="20240301000001",
        source_id="source-risk",
        text="사업보고서에 원재료 가격 변동 위험이 기재되어 있습니다.",
        locator={"analysis_issuer": issuer, "section": "위험요인"},
        lineage_status="root",
        filed_at=filed_at,
        report_name="사업보고서",
        is_current=True,
    )


class StubEvidenceService:
    def __init__(self, retrieval: AnalysisRetrieval) -> None:
        self.retrieval = retrieval
        self.plans = []

    def company_candidates(self) -> list[str]:
        return ["삼성전자", "SK하이닉스"]

    def search_analysis(self, plan, *, limit: int = 20) -> AnalysisRetrieval:
        self.plans.append((plan, limit))
        return self.retrieval


def _complete_retrieval(ref: EvidenceRef) -> AnalysisRetrieval:
    return AnalysisRetrieval(
        evidence=(ref,),
        slots=(SlotRetrieval("disclosed_risk_factors", (ref,), True),),
        complete=True,
    )


def test_executor_builds_existing_tool_response_from_owned_mandatory_slot() -> None:
    evidence = _evidence()
    source = StubEvidenceService(_complete_retrieval(evidence))

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 공시의 사업위험을 분석해줘"
    )

    assert execution is not None
    assert execution.complete is True
    assert execution.conclusion is None
    assert execution.plan.judgment_dimension == "business_risk"
    assert len(source.plans) == 1
    response = execution.to_tool_response()
    assert response["status"] == "success"
    assert response["tool_name"] == "build_summary_context"
    assert response["data"]["execution_mode"] == "bounded_analysis"
    assert response["data"]["analysis_dimension"] == "business_risk"
    assert response["data"]["allowed_conclusions"] == [
        "risk_signal", "stable", "mixed", "insufficient_evidence"
    ]
    assert response["data"]["evidence_slots"] == [{
        "slot_id": "disclosed_risk_factors",
        "domain": "text",
        "issuer": "삼성전자",
        "period_start": None,
        "period_end": None,
        "instant_date": None,
        "mandatory": True,
        "complete": True,
        "evidence_ids": ["ev-risk"],
        "reason_codes": [],
    }]
    assert response["evidence_bundle"]["evidence_ids"] == ["ev-risk"]


def test_any_missing_mandatory_slot_forces_insufficient_evidence_without_search_result_claim() -> None:
    source = StubEvidenceService(AnalysisRetrieval(
        slots=(SlotRetrieval(
            "disclosed_risk_factors", (), False,
            ("required_slot_missing:disclosed_risk_factors",),
        ),),
        complete=False,
        reason_codes=("required_slot_missing:disclosed_risk_factors",),
    ))

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 공시의 사업위험을 분석해줘"
    )

    assert execution is not None
    assert execution.complete is False
    assert execution.conclusion == "insufficient_evidence"
    assert "required_slot_missing:disclosed_risk_factors" in execution.reason_codes
    response = execution.to_tool_response()
    assert response["status"] == "insufficient"
    assert response["data"]["conclusion"] == "insufficient_evidence"
    assert response["metadata"]["sufficiency_check"]["answer_allowed"] is False


def test_slot_marked_complete_is_rejected_when_evidence_belongs_to_another_issuer() -> None:
    evidence = _evidence(issuer="SK하이닉스")
    source = StubEvidenceService(AnalysisRetrieval(
        evidence=(evidence,),
        slots=(SlotRetrieval("disclosed_risk_factors", (evidence,), True),),
        complete=True,
        financial_facts=({
            "financial_fact_id": "wrong-owner-fact",
            "evidence_ids": ["ev-risk"],
            "period_end": "2024-12-31",
        },),
    ))

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 공시의 사업위험을 분석해줘"
    )

    assert execution is not None
    assert execution.complete is False
    assert execution.conclusion == "insufficient_evidence"
    assert execution.reason_codes == (
        "evidence_slot_issuer_mismatch:disclosed_risk_factors",
    )
    assert execution.to_tool_response()["data"]["financial_facts"] == []


def test_explicit_period_slot_is_rejected_when_text_evidence_is_outside_owned_period() -> None:
    evidence = _evidence(filed_at="2025-03-01")
    source = StubEvidenceService(_complete_retrieval(evidence))

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 2024년 공시의 사업위험을 분석해줘"
    )

    assert execution is not None
    assert execution.plan.required_evidence_slots[0].period_start == "2024-01-01"
    assert execution.complete is False
    assert execution.reason_codes == (
        "evidence_slot_period_mismatch:disclosed_risk_factors",
    )


def test_owner_and_period_filters_both_apply_before_any_evidence_is_admitted() -> None:
    wrong_issuer = _evidence(issuer="SK하이닉스", filed_at="2024-03-01")
    wrong_period = EvidenceRef(
        evidence_id="ev-wrong-period",
        filing_id="20250301000001",
        source_id="source-wrong-period",
        text="2025년 사업보고서의 위험요인입니다.",
        locator={"analysis_issuer": "삼성전자", "section": "위험요인"},
        lineage_status="root",
        filed_at="2025-03-01",
        report_name="사업보고서",
        is_current=True,
    )
    source = StubEvidenceService(AnalysisRetrieval(
        evidence=(wrong_issuer, wrong_period),
        slots=(SlotRetrieval(
            "disclosed_risk_factors", (wrong_issuer, wrong_period), True,
        ),),
        complete=True,
    ))

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 2024년 공시의 사업위험을 분석해줘"
    )

    assert execution is not None
    assert execution.complete is False
    assert execution.evidence == ()
    assert execution.to_tool_response()["evidence_bundle"]["evidence_ids"] == []
    assert execution.reason_codes == (
        "evidence_slot_issuer_mismatch:disclosed_risk_factors",
        "evidence_slot_period_mismatch:disclosed_risk_factors",
    )


def test_prohibited_forecast_never_calls_evidence_search() -> None:
    source = StubEvidenceService(AnalysisRetrieval())

    execution = BoundedAnalysisExecutor(source).execute(
        "삼성전자 공시를 바탕으로 내년 매출을 전망해줘"
    )

    assert execution is not None
    assert execution.plan.analysis_mode == "prohibited"
    assert execution.reason_codes == ("policy_forecast_refusal",)
    assert source.plans == []


def test_multi_period_judgment_fails_closed_when_one_owned_period_slot_is_absent() -> None:
    class PartialPeriodService(StubEvidenceService):
        def __init__(self) -> None:
            super().__init__(AnalysisRetrieval())

        def search_analysis(self, plan, *, limit: int = 20) -> AnalysisRetrieval:
            self.plans.append((plan, limit))
            first = plan.required_evidence_slots[0]
            evidence = _evidence(filed_at="2023-03-01")
            evidence.locator["period_end"] = "2023-12-31"
            return AnalysisRetrieval(
                evidence=(evidence,),
                slots=(SlotRetrieval(first.slot_id, (evidence,), True),),
                complete=False,
            )

    execution = BoundedAnalysisExecutor(PartialPeriodService()).execute(
        "삼성전자 2023년과 2024년 수익성을 공시로 비교 분석해줘"
    )

    assert execution is not None
    assert execution.complete is False
    assert execution.conclusion == "insufficient_evidence"
    assert len(execution.evidence_slots) == 2
    assert execution.evidence_slots[0]["period_end"] == "2023-12-31"
    assert execution.evidence_slots[1]["period_end"] == "2024-12-31"
    assert any(code.startswith("required_slot_missing:") for code in execution.reason_codes)


class FiveToolRegistry:
    names = (
        "analyze_disclosure_trend",
        "build_summary_context",
        "get_correction_lineage",
        "get_financial_facts",
        "search_disclosures",
    )

    def __init__(self) -> None:
        self.calls = []

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "description": name,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object"},
            }
            for name in self.names
        ]

    def dispatch(self, name: str, arguments: object) -> dict[str, object]:
        self.calls.append((name, arguments))
        raise AssertionError("bounded analysis must call EvidenceService.search_analysis directly")


class JudgmentClient:
    model = "fake-hcx"
    configured = True

    def __init__(self, *, conclusion: str = "risk_signal", answer: str | None = None) -> None:
        self.conclusion = conclusion
        self.answer = answer or "공시된 과거 위험요인에 근거해 위험 신호로 판단합니다."
        self.selection_calls = []
        self.generation_calls = []

    def select_tool(self, question, tools):
        self.selection_calls.append((question, tools))
        raise AssertionError("bounded analysis must not ask HCX to select a public Tool")

    def generate_answer(self, question, tool_call, tool_response):
        self.generation_calls.append((question, tool_call, tool_response))
        return HcxGeneratedAnswer(self.answer, ("ev-risk",), self.conclusion)

    def generate_routed_answer(self, question, tool_call, tool_response):
        raise AssertionError("bounded analysis must use the constrained conclusion contract")


def _public_service(
    retrieval: AnalysisRetrieval,
    *,
    client: JudgmentClient | None = None,
):
    source = StubEvidenceService(retrieval)
    registry = FiveToolRegistry()
    provider = client or JudgmentClient()
    service = HcxFunctionCallingService(
        registry,
        provider,
        analysis_executor=BoundedAnalysisExecutor(source),
    )
    return service, source, registry, provider


def test_public_hcx_runs_bounded_analysis_without_adding_a_sixth_public_tool() -> None:
    service, source, registry, client = _public_service(
        _complete_retrieval(_evidence())
    )

    result = service.answer("삼성전자 공시의 사업위험을 분석해줘")

    assert [item["function"]["name"] for item in service.available_tools()] == list(
        FiveToolRegistry.names
    )
    assert result.status == "answered"
    assert result.answer_allowed is True
    assert result.tool_name == "build_summary_context"
    assert result.metadata["execution_mode"] == "bounded_analysis"
    assert result.metadata["analysis_dimension"] == "business_risk"
    assert result.metadata["conclusion"] == "risk_signal"
    assert result.metadata["evidence_slots"][0]["issuer"] == "삼성전자"
    assert len(source.plans) == 1
    assert registry.calls == []
    assert client.selection_calls == []
    assert len(client.generation_calls) == 1
    assert set(result.to_dict()) == {
        "status", "answer", "tool_name", "tool_response", "answer_allowed",
        "recommended_action", "citation_ids", "citations", "warnings", "metadata",
    }


def test_public_hcx_missing_mandatory_slot_abstains_without_provider_generation() -> None:
    retrieval = AnalysisRetrieval(
        slots=(SlotRetrieval(
            "disclosed_risk_factors", (), False,
            ("required_slot_missing:disclosed_risk_factors",),
        ),),
        complete=False,
        reason_codes=("required_slot_missing:disclosed_risk_factors",),
    )
    service, _source, _registry, client = _public_service(retrieval)

    result = service.answer("삼성전자 공시의 사업위험을 분석해줘")

    assert result.status == "abstained"
    assert result.answer_allowed is False
    assert result.recommended_action == "abstain"
    assert result.metadata["conclusion"] == "insufficient_evidence"
    assert result.tool_response["data"]["conclusion"] == "insufficient_evidence"
    assert client.selection_calls == []
    assert client.generation_calls == []


def test_public_hcx_refuses_recommendations_and_forecasts_before_retrieval_or_provider() -> None:
    service, source, registry, client = _public_service(AnalysisRetrieval())

    recommendation = service.answer("삼성전자 공시를 보고 지금 주식을 사도 될지 판단해줘")
    forecast = service.answer("삼성전자 공시를 바탕으로 내년 매출을 전망해줘")

    assert recommendation.status == "abstained"
    assert forecast.status == "abstained"
    assert "policy_recommendation_or_suitability_refusal" in recommendation.warnings
    assert "policy_forecast_refusal" in forecast.warnings
    assert source.plans == []
    assert registry.calls == []
    assert client.selection_calls == []
    assert client.generation_calls == []


def test_public_hcx_rejects_unbounded_conclusion_or_provider_forecast() -> None:
    cases = (
        JudgmentClient(conclusion="buy"),
        JudgmentClient(answer="향후 위험이 커질 것으로 전망됩니다."),
    )
    for client in cases:
        service, _source, _registry, _client = _public_service(
            _complete_retrieval(_evidence()), client=client
        )

        result = service.answer("삼성전자 공시의 사업위험을 분석해줘")

        assert result.status == "error"
        assert result.answer_allowed is False
        assert result.recommended_action == "abstain"
        assert "hcx_final_generation_failed" in result.warnings
