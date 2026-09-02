"""Fail-closed client for the optional full-corpus dense retrieval sidecar."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Iterable
from urllib import error, request


@dataclass(frozen=True, slots=True)
class DenseChunkHit:
    vector_id: int
    score: float
    evidence_ids: tuple[str, ...]
    filing_id: str


class DenseSearchClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 20.0) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("dense_url_invalid")
        self.base_url = normalized
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_environment(cls) -> "DenseSearchClient | None":
        url = os.getenv("DISCLOSURE_DENSE_URL", "").strip()
        return cls(url) if url else None

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


__all__ = ["DenseChunkHit", "DenseSearchClient", "flatten_evidence_ids"]
