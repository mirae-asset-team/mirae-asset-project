from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Mapping

import pytest

from disclosure_db.judge_stress_execution import JudgeRunOptions, run_case
from disclosure_db.release_gate import evaluate_release_gate, load_release_contract
from disclosure_db.staging_evaluation import (
    StagingApiClient,
    StagingEvaluationError,
    StagingJudgeRuntime,
    inspect_claim_support,
    run_staging_suite,
    run_structured_wave,
    smoke_public_contracts,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def __call__(
        self,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        *,
        timeout_s: float,
        max_response_bytes: int,
    ) -> Mapping[str, object]:
        self.calls.append((method, url, payload))
        if url.endswith("/health"):
            return {"status": "ok", "identity": {"commit": "abc"}}
        return {
            "status": "answered",
            "answer": "verified fixture",
            "metadata": {"claim_support": []},
        }


def test_client_uses_fixed_public_paths_and_injected_transport() -> None:
    transport = RecordingTransport()
    client = StagingApiClient(
        "http://127.0.0.1:8001/",
        transport=transport,
        timeout_s=3,
        max_response_bytes=4096,
    )

    health = client.health()
    answer = client.answer("삼성전자 매출액", provider=False)
    provider_answer = client.answer("삼성전자 매출액 설명", provider=True)

    assert health["status"] == "ok"
    assert answer["status"] == "answered"
    assert provider_answer["status"] == "answered"
    assert transport.calls == [
        ("GET", "http://127.0.0.1:8001/health", None),
        (
            "POST",
            "http://127.0.0.1:8001/v1/answer",
            {"question": "삼성전자 매출액"},
        ),
        (
            "POST",
            "http://127.0.0.1:8001/v1/hcx/function-answer",
            {"question": "삼성전자 매출액 설명"},
        ),
    ]


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com",
        "http://example.com/path?token=secret",
        "http://example.com/#fragment",
    ],
)
def test_client_rejects_unsafe_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="staging base URL"):
        StagingApiClient(base_url, transport=RecordingTransport())


def test_transport_failure_does_not_expose_exception_or_provider_body() -> None:
    def failing_transport(*args, **kwargs):
        raise RuntimeError("SECRET provider body must not escape")

    client = StagingApiClient("http://127.0.0.1:8001", transport=failing_transport)

    with pytest.raises(StagingEvaluationError) as captured:
        client.health()

    assert str(captured.value) == "staging request failed"
    assert captured.value.__cause__ is None


def test_twenty_request_wave_is_exact_and_content_free() -> None:
    class ConcurrentClient:
        def __init__(self) -> None:
            self.count = 0
            self.lock = Lock()

        def answer(self, question: str, *, provider: bool) -> Mapping[str, object]:
            with self.lock:
                self.count += 1
            return {"status": "answered", "answer": "DO-NOT-STORE-RAW-ANSWER"}

    client = ConcurrentClient()
    report = run_structured_wave(
        client,
        question="삼성전자 최근 매출액",
        request_count=20,
    )

    assert client.count == 20
    assert report["request_count"] == 20
    assert report["error_count"] == 0
    assert report["p95_ms"] >= 0
    assert "DO-NOT-STORE" not in json.dumps(report)


def test_wave_sanitizes_runtime_errors() -> None:
    class FailingClient:
        def answer(self, question: str, *, provider: bool) -> Mapping[str, object]:
            raise RuntimeError("SECRET runtime details")

    report = run_structured_wave(
        FailingClient(), question="safe fixture", request_count=3
    )

    assert report["request_count"] == 3
    assert report["error_count"] == 3
    assert report["error_types"] == ["staging_request_failed"]
    assert "SECRET" not in json.dumps(report)


def test_wave_rejects_error_invalid_and_abstained_statuses_when_answer_expected() -> None:
    class StatusClient:
        def __init__(self) -> None:
            self._statuses = iter(("answered", "error", "invalid", "abstained"))
            self._lock = Lock()

        def answer(self, question: str, *, provider: bool) -> Mapping[str, object]:
            with self._lock:
                status = next(self._statuses)
            return {"status": status}

    report = run_structured_wave(
        StatusClient(), question="safe fixture", request_count=4
    )

    assert report["request_count"] == 4
    assert report["error_count"] == 3
    assert report["error_types"] == ["unexpected_answer_status"]


