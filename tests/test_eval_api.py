from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from disclosure_db.api import create_app
from disclosure_db.hcx_function_calling import FunctionCallingResult
from disclosure_db.question_routing import DeterministicQuestionRouter


class ReadyService:
    def __init__(self, database: Path) -> None:
        self.base_database = database
        self.overlay_database = None
        self.search_database = None
        self.attestation = None
        self.corpus_revision = "eval-test"
    def company_candidates(self): return ["에스엠", "하이브"]


class FakeAgent:
    provider_configured = False
    def __init__(self, database: Path) -> None: self.evidence_service = ReadyService(database)


class FakeFunctionService:
    def __init__(self) -> None:
        self.router = DeterministicQuestionRouter(
            ["에스엠", "하이브"],
            company_aliases={"에스엠": ["에스엠엔터테인먼트"], "하이브": ["하이브엔터테인먼트"]},
        )
        self.client = type("Client", (), {"configured": True})()

    def answer(self, question: str) -> FunctionCallingResult:
        return FunctionCallingResult(
            "answered", "근거 기반 비교", "get_financial_facts",
            {
                "status": "success", "tool_name": "get_financial_facts",
                "data": {"facts": [{"display_value": "1억 원"}, {"display_value": "2억 원"}], "comparison": {"status": "complete"}},
                "evidence_bundle": {}, "warnings": [],
                "metadata": {"executors": ["get_financial_facts", "get_financial_facts"], "sufficiency_check": {"status": "sufficient"}},
            },
            True, "answer", ["ev1"], [{"evidence_id": "ev1", "rcept_no": "20260318000001"}], [],
            {"tool_selection_called": False, "final_generation_called": True},
        )


class FailingFunctionService(FakeFunctionService):
    def answer(self, question: str) -> FunctionCallingResult:
        raise RuntimeError("secret connection detail must not escape")


def test_eval_api_is_disabled_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EVAL_ENABLED", raising=False)
    database = tmp_path / "base.sqlite"; database.touch()
    client = TestClient(create_app(FakeAgent(database), function_calling_service=FakeFunctionService()))
    assert client.get("/eval").status_code == 404
    assert client.get("/v1/eval/cases").status_code == 404


def test_eval_dashboard_crud_run_and_results(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "base.sqlite"; database.touch()
    cases = tmp_path / "qa_cases.jsonl"; results = tmp_path / "qa_results"
    monkeypatch.setenv("EVAL_ENABLED", "1")
    monkeypatch.setenv("EVAL_CASES_PATH", str(cases))
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(results))
    client = TestClient(create_app(FakeAgent(database), function_calling_service=FakeFunctionService()))
    payload = {
        "id": "financial_compare_001",
        "question": "에스엠엔터테인먼트와 하이브엔터테인먼트 중 2025년 매출액이 더 높은 곳은?",
        "category": "financial_comparison",
        "expected": {
            "companies": ["에스엠", "하이브"], "period": "2025", "metric": "매출액",
            "workflow": "financial_comparison", "comparison_complete": True,
            "backend_display_value_required": True, "citation_required": True,
            "dense_max_calls": 0, "function_calling_max_calls": 0,
        },
        "forbidden_phrases": [],
    }

    assert client.get("/eval").status_code == 200
    assert client.get("/static/eval.js").status_code == 200
    assert client.get("/static/eval.css").status_code == 200
    assert client.post("/v1/eval/cases", json=payload).status_code == 201
    assert len(client.get("/v1/eval/cases").json()["cases"]) == 1
    payload["question"] = payload["question"].replace("어디야", "어디인가")
    assert client.put("/v1/eval/cases/financial_compare_001", json=payload).status_code == 200

    run = client.post("/v1/eval/run/financial_compare_001").json()
    assert (run["total"], run["pass"], run["fail"]) == (1, 1, 0)
    assert run["results"][0]["agent_answer"] == "근거 기반 비교"
    assert run["results"][0]["actual"]["workflow"] == "financial_comparison"
    assert run["results"][0]["evidence_status"]["citation_count"] == 1
    quick = client.post("/v1/eval/quick-answer", json={"question": payload["question"]}).json()
    assert quick["status"] == "PASS"
    assert quick["answer"] == "근거 기반 비교"
    assert quick["citations"][0]["rcept_no"] == "20260318000001"
    assert client.get(f"/v1/eval/results/{run['run_id']}").status_code == 200
    assert len(client.get("/v1/eval/results").json()) == 1
    assert client.delete("/v1/eval/cases/financial_compare_001").status_code == 204


def test_eval_dashboard_modal_copy_and_error_rendering_contract(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "base.sqlite"; database.touch()
    monkeypatch.setenv("EVAL_ENABLED", "1")
    monkeypatch.setenv("EVAL_CASES_PATH", str(tmp_path / "qa_cases.jsonl"))
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path / "qa_results"))
    client = TestClient(create_app(FakeAgent(database), function_calling_service=FailingFunctionService()))

    html = client.get("/eval").text
    javascript = client.get("/static/eval.js").text
    assert 'id="close-case" type="button"' in html
    assert 'id="cancel-case" type="button"' in html
    assert 'id="quick-form"' in html and 'id="quick-save"' in html
    assert 'addEventListener("cancel"' in javascript
    assert 'document.execCommand("copy")' in javascript
    assert "showCopyFallback(report)" in javascript
    assert 'failure_layer:"INFRA"' in javascript
    assert 'api("/v1/eval/quick-answer"' in javascript

    failed = client.post("/v1/eval/quick-answer", json={"question": "실제 질문"}).json()
    assert failed["status"] == "FAIL"
    assert failed["failure_layer"] == "INFRA"
    assert failed["failure_reason"] == "agent_execution_failed"
    assert "secret" not in str(failed)
