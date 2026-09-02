from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from disclosure_db.claim_verification import build_claim_admission
from disclosure_db.hcx_function_calling import (
    FINAL_ANSWER_TOOL_NAME,
    HcxFunctionCallingService,
    HcxGeneratedAnswer,
    HcxProtocolError,
    HcxToolCall,
    HyperClovaFunctionClient,
)


FIVE_TOOLS = (
    "analyze_disclosure_trend",
    "build_summary_context",
    "get_correction_lineage",
    "get_financial_facts",
    "search_disclosures",
)


def _item(evidence_id: str, receipt: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_ids": [evidence_id],
        "filing_id": receipt,
        "rcept_no": receipt,
        "report_name": "사업보고서",
        "filed_at": "2026-03-18",
    }


def _fact(
    fact_id: str,
    value: str,
    evidence_id: str,
    *,
    year: str,
) -> dict[str, object]:
    return {
        "financial_fact_id": fact_id,
        "account_id": "revenue",
        "account_name": "매출액",
        "value_numeric": value,
        "display_value": f"{int(value):,}원",
        "scale": 1,
        "unit": "KRW",
        "period": {
            "period_type": "duration",
            "period_start": f"{year}-01-01",
            "period_end": f"{year}-12-31",
            "instant_date": None,
        },
        "scope": "consolidated",
        "company_identifiers": {"company": "삼성전자"},
        "support_level": "structured",
        "validation_status": "validated",
        "evidence_ids": [evidence_id],
    }


def _response(
    *,
    facts: list[dict[str, object]] | None = None,
    calculations: list[dict[str, object]] | None = None,
    slots: list[dict[str, object]] | None = None,
    evidence_ids: tuple[str, ...] = ("ev-1",),
) -> dict[str, object]:
    facts = list(facts or [])
    calculations = list(calculations or [])
    slots = list(slots or [{
        "slot_id": "financial",
        "mandatory": True,
        "complete": True,
        "evidence_ids": list(evidence_ids),
    }])
    return {
        "status": "success",
        "tool_name": "get_financial_facts",
        "data": {
            "facts": facts,
            "calculations": calculations,
            "evidence_slots": slots,
        },
        "evidence_bundle": {
            "question_intent": "get_financial_facts",
            "requested_scope": {"company": "삼성전자"},
            "covered_scope": {"company": ["삼성전자"]},
            "items": [
                _item(evidence_id, f"20260318{index:06d}")
                for index, evidence_id in enumerate(evidence_ids, start=1)
            ],
            "evidence_ids": list(evidence_ids),
            "filing_ids": [f"20260318{index:06d}" for index in range(1, len(evidence_ids) + 1)],
            "issuer_corp_codes": ["00126380"],
            "quality_warnings": [],
            "correction_status": "policy_applied",
            "retrieval_status": {"route": "test"},
            "sufficiency": "sufficient",
        },
        "warnings": [],
        "metadata": {
            "sufficiency_check": {
                "status": "sufficient",
                "answer_allowed": True,
                "recommended_action": "answer",
            },
        },
    }


class StaticRegistry:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "description": name,
                "input_schema": {"type": "object", "properties": {}},
            }
            for name in FIVE_TOOLS
        ]

    def dispatch(self, _name: str, _arguments: object) -> dict[str, object]:
        return self.response


class ClaimClient:
    model = "fake-hcx"
    configured = True

    def __init__(self, generated: object) -> None:
        self.generated = generated

    def select_tool(self, _question: str, _tools: object) -> HcxToolCall:
        return HcxToolCall(
            "call-1",
            "get_financial_facts",
            {"company": "삼성전자", "account": "매출액"},
        )

    def generate_answer(self, *_args: object) -> object:
        return self.generated

    def generate_routed_answer(self, *_args: object) -> object:
        return self.generated