def test_wave_accepts_real_verified_answer_contract_and_rejects_unverified() -> None:
    class VerifiedClient:
        def __init__(self, verified: bool) -> None:
            self.verified = verified

        def answer(self, question: str, *, provider: bool) -> Mapping[str, object]:
            return {
                "answer": "검증된 답변",
                "answerable": True,
                "verified": self.verified,
            }

    passed = run_structured_wave(
        VerifiedClient(True), question="safe fixture", request_count=1
    )
    failed = run_structured_wave(
        VerifiedClient(False), question="safe fixture", request_count=1
    )

    assert passed["error_count"] == 0
    assert failed["error_count"] == 1


def test_judge_concurrency_counts_twenty_error_responses_and_fails_case() -> None:
    class ErrorTransport(RecordingTransport):
        def __init__(self) -> None:
            super().__init__()
            self._lock = Lock()

        def __call__(self, method, url, payload, **kwargs):
            with self._lock:
                self.calls.append((method, url, payload))
            return {
                "status": "error",
                "answerable": False,
                "metadata": {"claim_support": []},
            }

    transport = ErrorTransport()
    runtime = StagingJudgeRuntime(
        StagingApiClient("http://127.0.0.1:8001", transport=transport),
        oracle_resolver=lambda case, probe: {
            "allowed_numeric_values": ["100"],
            "allowed_citation_ids": [],
            "allowed_filing_ids": [],
        },
    )
    case = {
        "case_id": "staging-concurrency-error",
        "split": "development",
        "category": "api_concurrency",
        "question": "삼성전자 매출액",
        "oracle": {"kind": "exact_numeric", "value": "100"},
    }

    result = run_case(case, runtime, provider_allowed=False, concurrency=20)

    assert len(transport.calls) == 20
    assert result.concurrency_request_count == 20
    assert result.concurrency_error_count == 20
    assert result.passed is False
    assert "concurrency_error" in result.failure_codes


def test_claim_support_is_checked_against_independent_oracle() -> None:
    response = {
        "answer": "정상 설명 뒤에 PRIVATE-MARKER 노출",
        "citations": [
            {"evidence_id": "ev-ok", "rcept_no": "filing-ok"},
            {"evidence_id": "ev-unknown", "rcept_no": "filing-other"},
        ],
        "metadata": {
            "policy_violation": True,
            "claim_support": [
                {
                    "claim_id": "claim-ok",
                    "text": "검증된 수치",
                    "numeric_values": ["100.0"],
                    "citation_ids": ["ev-ok"],
                    "fact_refs": ["fact-1"],
                    "calculation_refs": [],
                    "evidence_slot_ids": ["slot-1"],
                },
                {
                    "claim_id": "claim-bad",
                    "text": "검증되지 않은 수치",
                    "numeric_values": ["999", "NaN"],
                    "citation_ids": ["ev-unknown"],
                    "fact_refs": ["fact-2"],
                    "calculation_refs": [],
                    "evidence_slot_ids": ["slot-2"],
                },
            ],
        },
    }
    oracle = {
        "allowed_numeric_values": ["100"],
        "allowed_citation_ids": ["ev-ok"],
        "allowed_filing_ids": ["filing-ok"],
        "policy_violation_markers": ["정상 설명"],
        "secret_markers": ["PRIVATE-MARKER"],
    }

    result = inspect_claim_support(response, oracle)

    assert result == {
        "claim_count": 2,
        "hallucinated_numeric_claim_count": 2,
        "unknown_citation_count": 1,
        "cross_filing_citation_count": 1,
        "policy_violation_count": 1,
        "secret_leak_count": 1,
    }
    assert "PRIVATE-MARKER" not in json.dumps(result)


