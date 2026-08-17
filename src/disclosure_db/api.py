"""Optional FastAPI surface for the disclosure agent."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from .agent import DisclosureAgent
from .agent_contracts import to_jsonable
from .calculator import calculate
from .query_planner import plan_query


def create_app(agent: DisclosureAgent):
    try:
        from fastapi import FastAPI, HTTPException, Query
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover - depends on optional deployment extra
        raise RuntimeError("FastAPI is optional; install miraeasset-disclosure-db[agent]") from exc

    class QueryRequest(BaseModel):
        question: str = Field(min_length=1)
        company: str | None = None
        as_of: str | None = None

    class SearchRequest(QueryRequest):
        limit: int = Field(default=20, ge=1, le=100)

    class CalculationRequest(BaseModel):
        operation: str
        operands: list[str]
        unit: str | None = None
        evidence_ids: list[str] = Field(default_factory=list)

    app = FastAPI(title="Mirae Asset Disclosure Agent", version="0.3.0")

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
        service = agent.evidence_service
        base_exists = service.base_database.exists()
        overlay_configured = bool(service.overlay_database)
        overlay_attested = bool(overlay_configured and service.overlay_database and service.overlay_database.exists())
        if overlay_attested:
            from .financial_overlay import overlay_matches_base
            overlay_attested = overlay_matches_base(service.base_database, service.overlay_database)  # type: ignore[arg-type]
        ready = base_exists and (not overlay_configured or overlay_attested)
        return envelope({"status": "ok" if ready else "degraded", "ready": ready, "overlay_configured": overlay_configured, "overlay_attested": overlay_attested}, started)

    @app.post("/v1/query/plan")
    def query_plan(request: QueryRequest) -> dict[str, Any]:
        started = perf_counter()
        plan = plan_query(request.question, company_candidates=agent.evidence_service.company_candidates(), company_hint=request.company, as_of=request.as_of)
        return envelope(plan, started)

    @app.post("/v1/evidence/search")
    def evidence_search(request: SearchRequest) -> dict[str, Any]:
        started = perf_counter()
        plan = plan_query(request.question, company_candidates=agent.evidence_service.company_candidates(), company_hint=request.company, as_of=request.as_of)
        return envelope(agent.evidence_service.search(plan, limit=request.limit), started)

    @app.get("/v1/financial-facts")
    def financial_facts(filing_id: str | None = None, company: str | None = None, account_id: str | None = None, as_of: str | None = None, limit: int = Query(default=100, ge=1, le=100)) -> dict[str, Any]:
        started = perf_counter()
        service = agent.evidence_service
        if not getattr(service, "overlay_database", None):
            return envelope({"facts": [], "reason": "overlay_not_configured"}, started)
        from .financial_overlay import fetch_overlay_facts
        facts = fetch_overlay_facts(service.base_database, service.overlay_database, filing_id=filing_id, company=company, account_id=account_id, as_of=as_of, limit=limit)
        return envelope({"facts": facts}, started)

    @app.post("/v1/calculate")
    def calculation(request: CalculationRequest) -> dict[str, Any]:
        started = perf_counter()
        try:
            return envelope(calculate(request.operation, request.operands, unit=request.unit, evidence_ids=request.evidence_ids), started)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/answer")
    def answer(request: QueryRequest) -> dict[str, Any]:
        started = perf_counter()
        return envelope(agent.answer(request.question, company=request.company, as_of=request.as_of), started)

    return app