def _generated(
    answer: str,
    *,
    citations: tuple[str, ...] = ("ev-1",),
    claims: tuple[dict[str, object], ...] = (),
    conclusion: str | None = None,
    limitations: tuple[str, ...] = ("historical_disclosure_only",),
) -> object:
    return SimpleNamespace(
        answer=answer,
        citation_ids=citations,
        conclusion=conclusion,
        claims=claims,
        limitations=limitations,
    )


def _claim(
    text: str,
    *,
    citations: tuple[str, ...] = ("ev-1",),
    fact_refs: tuple[str, ...] = (),
    calculation_refs: tuple[str, ...] = (),
    slots: tuple[str, ...] = ("financial",),
    numeric_values: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "claim_id": "claim-1",
        "text": text,
        "citation_ids": list(citations),
        "fact_refs": list(fact_refs),
        "calculation_refs": list(calculation_refs),
        "evidence_slot_ids": list(slots),
        "numeric_values": list(numeric_values),
    }


def _service(response: dict[str, object], generated: object) -> HcxFunctionCallingService:
    return HcxFunctionCallingService(
        StaticRegistry(response),  # type: ignore[arg-type]
        ClaimClient(generated),  # type: ignore[arg-type]
    )


def test_verified_claim_exposes_additive_support_and_bounded_trace() -> None:
    fact = _fact("fact-1", "200", "ev-1", year="2025")
    text = "삼성전자 2025년 연결 매출액은 200원입니다."
    result = _service(
        _response(facts=[fact]),
        _generated(
            text,
            claims=(_claim(
                text,
                fact_refs=("fact-1",),
                numeric_values=("2025", "200"),
            ),),
        ),
    ).answer("삼성전자 2025년 연결 매출액은?")

    assert result.status == "answered"
    assert result.answer_allowed is True
    assert result.metadata["claim_support"][0]["claim_id"] == "claim-1"
    assert result.metadata["claim_support"][0]["fact_refs"] == ["fact-1"]
    assert result.metadata["limitations"] == ["historical_disclosure_only"]
    trace = result.metadata["verification_trace"]
    assert trace == {
        "schema_version": "claim-verification-v1",
        "status": "verified",
        "claim_count": 1,
        "verified_claim_count": 1,
        "checks": [
            {"check": "citations", "status": "passed"},
            {"check": "evidence_slots", "status": "passed"},
            {"check": "numeric_values", "status": "passed"},
            {"check": "calculations", "status": "passed"},
            {"check": "policy", "status": "passed"},
        ],
        "failure_codes": [],
    }
    assert set(result.to_dict()) == {
        "status", "answer", "tool_name", "tool_response", "answer_allowed",
        "recommended_action", "citation_ids", "citations", "warnings", "metadata",
    }


def test_arbitrary_nested_numeric_object_is_not_admitted_as_synthetic_fact() -> None:
    response = _response(facts=[])
    response["data"]["summary"] = {  # type: ignore[index]
        "value": "999",
        "evidence_ids": ["ev-1"],
    }

    admission = build_claim_admission(response)

    assert admission.facts == {}
    assert admission.public_contract()["facts"] == []

    response["data"]["summary"]["item_id"] = "not-a-fact-id"  # type: ignore[index]
    assert build_claim_admission(response).facts == {}


def test_ungrounded_prose_number_is_never_reused_and_generic_answer_abstains() -> None:
    bad = "공시 수치는 12345678901234원입니다."
    response = _response(facts=[])
    response["tool_name"] = "search_disclosures"
    result = _service(
        response,
        _generated(bad, claims=(_claim(bad, numeric_values=("12345678901234",)),)),
    ).answer("삼성전자 공시 수치는?")

    assert result.status == "abstained"
    assert result.answer_allowed is False
    assert bad not in result.answer
    assert "claim_verification_failed" in result.warnings
    assert "numeric_value_not_grounded" in result.metadata["verification_trace"]["failure_codes"]


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_generated_numeric_values_fail_closed(bad_value: str) -> None:
    fact = _fact("fact-1", "200", "ev-1", year="2025")
    text = f"매출액은 {bad_value}원입니다."
    result = _service(
        _response(facts=[fact]),
        _generated(
            text,
            claims=(_claim(text, fact_refs=("fact-1",), numeric_values=(bad_value,)),),
        ),
    ).answer("삼성전자 매출액은?")

    assert result.status == "answered"
    assert bad_value not in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"
    assert "numeric_value_not_finite" in result.metadata["verification_trace"]["failure_codes"]


