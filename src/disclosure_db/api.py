"""Optional FastAPI surface for the disclosure agent."""

from __future__ import annotations

from datetime import date
import os
import re
import sqlite3
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from .attestation import verify_fast_identity
from .agent import DisclosureAgent
from .agent_contracts import to_jsonable
from .calculator import calculate
from .qa_evaluation import QA_CATEGORIES, QaCaseStore, QaEvaluator
from .public_limits import PublicLimitSettings, PublicRequestLimiter
from .input_hardening import (
    PUBLIC_QUESTION_MAX_CHARS,
    QuestionInputError,
    preflight_public_question,
)
from .query_planner import plan_query
from .qa_lab import QaLabError, QaLabStore


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

_RELEASE_IDENTITY_ENV = (
    ("commit", "RELEASE_COMMIT", re.compile(r"[0-9a-fA-F]{40}")),
    ("image_id", "RELEASE_IMAGE_ID", re.compile(r"sha256:[0-9a-fA-F]{64}")),
    ("base_sha256", "RELEASE_BASE_SHA256", re.compile(r"[0-9a-fA-F]{64}")),
    ("overlay_sha256", "RELEASE_OVERLAY_SHA256", re.compile(r"[0-9a-fA-F]{64}")),
    (
        "search_index_sha256",
        "RELEASE_SEARCH_INDEX_SHA256",
        re.compile(r"[0-9a-fA-F]{64}"),
    ),
)


def _release_identity_from_environment() -> dict[str, str] | None:
    identity: dict[str, str] = {}
    for public_name, environment_name, pattern in _RELEASE_IDENTITY_ENV:
        value = os.environ.get(environment_name, "").strip()
        if pattern.fullmatch(value) is None:
            return None
        identity[public_name] = value.lower()
    return identity