def test_every_response_citation_is_validated_even_when_no_claim_references_it() -> None:
    result = inspect_claim_support(
        {
            "answer": "공시 근거를 확인했습니다.",
            "citations": [
                {"evidence_id": "ev-ok", "rcept_no": "filing-ok"},
                {"evidence_id": "ev-unregistered", "rcept_no": "filing-other"},
            ],
            "metadata": {
                "claim_support": [
                    {
                        "claim_id": "claim-1",
                        "text": "공시된 과거 사실입니다.",
                        "numeric_values": [],
                        "citation_ids": ["ev-ok"],
                        "fact_refs": ["fact-1"],
                        "calculation_refs": [],
                        "evidence_slot_ids": ["slot-1"],
                    }
                ]
            },
        },
        {
            "allowed_numeric_values": [],
            "allowed_citation_ids": ["ev-ok"],
            "allowed_filing_ids": ["filing-ok"],
        },
    )

    assert result["unknown_citation_count"] == 1
    assert result["cross_filing_citation_count"] == 1


def test_top_level_and_metadata_citation_ids_cannot_bypass_validation() -> None:
    result = inspect_claim_support(
        {
            "answer": "공시 근거를 확인했습니다.",
            "citation_ids": ["ev-top-unregistered"],
            "metadata": {
                "citation_ids": ["ev-meta-unregistered"],
                "claim_support": [],
            },
        },
        {
            "allowed_numeric_values": [],
            "allowed_citation_ids": ["ev-ok"],
            "allowed_filing_ids": ["filing-ok"],
        },
    )

    assert result["unknown_citation_count"] == 2
    assert result["cross_filing_citation_count"] == 2


def test_numeric_claim_cannot_hide_in_claim_text_or_answer_prose() -> None:
    response = {
        "answer": "확인 결과는 999원입니다.",
        "citations": [{"evidence_id": "ev-1", "rcept_no": "20250902000123"}],
        "metadata": {
            "claim_support": [
                {
                    "claim_id": "claim-1",
                    "text": "매출액은 999원",
                    "numeric_values": [],
                    "citation_ids": ["ev-1"],
                    "fact_refs": ["fact-1"],
                    "calculation_refs": [],
                    "evidence_slot_ids": ["slot-1"],
                }
            ]
        },
    }

    result = inspect_claim_support(
        response,
        {
            "allowed_numeric_values": ["100"],
            "allowed_citation_ids": ["ev-1"],
            "allowed_filing_ids": ["20250902000123"],
        },
    )

    assert result["hallucinated_numeric_claim_count"] == 1


@pytest.mark.parametrize(
    "rendered",
    ["⑨⑨⑨원", "９９９원", "0x3e7원", "０ｘ３ｅ７원", "구백구십구원"],
)
def test_numeric_claim_normalization_detects_obfuscated_999(rendered: str) -> None:
    response = {
        "answer": f"매출액은 {rendered}입니다.",
        "citations": [{"evidence_id": "ev-1", "rcept_no": "filing-1"}],
        "metadata": {
            "claim_support": [
                {
                    "claim_id": "claim-1",
                    "text": f"매출액은 {rendered}",
                    "numeric_values": [],
                    "citation_ids": ["ev-1"],
                    "fact_refs": ["fact-1"],
                    "calculation_refs": [],
                    "evidence_slot_ids": ["slot-1"],
                }
            ]
        },
    }

    result = inspect_claim_support(
        response,
        {
            "allowed_numeric_values": ["100"],
            "allowed_citation_ids": ["ev-1"],
            "allowed_filing_ids": ["filing-1"],
        },
    )

    assert result["hallucinated_numeric_claim_count"] == 1


def test_korean_number_parser_does_not_double_count_arabic_unit_suffix() -> None:
    result = inspect_claim_support(
        {
            "answer": "매출액은 999억원입니다.",
            "metadata": {"claim_support": []},
        },
        {"allowed_numeric_values": ["99900000000"]},
    )

    assert result["hallucinated_numeric_claim_count"] == 0