def test_non_finite_prose_token_cannot_hide_outside_numeric_values() -> None:
    fact = _fact("fact-1", "200", "ev-1", year="2025")
    text = "매출액은 Infinity원입니다."
    result = _service(
        _response(facts=[fact]),
        _generated(
            text,
            claims=(_claim(text, fact_refs=("fact-1",), numeric_values=()),),
        ),
    ).answer("삼성전자 매출액은?")

    assert result.status == "answered"
    assert "Infinity" not in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"
    assert "numeric_value_not_finite" in result.metadata["verification_trace"]["failure_codes"]


def test_calculation_requires_all_operand_evidence_and_exact_decimal_result() -> None:
    facts = [
        _fact("fact-2024", "100", "ev-1", year="2024"),
        _fact("fact-2025", "150", "ev-2", year="2025"),
    ]
    calculations = [{
        "calculation_id": "calc-growth",
        "operation": "growth_rate",
        "value": "50",
        "unit": "%",
        "operands": ["100", "150"],
        "evidence_ids": ["ev-1"],
    }]
    text = "2024년 대비 2025년 매출액 증가율은 50%입니다."
    result = _service(
        _response(facts=facts, calculations=calculations, evidence_ids=("ev-1", "ev-2")),
        _generated(
            text,
            citations=("ev-1", "ev-2"),
            claims=(_claim(
                text,
                citations=("ev-1", "ev-2"),
                fact_refs=("fact-2024", "fact-2025"),
                calculation_refs=("calc-growth",),
                numeric_values=("2024", "2025", "50"),
            ),),
        ),
    ).answer("삼성전자 2024년 대비 2025년 매출액 증가율은?")

    assert result.status == "answered"
    assert text not in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"
    assert "calculation_operand_evidence_missing" in result.metadata["verification_trace"]["failure_codes"]


def test_wrong_evidence_slot_and_mixed_unadmitted_fact_support_fail_closed() -> None:
    mixed = _fact("fact-mixed", "200", "ev-1", year="2025")
    mixed["evidence_ids"] = ["ev-1", "not-admitted"]
    slots = [
        {"slot_id": "financial", "mandatory": True, "complete": True, "evidence_ids": ["ev-1"]},
        {"slot_id": "risk", "mandatory": True, "complete": True, "evidence_ids": ["ev-2"]},
    ]
    text = "삼성전자 2025년 연결 매출액은 200원입니다."
    result = _service(
        _response(facts=[mixed], slots=slots, evidence_ids=("ev-1", "ev-2")),
        _generated(
            text,
            claims=(_claim(
                text,
                fact_refs=("fact-mixed",),
                slots=("risk",),
                numeric_values=("2025", "200"),
            ),),
        ),
    ).answer("삼성전자 매출액은?")

    assert result.status == "abstained"
    assert text not in result.answer
    failures = result.metadata["verification_trace"]["failure_codes"]
    assert "fact_ref_not_admitted" in failures
    assert "evidence_slot_mismatch" in failures


@dataclass
class _BoundedExecution:
    response: dict[str, object]
    complete: bool = True
    conclusion: str | None = None
    reason_codes: tuple[str, ...] = ()
    evidence_slots: tuple[dict[str, object], ...] = ()

    def __post_init__(self) -> None:
        self.plan = SimpleNamespace(
            analysis_mode="bounded_judgment",
            judgment_dimension="business_risk",
            allowed_conclusions=("risk_signal", "stable", "mixed", "insufficient_evidence"),
        )

    def to_tool_response(self) -> dict[str, object]:
        return self.response


