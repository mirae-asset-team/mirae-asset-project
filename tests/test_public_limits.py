from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from disclosure_db.agent_contracts import VerifiedAnswer
from disclosure_db.api import create_app
from disclosure_db.public_limits import PublicLimitSettings, PublicRequestLimiter


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

        class ReadyAgent:
            evidence_service = ReadyService()
            provider_configured = False

            def answer(self, question: str, **kwargs: object) -> VerifiedAnswer:
                return VerifiedAnswer("답", [], True, True)

        yield ReadyAgent()


@pytest.fixture
def raising_agent():
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as base:
        class ReadyService:
            base_database = Path(base.name)
            overlay_database = None
            search_database = None
            attestation = None
            corpus_revision = "test-revision"

            def company_candidates(self) -> list[str]:
                return ["테스트"]

        class RaisingAgent:
            evidence_service = ReadyService()
            provider_configured = False

            def answer(self, question: str, **kwargs: object) -> VerifiedAnswer:
                raise RuntimeError("agent failed")

        yield RaisingAgent()


def test_rate_limit_reopens_after_rolling_window() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=2, per_ip_concurrency=4, global_concurrency=8)
    )
    first = limiter.try_acquire("198.51.100.1", now=0.0)
    limiter.release("198.51.100.1")
    second = limiter.try_acquire("198.51.100.1", now=1.0)
    limiter.release("198.51.100.1")
    denied = limiter.try_acquire("198.51.100.1", now=2.0)

    assert first.allowed and second.allowed
    assert not denied.allowed
    assert denied.status_code == 429
    assert denied.detail == "rate_limited"
    assert denied.retry_after == 58
    assert limiter.try_acquire("198.51.100.1", now=61.0).allowed


def test_per_ip_and_global_concurrency_release_cleanly() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=2)
    )

    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    assert limiter.try_acquire("198.51.100.1", now=0.1).detail == "ip_busy"
    assert limiter.try_acquire("198.51.100.2", now=0.1).allowed
    assert limiter.try_acquire("198.51.100.3", now=0.1).detail == "server_busy"
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.3", now=0.2).allowed


def test_limit_settings_parse_exact_environment_and_reject_disable_values() -> None:
    settings = PublicLimitSettings.from_env(
        {
            "DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "30",
            "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "2",
            "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "5",
        }
    )

    assert settings == PublicLimitSettings(30, 2, 5)
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "0"})
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "10001"})
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "not-an-int"})


def test_expired_idle_ip_state_is_removed_on_next_acquire() -> None:
    limiter = PublicRequestLimiter(PublicLimitSettings())
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    limiter.release("198.51.100.1")

    assert limiter.try_acquire("198.51.100.2", now=61.0).allowed
    assert limiter.tracked_ip_count == 1


def test_extra_release_does_not_reduce_global_active_count() -> None:
    limiter = PublicRequestLimiter(
        PublicLimitSettings(per_minute=120, per_ip_concurrency=2, global_concurrency=1)
    )
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    limiter.release("198.51.100.1")
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.2", now=0.1).allowed