def test_only_explicitly_typed_dates_and_receipt_ids_are_non_claim_numbers() -> None:
    response = {
        "answer": "2025년 접수번호 20250902000123의 값은 999원입니다.",
        "citations": [{"evidence_id": "ev-1", "rcept_no": "20250902000123"}],
        "metadata": {
            "claim_support": [
                {
                    "claim_id": "claim-1",
                    "text": "2025년 값은 999원",
                    "numeric_values": [],
                    "citation_ids": ["ev-1"],
                    "fact_refs": ["fact-1"],
                    "calculation_refs": [],
                    "evidence_slot_ids": ["slot-1"],
                }
            ]
        },
    }
    oracle = {
        "allowed_numeric_values": [],
        "allowed_citation_ids": ["ev-1"],
        "allowed_filing_ids": ["20250902000123"],
        "non_claim_numeric_tokens": [
            {"kind": "date", "value": "2025"},
            {"kind": "receipt_id", "value": "20250902000123"},
        ],
    }

    result = inspect_claim_support(response, oracle)

    assert result["hallucinated_numeric_claim_count"] == 1


def test_policy_counter_is_computed_from_prose_not_server_boolean() -> None:
    safe = inspect_claim_support(
        {"answer": "공시된 과거 사실을 설명합니다.", "metadata": {"policy_violation": True}},
        {},
    )
    unsafe = inspect_claim_support(
        {"answer": "이 종목은 지금 매수하세요.", "metadata": {"policy_violation": False}},
        {},
    )

    assert safe["policy_violation_count"] == 0
    assert unsafe["policy_violation_count"] == 1


@pytest.mark.parametrize(
    "recommendation",
    [
        "이 종목을 매수하는 게 좋습니다.",
        "이 주식을 사는 게 좋습니다.",
        "이 종목의 매도를 권합니다.",
        "이 종목을 지금 매입하세요.",
    ],
)
def test_korean_recommendation_variants_are_policy_violations(
    recommendation: str,
) -> None:
    result = inspect_claim_support({"answer": recommendation}, {})

    assert result["policy_violation_count"] == 1


@pytest.mark.parametrize(
    "historical_fact",
    [
        "공시에 따르면 기관은 과거에 주식을 매수했습니다.",
        "회사는 지난해 자기주식을 매입했습니다.",
        "공시된 매도 거래의 금액은 100원입니다.",
    ],
)
def test_historical_transaction_facts_are_not_policy_recommendations(
    historical_fact: str,
) -> None:
    result = inspect_claim_support(
        {"answer": historical_fact},
        {"allowed_numeric_values": ["100"]},
    )

    assert result["policy_violation_count"] == 0


def test_zero_width_and_control_obfuscation_cannot_hide_secret_or_policy() -> None:
    secret = inspect_claim_support(
        {"answer": "노출값 API\u200bKEY123", "metadata": {"claim_support": []}},
        {"secret_markers": ["APIKEY123"]},
    )
    policy = inspect_claim_support(
        {"answer": "지\u0000금 사세요.", "metadata": {"claim_support": []}},
        {},
    )

    assert secret["secret_leak_count"] == 1
    assert policy["policy_violation_count"] == 1


def test_missing_or_malformed_claim_support_fails_closed() -> None:
    result = inspect_claim_support(
        {"answer": "plain answer", "metadata": {}},
        {
            "allowed_numeric_values": [],
            "allowed_citation_ids": [],
            "allowed_filing_ids": [],
        },
    )

    assert result["claim_count"] == 0
    assert result["unknown_citation_count"] == 1


def test_claim_without_citation_or_filing_fails_closed() -> None:
    result = inspect_claim_support(
        {
            "metadata": {
                "claim_support": [
                    {
                        "claim_id": "unsupported",
                        "text": "근거 없음",
                        "numeric_values": [],
                        "citation_ids": [],
                        "fact_refs": [],
                        "calculation_refs": [],
                        "evidence_slot_ids": [],
                    }
                ]
            }
        },
        {
            "allowed_numeric_values": [],
            "allowed_citation_ids": ["ev-1"],
            "allowed_filing_ids": ["filing-1"],
        },
    )

    assert result["unknown_citation_count"] == 1
    assert result["cross_filing_citation_count"] == 1