class _BoundedExecutor:
    def __init__(self, execution: _BoundedExecution) -> None:
        self.execution = execution

    def execute(self, _question: str) -> _BoundedExecution:
        return self.execution


def test_bounded_verification_failure_abstains_and_conclusion_metadata_agrees() -> None:
    response = _response(facts=[])
    response["tool_name"] = "build_summary_context"
    response["data"].update({
        "allowed_conclusions": ["risk_signal", "stable", "mixed", "insufficient_evidence"],
        "conclusion": None,
        "analysis_dimension": "business_risk",
        "limitations": ["historical_disclosure_only"],
    })
    bad = "공시 근거와 무관하게 위험 신호입니다."
    execution = _BoundedExecution(response, evidence_slots=tuple(response["data"]["evidence_slots"]))
    service = HcxFunctionCallingService(
        StaticRegistry(response),  # type: ignore[arg-type]
        ClaimClient(_generated(
            bad,
            conclusion="risk_signal",
            claims=(_claim(bad, citations=("not-admitted",)),),
        )),  # type: ignore[arg-type]
        analysis_executor=_BoundedExecutor(execution),  # type: ignore[arg-type]
    )

    result = service.answer("삼성전자 공시의 사업위험을 분석해줘")

    assert result.status == "abstained"
    assert bad not in result.answer
    assert result.metadata["conclusion"] == "insufficient_evidence"
    assert result.tool_response["data"]["conclusion"] == "insufficient_evidence"
    assert result.metadata["verification_trace"]["status"] == "abstained"


def test_explicit_incomplete_slots_do_not_create_legacy_slot_for_bounded_analysis() -> None:
    fact = _fact("fact-1", "200", "ev-1", year="2025")
    response = _response(facts=[fact], slots=[{
        "slot_id": "financial",
        "mandatory": True,
        "complete": False,
        "evidence_ids": ["ev-1"],
    }])
    response["tool_name"] = "build_summary_context"
    response["data"].update({  # type: ignore[union-attr]
        "allowed_conclusions": ["risk_signal", "stable", "mixed", "insufficient_evidence"],
        "conclusion": None,
        "analysis_dimension": "business_risk",
        "limitations": ["historical_disclosure_only"],
    })
    text = "삼성전자 2025년 연결 매출액 200원을 근거로 위험 신호입니다."
    execution = _BoundedExecution(
        response,
        evidence_slots=tuple(response["data"]["evidence_slots"]),  # type: ignore[index]
    )
    service = HcxFunctionCallingService(
        StaticRegistry(response),  # type: ignore[arg-type]
        ClaimClient(_generated(
            text,
            conclusion="risk_signal",
            claims=(_claim(
                text,
                fact_refs=("fact-1",),
                slots=("tool_evidence",),
                numeric_values=("2025", "200"),
            ),),
        )),  # type: ignore[arg-type]
        analysis_executor=_BoundedExecutor(execution),  # type: ignore[arg-type]
    )

    result = service.answer("삼성전자 공시의 사업위험을 분석해줘")

    assert result.status == "abstained"
    assert text not in result.answer
    assert build_claim_admission(response).evidence_slots == {}
    assert result.metadata["conclusion"] == "insufficient_evidence"


def test_legacy_generated_answer_without_claims_gets_safe_deterministic_fallback() -> None:
    fact = _fact("fact-1", "1234567", "ev-1", year="2025")
    fact.pop("display_value")
    generated = HcxGeneratedAnswer("삼성전자 매출액은 999원입니다.", ("ev-1",))

    result = _service(_response(facts=[fact]), generated).answer("삼성전자 매출액은?")

    assert result.status == "answered"
    assert "1,234,567원입니다" in result.answer
    assert "999원" not in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"