def _validated_as_of(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("as_of_date_invalid") from exc
    return value


def _validated_question(value: str) -> str:
    try:
        return preflight_public_question(value)
    except QuestionInputError as exc:
        raise ValueError(exc.code) from exc


def _fetch_financial_facts(
    service: Any,
    *,
    filing_id: str | None = None,
    company: str | None = None,
    account_id: str | None = None,
    fiscal_year: int | None = None,
    scope: str | None = None,
    as_of: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    if not getattr(service, "overlay_database", None) or getattr(service, "attestation", None) is None:
        return []
    from .financial_overlay import fetch_overlay_facts
    return fetch_overlay_facts(
        service.base_database,
        service.overlay_database,
        filing_id=filing_id,
        company=company,
        account_id=account_id,
        fiscal_year=fiscal_year,
        scope=scope,
        as_of=as_of,
        limit=limit,
        attestation=getattr(service, "attestation", None),
    )


def _fetch_financial_coverage(service: Any, *, account_id: str | None = None) -> dict[str, object]:
    if not getattr(service, "overlay_database", None) or getattr(service, "attestation", None) is None:
        return {}
    from .financial_overlay import fetch_financial_coverage
    return fetch_financial_coverage(
        service.base_database,
        service.overlay_database,
        account_id=account_id,
        attestation=getattr(service, "attestation", None),
    )


def _fetch_event_facts(
    service: Any,
    *,
    company: str | None = None,
    predicate: str | None = None,
    as_of: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Read only attested, evidence-backed event facts for the API surface."""
    overlay_database = getattr(service, "overlay_database", None)
    if not overlay_database or getattr(service, "attestation", None) is None:
        return []
    from .financial_overlay import fetch_event_facts
    return fetch_event_facts(
        service.base_database,
        overlay_database,
        company=company,
        predicate_terms=[predicate] if predicate else [],
        as_of=as_of,
        limit=limit,
        attestation=getattr(service, "attestation", None),
    )


def _health_status(service: Any) -> dict[str, Any]:
    base_database = Path(service.base_database)
    base_exists = base_database.exists()
    attestation_configured = getattr(service, "attestation", None) is not None
    base_attested = bool(base_exists)
    if attestation_configured:
        base_attested = verify_fast_identity(base_database, service.attestation)
    overlay_database = getattr(service, "overlay_database", None)
    overlay_configured = bool(overlay_database)
    # An overlay cannot be attested from presence alone. Without the startup
    # attestation, fail readiness closed rather than hashing the base on /health.
    overlay_attested = bool(
        overlay_configured and Path(overlay_database).exists() and base_attested and attestation_configured
    )
    if overlay_attested and attestation_configured:
        from .financial_overlay import overlay_matches_base
        overlay_attested = overlay_matches_base(
            base_database,
            Path(overlay_database),
            attestation=service.attestation,
        )
    search_database = getattr(service, "search_database", None)
    search_configured = bool(search_database)
    search_index_ready = not search_configured
    cached_search_ready = getattr(service, "search_index_ready", None)
    if search_configured and cached_search_ready is not None:
        search_index_ready = bool(cached_search_ready)
    elif search_configured and base_attested and attestation_configured and Path(search_database).exists():
        try:
            from .search_index import SafeSearchIndex
            SafeSearchIndex(
                Path(search_database),
                base_sha256=service.attestation.sha256,
                expected_base_size=service.attestation.size_bytes,
            )
        except (OSError, ValueError, TypeError, sqlite3.Error):
            search_index_ready = False
        else:
            search_index_ready = True
    ready = base_exists and base_attested and (not overlay_configured or overlay_attested) and search_index_ready
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "base_attested": base_attested,
        "attestation_configured": attestation_configured,
        "overlay_configured": overlay_configured,
        "overlay_attested": overlay_attested,
        "search_index_configured": search_configured,
        "search_index_ready": search_index_ready,
    }


def create_app(
    agent: DisclosureAgent,
    *,
    function_calling_service: Any | None = None,
    public_limits: PublicLimitSettings | None = None,
    qa_database: Path | None = None,
):
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field, field_validator
    except ImportError as exc:  # pragma: no cover - depends on optional deployment extra
        raise RuntimeError("FastAPI is optional; install miraeasset-disclosure-db[agent]") from exc

    class QueryRequest(BaseModel):
        question: str = Field(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS)
        company: str | None = None
        as_of: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$")
        limit: int = Field(default=20, ge=1, le=100)

        @field_validator("as_of")
        @classmethod
        def validate_as_of(cls, value: str | None) -> str | None:
            return _validated_as_of(value)

        @field_validator("question")
        @classmethod
        def validate_question(cls, value: str) -> str:
            return _validated_question(value)

    class HcxFunctionCallingRequest(BaseModel):
        question: str = Field(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS)

        @field_validator("question")
        @classmethod
        def validate_question(cls, value: str) -> str:
            return _validated_question(value)

    class EvalCaseRequest(BaseModel):
        id: str = Field(min_length=3, max_length=100)
        question: str = Field(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS)
        category: str
        expected: dict[str, Any] = Field(default_factory=dict)
        forbidden_phrases: list[str] = Field(default_factory=list)

        @field_validator("question")
        @classmethod
        def validate_question(cls, value: str) -> str:
            return _validated_question(value)

    class EvalRunRequest(BaseModel):
        ids: list[str] = Field(default_factory=list)
        failed_only: bool = False

    class EvalQuickQuestionRequest(BaseModel):
        question: str = Field(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS)

        @field_validator("question")
        @classmethod
        def validate_question(cls, value: str) -> str:
            return _validated_question(value)

    class ContestQueryRequest(BaseModel):
        question_id: str | None = Field(default=None, max_length=200)
        question: str = Field(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS)
        company: str | None = Field(default=None, max_length=200)
        as_of: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$")
        limit: int = Field(default=20, ge=1, le=100)

        @field_validator("as_of")
        @classmethod
        def validate_as_of(cls, value: str | None) -> str | None:
            return _validated_as_of(value)

        @field_validator("question")
        @classmethod
        def validate_question(cls, value: str) -> str:
            return _validated_question(value)

    class SearchRequest(QueryRequest):
        limit: int = Field(default=20, ge=1, le=100)

    class CalculationRequest(BaseModel):
        operation: str
        operands: list[str]
        unit: str | None = None
        evidence_ids: list[str] = Field(default_factory=list)

    app = FastAPI(title="Mirae Asset Disclosure Agent", version="0.3.0")
    web_directory = Path(__file__).with_name("web")
    limit_settings = PublicLimitSettings.from_env() if public_limits is None else public_limits
    request_limiter = PublicRequestLimiter(limit_settings)
    eval_enabled = os.environ.get("EVAL_ENABLED") == "1"
    project_root = Path(__file__).resolve().parents[2]
    eval_store = QaCaseStore(
        Path(os.environ.get("EVAL_CASES_PATH") or project_root / "eval" / "qa_cases.jsonl"),
        Path(os.environ.get("EVAL_RESULTS_DIR") or project_root / "eval" / "qa_results"),
    ) if eval_enabled else None
    evaluator = (
        QaEvaluator(function_calling_service, eval_store)
        if eval_store is not None and function_calling_service is not None
        else None
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.middleware("http")
    async def limit_public_queries(request, call_next):
        is_post_query = request.method == "POST" and request.url.path in {
            "/query", "/v1/answer", "/v1/hcx/function-answer",
        }
        is_official_get = request.method == "GET" and request.url.path == "/answer"
        if not (is_post_query or is_official_get):
            return await call_next(request)

        client_ip = request.client.host if request.client is not None else "unknown"
        admission = request_limiter.try_acquire(client_ip)
        if not admission.allowed:
            return JSONResponse(
                {"detail": admission.detail},
                status_code=admission.status_code,
                headers={"Retry-After": str(admission.retry_after)},
            )
        try:
            return await call_next(request)
        finally:
            request_limiter.release(client_ip)

    app.mount("/static", StaticFiles(directory=web_directory), name="static")
    configured_qa_db = qa_database
    if configured_qa_db is None:
        env_qa_db = os.environ.get("DISCLOSURE_QA_DB")
        if env_qa_db:
            configured_qa_db = Path(env_qa_db)
    corpus_database = getattr(getattr(agent, "evidence_service", None), "base_database", None)
    qa_store = (
        QaLabStore(
            configured_qa_db,
            corpus_database=Path(corpus_database) if corpus_database is not None else None,
        )
        if configured_qa_db is not None
        else None
    )
    startup_health: dict[str, Any] | None = None
    release_identity = _release_identity_from_environment()

    def require_qa_store() -> QaLabStore:
        if qa_store is None:
            raise HTTPException(status_code=503, detail="lab_db_not_configured")
        return qa_store

    def validated_health_snapshot() -> dict[str, Any]:
        health = _health_status(agent.evidence_service)
        company_count = 0
        if health.get("ready"):
            candidates = getattr(agent.evidence_service, "company_candidates", None)
            if callable(candidates):
                company_count = len(candidates())
        health["company_count"] = company_count
        return health

    @app.get("/", include_in_schema=False)
    def public_web():
        return FileResponse(web_directory / "index.html")

    @app.get("/lab", include_in_schema=False)
    def qa_lab_page():
        return FileResponse(web_directory / "lab.html")

    @app.get("/eval", include_in_schema=False)
    def eval_web():
        if not eval_enabled:
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(web_directory / "eval.html")

    @app.on_event("startup")
    async def validate_runtime_once() -> None:
        """Run the full read-only runtime validation before serving requests.

        Search-index validation includes ``PRAGMA quick_check``.  Running that
        scan for every health request makes Docker Desktop bind mounts exceed
        the short healthcheck timeout, while the serving inputs are explicitly
        mounted read-only.  Validate once at startup and reuse the attested
        result for readiness and health responses.
        """
        nonlocal startup_health
        startup_health = validated_health_snapshot()

    def runtime_health() -> dict[str, Any]:
        nonlocal startup_health
        if startup_health is None:
            startup_health = validated_health_snapshot()
        return dict(startup_health)

    def envelope(payload: Any, started: float) -> dict[str, Any]:
        body = to_jsonable(payload)
        if isinstance(body, dict):
            body = dict(body)
        else:
            body = {"data": body}
        body.update({
            "request_id": uuid4().hex,
            "corpus_revision": getattr(agent.evidence_service, "corpus_revision", "unknown"),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        })
        return body

    @app.get("/health")
    def health() -> dict[str, Any]:
        started = perf_counter()
        health_payload = runtime_health()
        health_payload["provider_configured"] = bool(getattr(agent, "provider_configured", False))
        function_client = getattr(function_calling_service, "client", None)
        health_payload["function_calling_configured"] = bool(
            function_calling_service is not None and getattr(function_client, "configured", False)
        )
        health_payload["eval_enabled"] = eval_enabled
        if release_identity is not None:
            health_payload["identity"] = dict(release_identity)
        return envelope(health_payload, started)

    def verified_answer(
        question: str,
        *,
        company: str | None = None,
        as_of: str | None = None,
        limit: int = 20,
    ) -> Any:
        health_status = runtime_health()
        if not health_status["ready"]:
            raise HTTPException(status_code=503, detail="runtime_not_ready")
        question = plan_query(
            question,
            company_candidates=agent.evidence_service.company_candidates(),
            company_hint=company,
            as_of=as_of,
        ).question
        return agent.answer(
            question,
            company=company,
            as_of=as_of,
            limit=limit,
        )

    def contest_query(request: ContestQueryRequest) -> dict[str, Any]:
        started = perf_counter()
        answer = verified_answer(
            request.question,
            company=request.company,
            as_of=request.as_of,
            limit=request.limit,
        )
        body = to_jsonable(answer)
        citations = body.pop("citations", [])
        body.pop("citation_ids", None)
        body["question_id"] = request.question_id
        body["evidence"] = [
            {**citation, "receipt_no": citation["filing_id"]}
            for citation in citations
        ]
        return envelope(body, started)

    # ``from __future__ import annotations`` stores this nested model as a
    # string, while newer FastAPI versions resolve route annotations from the
    # module namespace. Bind the local model before registering the route.
    contest_query.__annotations__["request"] = ContestQueryRequest
    app.post("/query")(contest_query)

    def official_answer(
        question_id: str = Query(min_length=1, max_length=200),
        question: str = Query(min_length=1, max_length=PUBLIC_QUESTION_MAX_CHARS),
        company: str | None = Query(default=None, max_length=200),
        as_of: str | None = Query(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$"),
    ) -> dict[str, str]:
        try:
            as_of = _validated_as_of(as_of)
            question = _validated_question(question)
        except ValueError:
            raise HTTPException(status_code=422, detail="question_or_as_of_invalid") from None
        question = plan_query(
            question,
            company_candidates=agent.evidence_service.company_candidates(),
            company_hint=company,
            as_of=as_of,
        ).question
        verified = verified_answer(question, company=company, as_of=as_of)
        citations = list(getattr(verified, "citations", []) or [])[:20]
        context_rows = []
        for citation in citations:
            report_name = getattr(citation, "report_name", None) or "공시"
            filed_at = getattr(citation, "filed_at", None) or "일자 미상"
            filing_id = getattr(citation, "filing_id", None) or "접수번호 미상"
            context_rows.append(
                f"공시명={report_name} | 공시일={filed_at} | 접수번호={filing_id}"
            )
        retrieved_context = "\n".join(context_rows)[:6000]
        if bool(getattr(verified, "answerable", False)) and bool(getattr(verified, "verified", False)):
            trace = "질의 구조화 -> 공시 검색 -> 정정·수치 검증 -> 근거 귀속"
        else:
            trace = "질의 구조화 -> 공시 검색 -> 근거 검증 -> 정보한계 판정"
        return {
            "question_id": question_id,
            "question": question,
            "retrieved_context": retrieved_context,
            "think_trace": trace,
            "answer": str(getattr(verified, "answer", "")),
        }

    app.get("/answer")(official_answer)

    def query_plan(request: QueryRequest) -> dict[str, Any]:
        started = perf_counter()
        plan = plan_query(request.question, company_candidates=agent.evidence_service.company_candidates(), company_hint=request.company, as_of=request.as_of)
        return envelope(plan, started)

    query_plan.__annotations__["request"] = QueryRequest
    app.post("/v1/query/plan")(query_plan)

    def evidence_search(request: SearchRequest) -> dict[str, Any]:
        started = perf_counter()
        plan = plan_query(request.question, company_candidates=agent.evidence_service.company_candidates(), company_hint=request.company, as_of=request.as_of)
        return envelope(agent.evidence_service.search(plan, limit=request.limit), started)

    evidence_search.__annotations__["request"] = SearchRequest
    app.post("/v1/evidence/search")(evidence_search)

    @app.get("/financial-facts", include_in_schema=False)
    @app.get("/v1/financial-facts")
    def financial_facts(
        filing_id: str | None = None,
        company: str | None = None,
        account_id: str | None = None,
        fiscal_year: int | None = Query(default=None, ge=1900, le=2200),
        scope: str | None = Query(default=None, pattern=r"^(consolidated|separate|unknown)$"),
        as_of: str | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            as_of = _validated_as_of(as_of)
        except ValueError:
            raise HTTPException(status_code=422, detail="as_of_date_invalid") from None
        service = agent.evidence_service
        if not getattr(service, "overlay_database", None):
            return envelope({"facts": [], "reason": "overlay_not_configured"}, started)
        facts = _fetch_financial_facts(
            service,
            filing_id=filing_id,
            company=company,
            account_id=account_id,
            fiscal_year=fiscal_year,
            scope=scope,
            as_of=as_of,
            limit=limit,
        )
        return envelope({"facts": facts}, started)

    @app.get("/financial-coverage", include_in_schema=False)
    @app.get("/v1/financial-coverage")
    def financial_coverage(account_id: str | None = None) -> dict[str, Any]:
        started = perf_counter()
        service = agent.evidence_service
        if not getattr(service, "overlay_database", None):
            return envelope({"snapshot": {}, "metrics": [], "companies": [], "reason": "overlay_not_configured"}, started)
        coverage = _fetch_financial_coverage(service, account_id=account_id)
        if not coverage:
            return envelope({"snapshot": {}, "metrics": [], "companies": [], "reason": "coverage_not_available"}, started)
        return envelope(coverage, started)

    @app.get("/v1/event-facts")
    def event_facts(
        company: str | None = None,
        predicate: str | None = None,
        as_of: str | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            as_of = _validated_as_of(as_of)
        except ValueError:
            raise HTTPException(status_code=422, detail="as_of_date_invalid") from None
        service = agent.evidence_service
        if not getattr(service, "overlay_database", None):
            return envelope({"facts": [], "reason": "overlay_not_configured"}, started)
        facts = _fetch_event_facts(service, company=company, predicate=predicate, as_of=as_of, limit=limit)
        return envelope({"facts": facts}, started)

    def calculation(request: CalculationRequest) -> dict[str, Any]:
        started = perf_counter()
        try:
            return envelope(calculate(request.operation, request.operands, unit=request.unit, evidence_ids=request.evidence_ids), started)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    calculation.__annotations__["request"] = CalculationRequest
    app.post("/v1/calculate")(calculation)

    def answer(request: QueryRequest) -> dict[str, Any]:
        started = perf_counter()
        return envelope(
            verified_answer(
                request.question,
                company=request.company,
                as_of=request.as_of,
                limit=request.limit,
            ),
            started,
        )

    answer.__annotations__["request"] = QueryRequest
    app.post("/v1/answer")(answer)

    class QaReviewRequest(BaseModel):
        reviewer: str = Field(min_length=1, max_length=80)
        question: str = Field(min_length=1, max_length=4000)
        answer: str = Field(min_length=1, max_length=20000)
        verdict: str = Field(min_length=1, max_length=32)
        notes: str = ""
        question_id: str | None = None
        answerable: bool | None = None
        verified: bool | None = None
        latency_ms: float | None = None
        request_id: str | None = None
        corpus_revision: str | None = None
        citations: list[dict[str, Any]] = Field(default_factory=list)
        endpoint: str | None = None

    class QaPerfRequest(BaseModel):
        recorder: str = Field(min_length=1, max_length=80)
        suite: str = Field(min_length=1, max_length=120)
        passed: int | None = None
        failed: int | None = None
        skipped: int | None = None
        p50_ms: float | None = None
        p95_ms: float | None = None
        git_commit: str | None = None
        notes: str = ""
        metrics: dict[str, Any] | None = None

    class QaMilestoneRequest(BaseModel):
        author: str = Field(min_length=1, max_length=80)
        title: str = Field(min_length=1, max_length=200)
        body: str = Field(min_length=1, max_length=8000)
        category: str = "process"

    class GoldCandidateCreateRequest(BaseModel):
        review_id: str = Field(min_length=1, max_length=64)
        annotator: str = Field(min_length=1, max_length=80)

    class GoldCandidateUpdateRequest(BaseModel):
        editor: str = Field(min_length=1, max_length=80)
        annotation: dict[str, Any]

    class GoldDecisionRequest(BaseModel):
        reviewer: str = Field(min_length=1, max_length=80)
        decision: str = Field(min_length=1, max_length=16)
        notes: str = Field(default="", max_length=8000)

    def _lab_error(exc: QaLabError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/lab/api/summary")
    def qa_summary() -> dict[str, Any]:
        return require_qa_store().summary()

    @app.get("/lab/api/reviews")
    def qa_reviews() -> dict[str, Any]:
        return {"reviews": require_qa_store().list_reviews()}

    def qa_create_review(payload: QaReviewRequest) -> dict[str, Any]:
        try:
            review = require_qa_store().add_review(**payload.model_dump())
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"review": review}

    qa_create_review.__annotations__["payload"] = QaReviewRequest
    app.post("/lab/api/reviews")(qa_create_review)

    @app.get("/lab/api/perf-runs")
    def qa_perf_runs() -> dict[str, Any]:
        return {"runs": require_qa_store().list_perf_runs()}

    def qa_create_perf(payload: QaPerfRequest) -> dict[str, Any]:
        try:
            run = require_qa_store().add_perf_run(**payload.model_dump())
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"run": run}

    qa_create_perf.__annotations__["payload"] = QaPerfRequest
    app.post("/lab/api/perf-runs")(qa_create_perf)

    @app.get("/lab/api/milestones")
    def qa_milestones() -> dict[str, Any]:
        return {"milestones": require_qa_store().list_milestones()}

    def qa_create_milestone(payload: QaMilestoneRequest) -> dict[str, Any]:
        try:
            milestone = require_qa_store().add_milestone(**payload.model_dump())
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"milestone": milestone}

    qa_create_milestone.__annotations__["payload"] = QaMilestoneRequest
    app.post("/lab/api/milestones")(qa_create_milestone)

    @app.get("/lab/api/gold-candidates")
    def qa_gold_candidates() -> dict[str, Any]:
        return {"candidates": require_qa_store().list_gold_candidates()}

    def qa_create_gold_candidate(payload: GoldCandidateCreateRequest) -> dict[str, Any]:
        try:
            candidate = require_qa_store().create_gold_candidate(**payload.model_dump())
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"candidate": candidate}

    qa_create_gold_candidate.__annotations__["payload"] = GoldCandidateCreateRequest
    app.post("/lab/api/gold-candidates")(qa_create_gold_candidate)

    def qa_update_gold_candidate(candidate_id: str, payload: GoldCandidateUpdateRequest) -> dict[str, Any]:
        try:
            candidate = require_qa_store().update_gold_candidate(
                candidate_id=candidate_id,
                **payload.model_dump(),
            )
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"candidate": candidate}

    qa_update_gold_candidate.__annotations__["payload"] = GoldCandidateUpdateRequest
    app.put("/lab/api/gold-candidates/{candidate_id}")(qa_update_gold_candidate)

    def qa_decide_gold_candidate(candidate_id: str, payload: GoldDecisionRequest) -> dict[str, Any]:
        try:
            candidate = require_qa_store().decide_gold_candidate(
                candidate_id=candidate_id,
                **payload.model_dump(),
            )
        except QaLabError as exc:
            raise _lab_error(exc) from exc
        return {"candidate": candidate}

    qa_decide_gold_candidate.__annotations__["payload"] = GoldDecisionRequest
    app.post("/lab/api/gold-candidates/{candidate_id}/decision")(qa_decide_gold_candidate)

    @app.get("/lab/export/gold.jsonl", include_in_schema=False)
    def qa_export_gold_jsonl():
        return Response(
            require_qa_store().export_gold_jsonl(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="gold_qa.approved.jsonl"'},
        )

    @app.get("/lab/source", include_in_schema=False)
    def qa_source_file(source_id: str = Query(min_length=1, max_length=200)):
        try:
            path = require_qa_store().source_path(source_id)
        except QaLabError as exc:
            status = 404 if str(exc) in {"source_not_found", "source_file_unavailable"} else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return FileResponse(path, filename=path.name)

    @app.get("/lab/export.html", include_in_schema=False)
    def qa_export_html():
        return HTMLResponse(require_qa_store().export_html())

    def hcx_function_answer(request: HcxFunctionCallingRequest) -> dict[str, Any]:
        started = perf_counter()
        health_status = runtime_health()
        if not health_status["ready"]:
            raise HTTPException(status_code=503, detail="runtime_not_ready")
        if function_calling_service is None:
            raise HTTPException(status_code=503, detail="hcx_function_calling_not_configured")
        result = function_calling_service.answer(request.question)
        return envelope(result.to_dict(), started)

    hcx_function_answer.__annotations__["request"] = HcxFunctionCallingRequest
    app.post("/v1/hcx/function-answer")(hcx_function_answer)

    def require_eval() -> tuple[QaCaseStore, QaEvaluator]:
        if not eval_enabled:
            raise HTTPException(status_code=404, detail="not_found")
        if eval_store is None or evaluator is None:
            raise HTTPException(status_code=503, detail="eval_runtime_not_ready")
        return eval_store, evaluator

    @app.get("/v1/eval/cases")
    def list_eval_cases() -> dict[str, object]:
        store, _ = require_eval()
        return {"categories": sorted(QA_CATEGORIES), "cases": store.list_cases()}

    def create_eval_case(request: EvalCaseRequest) -> dict[str, object]:
        store, _ = require_eval()
        try:
            return store.create(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    create_eval_case.__annotations__["request"] = EvalCaseRequest
    app.post("/v1/eval/cases", status_code=201)(create_eval_case)

    def update_eval_case(identifier: str, request: EvalCaseRequest) -> dict[str, object]:
        store, _ = require_eval()
        try:
            return store.update(identifier, request.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="qa_case_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    update_eval_case.__annotations__["request"] = EvalCaseRequest
    app.put("/v1/eval/cases/{identifier}")(update_eval_case)

    @app.delete("/v1/eval/cases/{identifier}", status_code=204)
    def delete_eval_case(identifier: str) -> None:
        store, _ = require_eval()
        try:
            store.delete(identifier)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="qa_case_not_found") from exc

    def run_eval(request: EvalRunRequest) -> dict[str, object]:
        store, runner = require_eval()
        cases = store.list_cases()
        selected_ids = set(request.ids)
        if request.failed_only:
            previous = store.list_results()
            failed_ids = {
                str(row.get("qa_id"))
                for row in (previous[0].get("results", []) if previous else [])
                if isinstance(row, dict) and row.get("status") == "FAIL"
            }
            selected_ids.update(failed_ids)
        if selected_ids:
            cases = [case for case in cases if str(case["id"]) in selected_ids]
        if not cases:
            raise HTTPException(status_code=400, detail="qa_cases_empty")
        return runner.run(cases)

    run_eval.__annotations__["request"] = EvalRunRequest
    app.post("/v1/eval/run")(run_eval)

    def run_eval_quick_question(request: EvalQuickQuestionRequest) -> dict[str, object]:
        _, runner = require_eval()
        return runner.run_question(request.question)

    run_eval_quick_question.__annotations__["request"] = EvalQuickQuestionRequest
    app.post("/v1/eval/quick-answer")(run_eval_quick_question)

    @app.post("/v1/eval/run/{identifier}")
    def run_one_eval(identifier: str) -> dict[str, object]:
        store, runner = require_eval()
        cases = [case for case in store.list_cases() if case["id"] == identifier]
        if not cases:
            raise HTTPException(status_code=404, detail="qa_case_not_found")
        return runner.run(cases)

    @app.get("/v1/eval/results")
    def list_eval_results() -> list[dict[str, object]]:
        store, _ = require_eval()
        return store.list_results()

    @app.get("/v1/eval/results/{run_id}")
    def get_eval_result(run_id: str) -> dict[str, object]:
        store, _ = require_eval()
        try:
            return store.get_result(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="qa_result_not_found") from exc

    return app
