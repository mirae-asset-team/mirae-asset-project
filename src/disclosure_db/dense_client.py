"""Fail-closed client for the optional full-corpus dense retrieval sidecar."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from types import SimpleNamespace
from typing import Iterable, Mapping, Protocol
from urllib import error, request

from .hybrid_retrieval import RetrievalResult


@dataclass(frozen=True, slots=True)
class DenseChunkHit:
    vector_id: int
    score: float
    evidence_ids: tuple[str, ...]
    filing_id: str


class DenseSearchClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        vector_count: int = 0,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("dense_url_invalid")
        if timeout_seconds <= 0:
            raise ValueError("dense_timeout_invalid")
        if vector_count < 0:
            raise ValueError("dense_vector_count_invalid")
        self.base_url = normalized
        self.timeout_seconds = float(timeout_seconds)
        self.vector_count = int(vector_count)

    @classmethod
    def from_environment(cls) -> "DenseSearchClient | None":
        url = os.getenv("DISCLOSURE_DENSE_URL", "").strip()
        return cls(
            url,
            timeout_seconds=float(os.getenv("DISCLOSURE_DENSE_TIMEOUT_SECONDS", "5")),
            vector_count=int(os.getenv("DISCLOSURE_DENSE_VECTOR_COUNT", "0")),
        ) if url else None

    def search(
        self,
        question: str,
        *,
        limit: int = 40,
        filing_ids: Iterable[str] | None = None,
    ) -> list[DenseChunkHit]:
        if not question.strip() or not 1 <= int(limit) <= 100:
            raise ValueError("dense_query_invalid")
        allowed_filings = list(dict.fromkeys(str(item) for item in (filing_ids or ()) if str(item)))
        if len(allowed_filings) > 20_000:
            raise ValueError("dense_filing_filter_too_large")
        body = json.dumps(
            {"question": question, "limit": int(limit), "filing_ids": allowed_filings},
            ensure_ascii=False,
        ).encode("utf-8")
        call = request.Request(
            f"{self.base_url}/search",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(call, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("dense_service_unavailable") from exc
        rows = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("dense_response_invalid")
        hits: list[DenseChunkHit] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("dense_response_invalid")
            evidence = row.get("evidence_ids")
            if not isinstance(evidence, list) or not evidence:
                continue
            try:
                hit = DenseChunkHit(
                    vector_id=int(row["vector_id"]),
                    score=float(row["score"]),
                    evidence_ids=tuple(dict.fromkeys(str(item) for item in evidence if str(item))),
                    filing_id=str(row.get("filing_id") or ""),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("dense_response_invalid") from exc
            if not hit.evidence_ids:
                continue
            hits.append(hit)
        return hits


def flatten_evidence_ids(hits: Iterable[DenseChunkHit]) -> list[str]:
    return list(dict.fromkeys(
        evidence_id
        for hit in hits
        for evidence_id in hit.evidence_ids
    ))


class DenseEvidenceService(Protocol):
    dense_client: DenseSearchClient | None

    def search_dense(
        self,
        question: str,
        *,
        company: str | None,
        filing_id: str | None,
        as_of: str | None,
        filed_at: str | None,
        start_date: str | None,
        end_date: str | None,
        correction_policy: str,
        limit: int,
    ) -> tuple[list[object], Mapping[str, object]]: ...


class EvidenceServiceDenseRetriever:
    """Adapt hydrated sidecar Evidence to the existing RRF hybrid contract."""

    def __init__(self, evidence_service: DenseEvidenceService) -> None:
        self.evidence_service = evidence_service
        client = evidence_service.dense_client
        self.corpus_size = int(client.vector_count) if client is not None else 0
        self.config = SimpleNamespace(corpus_status="full_corpus")

    def search(
        self,
        question: str,
        *,
        company: str | None = None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        correction_policy: str = "current",
        limit: int = 20,
    ) -> RetrievalResult:
        refs, diagnostics = self.evidence_service.search_dense(
            question,
            company=company,
            filing_id=filing_id,
            as_of=as_of,
            filed_at=filed_at,
            start_date=start_date,
            end_date=end_date,
            correction_policy=correction_policy,
            limit=limit,
        )
        rows: list[dict[str, object]] = []
        for ref in refs:
            locator = dict(getattr(ref, "locator", {}) or {})
            rows.append({
                "evidence_id": str(getattr(ref, "evidence_id", "")),
                "filing_id": str(getattr(ref, "filing_id", "")),
                "source_id": str(getattr(ref, "source_id", "")),
                "text_normalized": str(getattr(ref, "text", "")),
                "locator": locator,
                "lineage_status": str(getattr(ref, "lineage_status", "")),
                "score": float(getattr(ref, "score", 0.0)),
                "filed_at": str(getattr(ref, "filed_at", "") or ""),
                "report_name_raw": str(getattr(ref, "report_name", "") or ""),
                "is_current": getattr(ref, "is_current", None),
                "company": company,
                "retrieval_source": "bge-m3-dense",
                "quality_status": "safe_search_admitted",
            })
        status = "full_corpus" if rows else str(diagnostics.get("dense_reason_code") or "no_filtered_results")
        return RetrievalResult(
            tuple(rows),
            status,
            self.corpus_size,
            False,
            "dense_only",
            False,
        )


__all__ = [
    "DenseChunkHit",
    "DenseSearchClient",
    "EvidenceServiceDenseRetriever",
    "flatten_evidence_ids",
]