def test_legacy_generated_qualitative_prose_without_claims_is_never_reused() -> None:
    fact = _fact("fact-1", "200", "ev-1", year="2025")
    bad = "공시를 보면 수익성이 뚜렷하게 개선됐습니다."
    generated = HcxGeneratedAnswer(bad, ("ev-1",))

    result = _service(_response(facts=[fact]), generated).answer("삼성전자 매출액은?")

    assert result.status == "answered"
    assert bad not in result.answer
    assert "200원입니다" in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"
    assert "claim_missing" in result.metadata["verification_trace"]["failure_codes"]


def test_deterministic_fallback_excludes_row_without_fact_id_or_evidence() -> None:
    admitted = _fact("fact-1", "200", "ev-1", year="2025")
    orphan = dict(_fact("unused", "999", "ev-1", year="2025"))
    orphan.pop("financial_fact_id")
    orphan.pop("evidence_ids")
    generated = HcxGeneratedAnswer("검증되지 않은 설명입니다.", ("ev-1",))

    result = _service(
        _response(facts=[admitted, orphan]), generated
    ).answer("삼성전자 매출액은?")

    assert result.status == "answered"
    assert "200원입니다" in result.answer
    assert "999원" not in result.answer
    assert result.metadata["verification_trace"]["status"] == "fallback"


class _HttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        import json

        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _UrlOpen:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[dict[str, object]] = []

    def __call__(self, request: object, *, timeout: float) -> _HttpResponse:
        import json

        del timeout
        self.requests.append(json.loads(request.data.decode("utf-8")))  # type: ignore[attr-defined]
        return _HttpResponse(self.payload)


def _final_tool_payload(arguments: dict[str, object]) -> dict[str, object]:
    import json

    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-final",
                "type": "function",
                "function": {
                    "name": FINAL_ANSWER_TOOL_NAME,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }],
        }}],
    }


def test_concrete_routed_generation_forces_additive_claim_contract() -> None:
    text = "삼성전자 2025년 연결 매출액은 200원입니다."
    opener = _UrlOpen(_final_tool_payload({
        "answer": text,
        "citation_ids": ["ev-1"],
        "claims": [_claim(
            text,
            fact_refs=("fact-1",),
            numeric_values=("2025", "200"),
        )],
        "limitations": ["historical_disclosure_only"],
    }))
    client = HyperClovaFunctionClient(
        api_key="in-memory-test-key", urlopen=opener
    )

    generated = client.generate_routed_answer(
        "삼성전자 매출액은?",
        HcxToolCall("call-1", "get_financial_facts", {"company": "삼성전자"}),
        _response(facts=[_fact("fact-1", "200", "ev-1", year="2025")]),
    )

    assert generated.claims[0].claim_id == "claim-1"
    payload = opener.requests[0]
    assert payload["tool_choice"] == {
        "type": "function", "function": {"name": FINAL_ANSWER_TOOL_NAME},
    }
    parameters = payload["tools"][0]["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == [
        "answer", "citation_ids", "claims", "limitations",
    ]
    claim_schema = parameters["properties"]["claims"]["items"]
    assert claim_schema["additionalProperties"] is False
    assert claim_schema["properties"]["fact_refs"]["items"]["enum"] == ["fact-1"]
    assert claim_schema["properties"]["evidence_slot_ids"]["items"]["enum"] == ["financial"]


def test_concrete_client_rejects_legacy_final_shape_without_claims() -> None:
    opener = _UrlOpen(_final_tool_payload({
        "answer": "근거 기반 답변",
        "citation_ids": ["ev-1"],
    }))
    client = HyperClovaFunctionClient(
        api_key="in-memory-test-key", urlopen=opener
    )

    with pytest.raises(HcxProtocolError, match="hcx_final_output_schema_mismatch"):
        client.generate_answer(
            "삼성전자 매출액은?",
            HcxToolCall("call-1", "get_financial_facts", {"company": "삼성전자"}),
            _response(facts=[_fact("fact-1", "200", "ev-1", year="2025")]),
        )
