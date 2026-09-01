"""Optional FastAPI surface for the disclosure agent."""

from __future__ import annotations

import sqlite3
import os
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
from .query_planner import plan_query


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
    if search_configured and base_attested and attestation_configured and Path(search_database).exists():
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
):
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover - depends on optional deployment extra
        raise RuntimeError("FastAPI is optional; install miraeasset-disclosure-db[agent]") from exc

    class QueryRequest(BaseModel):
        question: str = Field(min_length=1)
        company: str | None = None
        as_of: str | None = None
        limit: int = Field(default=20, ge=1, le=100)

    class HcxFunctionCallingRequest(BaseModel):
        question: str = Field(min_length=1, max_length=2000)

    class EvalCaseRequest(BaseModel):
        id: str = Field(min_length=3, max_length=100)
        question: str = Field(min_length=1, max_length=2000)
        category: str
        expected: dict[str, Any] = Field(default_factory=dict)
        forbidden_phrases: list[str] = Field(default_factory=list)

    class EvalRunRequest(BaseModel):
        ids: list[str] = Field(default_factory=list)
        failed_only: bool = False

    class ContestQueryRequest(BaseModel):
        question_id: str | None = Field(default=None, max_length=200)
        question: str = Field(min_length=1, max_length=4000)
        company: str | None = Field(default=None, max_length=200)
        as_of: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$")
        limit: int = Field(default=20, ge=1, le=100)

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
    startup_health: dict[str, Any] | None = None

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
        question: str = Query(min_length=1, max_length=4000),
        company: str | None = Query(default=None, max_length=200),
        as_of: str | None = Query(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$"),
    ) -> dict[str, str]:
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
        return envelope(agent.answer(request.question, company=request.company, as_of=request.as_of, limit=request.limit), started)

    answer.__annotations__["request"] = QueryRequest
    app.post("/v1/answer")(answer)

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