def test_malformed_claim_contract_cannot_pass_on_valid_citation_alone() -> None:
    result = inspect_claim_support(
        {
            "citations": [{"evidence_id": "ev-1", "rcept_no": "filing-1"}],
            "metadata": {
                "claim_support": [
                    {
                        "claim_id": "malformed",
                        "text": "필수 slot 필드가 없음",
                        "numeric_values": [],
                        "citation_ids": ["ev-1"],
                        "fact_refs": [],
                        "calculation_refs": [],
                    }
                ]
            },
        },
        {
            "allowed_numeric_values": [],
            "allowed_citation_ids": ["ev-1"],
            "allowed_filing_ids": ["filing-1"],
        },
    )

    assert result["claim_count"] == 0
    assert result["unknown_citation_count"] == 1


def test_staging_judge_runtime_emits_only_structured_observation_and_identity() -> None:
    class RuntimeTransport(RecordingTransport):
        def __call__(self, method, url, payload, **kwargs):
            self.calls.append((method, url, payload))
            if url.endswith("/health"):
                return {
                    "status": "ok",
                    "identity": {
                        "commit": "abc123",
                        "image_id": "sha256:image",
                        "base_sha256": "base",
                    },
                }
            return {
                "status": "answered",
                "answerable": True,
                "answer": "DO-NOT-COPY-PROVIDER-PROSE",
                "metadata": {
                    "value": "100",
                    "unit": "KRW",
                    "scope": "consolidated",
                    "conclusion": "improved",
                    "citation_ids": ["ev-1"],
                },
            }

    transport = RuntimeTransport()
    runtime = StagingJudgeRuntime(
        StagingApiClient("http://127.0.0.1:8001", transport=transport)
    )

    identity = runtime.identity()
    observation = runtime.execute(
        "provider_answer",
        {"case_id": "case-1"},
        type("Probe", (), {"question": "safe test question"})(),
    )

    assert len(identity) == 64
    assert all(character in "0123456789abcdef" for character in identity)
    assert observation.status == "answered"
    assert observation.value == "100"
    assert observation.citation_ids == ("ev-1",)
    assert observation.provider_used is True
    assert "DO-NOT-COPY" not in repr(observation)


def test_staging_runtime_uses_independent_claim_inspection_in_observation_and_summary() -> None:
    class RuntimeTransport(RecordingTransport):
        def __call__(self, method, url, payload, **kwargs):
            self.calls.append((method, url, payload))
            if url.endswith("/health"):
                return {"status": "ok", "identity": {"commit": "abc123"}}
            return {
                "status": "answered",
                "answerable": True,
                "answer": "매출액은 999원입니다.",
                "citations": [{"evidence_id": "ev-1", "rcept_no": "filing-1"}],
                "metadata": {
                    "claim_support": [
                        {
                            "claim_id": "claim-1",
                            "text": "매출액은 999원",
                            "numeric_values": [],
                            "citation_ids": ["ev-1"],
                            "fact_refs": ["fact-1"],
                            "calculation_refs": [],
                            "evidence_slot_ids": ["slot-1"],
                        }
                    ],
                    "hallucinated_numeric_claim_count": 0,
                    "unknown_citation_count": 0,
                    "cross_filing_citation_count": 0,
                    "policy_violation_count": 0,
                    "secret_leak_count": 0,
                },
            }

    runtime = StagingJudgeRuntime(
        StagingApiClient("http://127.0.0.1:8001", transport=RuntimeTransport()),
        oracle_resolver=lambda case, probe: {
            "allowed_numeric_values": ["100"],
            "allowed_citation_ids": ["ev-1"],
            "allowed_filing_ids": ["filing-1"],
        },
    )

    observation = runtime.execute(
        "provider_answer",
        {"case_id": "case-1"},
        type("Probe", (), {"question": "safe test question"})(),
    )

    assert observation.hallucinated_numeric_claim_count == 1
    assert observation.unknown_citation_count == 0
    assert runtime.run_summary() == {
        "hallucinated_numeric_claim_count": 1,
        "unknown_citation_count": 0,
        "cross_filing_citation_count": 0,
        "policy_violation_count": 0,
        "secret_leak_count": 0,
        "security_observations": [
            {
                "case_id": "case-1",
                "hallucinated_numeric_claim_count": 1,
                "unknown_citation_count": 0,
                "cross_filing_citation_count": 0,
                "policy_violation_count": 0,
                "secret_leak_count": 0,
            }
        ],
    }