def test_public_query_uses_socket_ip_and_ignores_forwarded_header(ready_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=4, global_concurrency=8)
    client = TestClient(create_app(ready_agent, public_limits=settings))

    assert client.post(
        "/query",
        json={"question": "첫 질문"},
        headers={"X-Forwarded-For": "1.1.1.1"},
    ).status_code == 200
    response = client.post(
        "/query",
        json={"question": "둘째 질문"},
        headers={"X-Forwarded-For": "2.2.2.2"},
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limited"
    assert response.headers["Retry-After"] == "60"


def test_health_and_static_assets_are_not_rate_limited(ready_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=1, global_concurrency=1)
    client = TestClient(create_app(ready_agent, public_limits=settings))

    for _ in range(3):
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


def test_sequential_queries_release_the_per_ip_lease(ready_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=1)
    client = TestClient(create_app(ready_agent, public_limits=settings))

    assert client.post("/query", json={"question": "첫 질문"}).status_code == 200
    assert client.post("/query", json={"question": "둘째 질문"}).status_code == 200


def test_query_lease_releases_after_agent_exception(raising_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=1)
    client = TestClient(
        create_app(raising_agent, public_limits=settings),
        raise_server_exceptions=False,
    )

    assert client.post("/query", json={"question": "첫 실패"}).status_code == 500
    assert client.post("/query", json={"question": "둘째 실패"}).status_code == 500


def test_legacy_public_answer_route_uses_the_same_limit(ready_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=4, global_concurrency=8)
    client = TestClient(create_app(ready_agent, public_limits=settings))

    first = client.post("/v1/answer", json={"question": "첫 질문"})
    assert first.status_code == 200, first.text
    response = client.post("/v1/answer", json={"question": "둘째 질문"})
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limited"


def test_official_get_answer_route_uses_the_same_limit(ready_agent) -> None:
    from fastapi.testclient import TestClient

    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=4, global_concurrency=8)
    client = TestClient(create_app(ready_agent, public_limits=settings))

    first = client.get("/answer", params={"question_id": "Q-1", "question": "첫 질문"})
    assert first.status_code == 200, first.text
    response = client.get("/answer", params={"question_id": "Q-2", "question": "둘째 질문"})
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limited"
    assert response.headers["Retry-After"] == "60"


def _question_schema(openapi: dict[str, object], path: str, method: str) -> dict[str, object]:
    operation = openapi["paths"][path][method]  # type: ignore[index]
    if method == "get":
        return next(  # type: ignore[return-value]
            parameter["schema"]
            for parameter in operation["parameters"]
            if parameter["name"] == "question"
        )
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = openapi["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]  # type: ignore[index]
    return schema["properties"]["question"]  # type: ignore[return-value]


def test_every_public_question_field_has_the_exact_2000_character_contract(ready_agent) -> None:
    from fastapi.testclient import TestClient

    openapi = TestClient(create_app(ready_agent)).get("/openapi.json").json()
    endpoints = (
        ("/query", "post"),
        ("/answer", "get"),
        ("/v1/query/plan", "post"),
        ("/v1/evidence/search", "post"),
        ("/v1/answer", "post"),
        ("/v1/hcx/function-answer", "post"),
        ("/v1/eval/cases", "post"),
        ("/v1/eval/cases/{identifier}", "put"),
        ("/v1/eval/quick-answer", "post"),
    )

    for path, method in endpoints:
        assert _question_schema(openapi, path, method)["maxLength"] == 2_000, (path, method)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/query", None),
        ("post", "/v1/query/plan", None),
        ("post", "/v1/answer", None),
        ("get", "/answer", {"question_id": "Q-boundary"}),
    ],
)
def test_public_question_boundaries_accept_2000_and_reject_2001(
    ready_agent, method: str, path: str, payload: dict[str, str] | None,
) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(ready_agent))
    accepted = "가" * 2_000
    rejected = "가" * 2_001
    if method == "get":
        accepted_response = client.get(path, params={**(payload or {}), "question": accepted})
        rejected_response = client.get(path, params={**(payload or {}), "question": rejected})
    else:
        accepted_response = client.post(path, json={**(payload or {}), "question": accepted})
        rejected_response = client.post(path, json={**(payload or {}), "question": rejected})

    assert accepted_response.status_code == 200, accepted_response.text
    assert rejected_response.status_code == 422, rejected_response.text


@pytest.mark.parametrize(("method", "path"), [("post", "/query"), ("get", "/answer")])
def test_public_as_of_rejects_impossible_calendar_dates(
    ready_agent, method: str, path: str,
) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(ready_agent))
    if method == "get":
        response = client.get(path, params={
            "question_id": "Q-date", "question": "질문", "as_of": "2025-02-30",
        })
    else:
        response = client.post(path, json={"question": "질문", "as_of": "2025-02-30"})

    assert response.status_code == 422
