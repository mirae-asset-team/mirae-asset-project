"""Optional FastAPI surface for the disclosure agent."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from .attestation import verify_fast_identity
from .agent import DisclosureAgent
from .agent_contracts import to_jsonable
from .calculator import calculate
from .query_planner import plan_query


def _fetch_financial_facts(
    service: Any,
    *,
    filing_id: str | None = None,
    company: str | None = None,
    account_id: str | None = None,
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
        as_of=as_of,
        limit=limit,
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
        limit: int = Field(default=20, ge=1, le=100)

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
        return envelope(_health_status(agent.evidence_service), started)

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
        facts = _fetch_financial_facts(
            service,
            filing_id=filing_id,
            company=company,
            account_id=account_id,
            as_of=as_of,
            limit=limit,
        )
        return envelope({"facts": facts}, started)

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
        return envelope(agent.answer(request.question, company=request.company, as_of=request.as_of, limit=request.limit), started)

    return app
