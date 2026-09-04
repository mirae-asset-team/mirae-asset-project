from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from disclosure_db.agent_contracts import CitationRef, VerifiedAnswer
from disclosure_db.api import create_app


@pytest.fixture
def ready_agent():
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as base:
        class ReadyService:
            base_database = Path(base.name)
            overlay_database = None
            search_database = None
            attestation = None
            corpus_revision = "test-revision"

            def company_candidates(self) -> list[str]:
                return ["테스트"]

        class FakeAgent:
            evidence_service = ReadyService()
            provider_configured = False

            def answer(self, question: str, **kwargs: object) -> VerifiedAnswer:
                return VerifiedAnswer(
                    answer="검증된 답변",
                    citation_ids=["ev1"],
                    verified=True,
                    answerable=True,
                    citations=[CitationRef("ev1", "f1", report_name="사업보고서")],
                )

        yield FakeAgent()


def test_root_serves_accessible_web_shell(ready_agent) -> None:
    from fastapi.testclient import TestClient

    response = TestClient(create_app(ready_agent)).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for element_id in (
        "new-chat",
        "history-search",
        "conversation-list",
        "clear-history",
        "sidebar-toggle",
        "sidebar-close",
        "messages",
        "question-input",
        "send-question",
        "service-status",
        "example-title",
        "question-form",
    ):
        assert f'id="{element_id}"' in response.text
    assert "HCX-005 DISCLOSURE AGENT" in response.text
    assert "근거가 충분하지 않으면 답변을 만들지 않습니다." in response.text
    assert '<script type="module" src="/static/app.js"></script>' in response.text


def test_public_assets_have_security_headers_and_local_sources(ready_agent) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(ready_agent))
    root = client.get("/")

    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
    assert root.headers["x-content-type-options"] == "nosniff"
    assert root.headers["referrer-policy"] == "no-referrer"
    assert root.headers["x-frame-options"] == "DENY"
    assert "http://" not in root.text
    assert "https://" not in root.text
    assert client.get("/static/app.css").headers["content-type"].startswith("text/css")
    assert "javascript" in client.get("/static/app.js").headers["content-type"]
    assert "javascript" in client.get("/static/api.js").headers["content-type"]
    assert "javascript" in client.get("/static/history.js").headers["content-type"]

    for font_name in ("KoPubWorld-Dotum-Bold.woff2",):
        font = client.get(f"/static/fonts/{font_name}")
        assert font.status_code == 200
        assert len(font.content) > 1_000

    css = client.get("/static/app.css").text
    assert "KoPubWorld Dotum" in css
    assert "url(\"/static/fonts/" in css
    assert "https://" not in css
    assert "http://" not in css


def test_public_web_calls_only_hcx_function_answer_for_questions(ready_agent) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(ready_agent))
    api_js = client.get("/static/api.js").text
    app_js = client.get("/static/app.js").text

    assert '"/v1/hcx/function-answer"' in api_js
    for legacy_endpoint in ('"/query"', '"/health"', '"/financial-coverage"'):
        assert legacy_endpoint not in api_js
        assert legacy_endpoint not in app_js
    assert "HCX_API_KEY" not in api_js
    assert "HCX_API_KEY" not in app_js
    assert "SQLITE" not in api_js.upper()
    assert "SQLITE" not in app_js.upper()
    assert "선택 Tool" not in app_js
    assert "HCX가 공시 Tool을 선택" not in app_js