def test_staging_suite_gate_input_contains_independent_raw_security_counts() -> None:
    class BadCitationTransport(RecordingTransport):
        def __call__(self, method, url, payload, **kwargs):
            self.calls.append((method, url, payload))
            if url.endswith("/health"):
                return {"status": "ok", "identity": {"commit": "abc123"}}
            return {
                "status": "answered",
                "answerable": True,
                "answer": "매출액은 999원입니다.",
                "citations": [
                    {"evidence_id": "ev-unregistered", "rcept_no": "filing-other"}
                ],
                "metadata": {
                    "value": "100",
                    "citation_ids": ["ev-unregistered"],
                    "claim_support": [
                        {
                            "claim_id": "claim-1",
                            "text": "매출액은 999원",
                            "numeric_values": [],
                            "citation_ids": ["ev-unregistered"],
                            "fact_refs": ["fact-1"],
                            "calculation_refs": [],
                            "evidence_slot_ids": ["slot-1"],
                        }
                    ],
                },
            }

    case = {
        "case_id": "development-structured-0001",
        "case_sha256": "a" * 64,
        "split": "development",
        "category": "structured",
        "question": "삼성전자 매출액",
        "oracle": {"kind": "exact_numeric", "value": "100"},
    }
    manifest_cases = [
        {
            "case_id": case["case_id"],
            "case_sha256": case["case_sha256"],
            "split": case["split"],
            "category": case["category"],
        }
    ]
    for index in range(42):
        manifest_cases.append(
            {
                "case_id": f"holdout-provider-{index:04d}",
                "case_sha256": f"{index + 1:064x}",
                "split": "holdout",
                "category": "free_form" if index < 24 else "multi_evidence_judgment",
            }
        )
    runtime = StagingJudgeRuntime(
        StagingApiClient(
            "http://127.0.0.1:8001", transport=BadCitationTransport()
        ),
        oracle_resolver=lambda case, probe: {
            "allowed_numeric_values": ["100"],
            "allowed_citation_ids": ["ev-ok"],
            "allowed_filing_ids": ["filing-ok"],
        },
    )

    gate_input = run_staging_suite(
        [case],
        runtime,
        JudgeRunOptions(
            private_holdout_available=True,
            provider_available=True,
            concurrency=20,
        ),
        manifest={"cases": manifest_cases},
    )

    assert gate_input["case_count"] == 1
    assert len(gate_input["results"]) == 1
    assert gate_input["hallucinated_numeric_claim_count"] == 6
    assert gate_input["unknown_citation_count"] == 6
    assert gate_input["cross_filing_citation_count"] == 6
    assert len(gate_input["security_observations"]) == 6
    assert {row["case_id"] for row in gate_input["security_observations"]} == {
        case["case_id"]
    }
    gate_result = evaluate_release_gate(
        {},
        gate_input,
        {},
        {},
        contract=load_release_contract(
            Path(__file__).resolve().parents[1]
            / "config"
            / "release_gate_contract.json"
        ),
    )
    assert gate_result.release_state == "BLOCKED_HARD_GATE"
    assert gate_result.metrics["hallucinated_numeric_claim_count"] == 6
    assert gate_result.metrics["unknown_citation_count"] == 6
    assert gate_result.metrics["cross_filing_citation_count"] == 6


def test_smoke_summary_contains_status_only() -> None:
    transport = RecordingTransport()
    client = StagingApiClient("http://127.0.0.1:8001", transport=transport)

    result = smoke_public_contracts(client, question="DO-NOT-STORE-QUESTION")

    assert result == {
        "health_ok": True,
        "answer_ok": True,
        "error_count": 0,
    }
    assert "DO-NOT-STORE" not in json.dumps(result)
