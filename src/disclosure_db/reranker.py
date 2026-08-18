"""Bounded optional CLOVA Studio reranking with a local-order fallback."""

from __future__ import annotations

import json
import os
import socket
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

from .agent_contracts import EvidenceRef


@dataclass(slots=True)
class RerankResult:
    evidence_ids: list[str]
    used_provider: bool
    reason_codes: list[str]


class ClovaReranker:
    """Small standard-library adapter for the CLOVA Studio reranker API."""

    url = "https://clovastudio.stream.ntruss.com/v1/api-tools/reranker"

    def __init__(self, *, api_key: str | None = None, timeout: float = 5.0):
        self.api_key = api_key or os.getenv("CLOVASTUDIO_API_KEY")
        self.timeout = timeout

    def rerank(
        self,
        question: str,
        evidence: Sequence[EvidenceRef],
        *,
        limit: int = 8,
    ) -> RerankResult:
        final_limit = max(0, min(int(limit), 8))
        fallback = [item.evidence_id for item in evidence[:final_limit]]
        if not self.api_key:
            return RerankResult(fallback, False, ["reranker_not_configured"])

        bounded = list(evidence[:30])
        payload = {
            "query": question,
            "maxTokens": 512,
            "documents": [
                {"id": item.evidence_id, "doc": item.text[:1200]}
                for item in bounded
            ],
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response_bytes, failure = self._request(request)
        if failure is not None:
            return RerankResult(fallback, False, [failure])
        try:
            parsed = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return RerankResult(fallback, False, ["reranker_invalid_json"])

        cited = self._cited_ids(parsed)
        if cited is None:
            return RerankResult(fallback, False, ["reranker_invalid_response"])
        known_ids = {item.evidence_id for item in bounded}
        selected: list[str] = []
        seen: set[str] = set()
        for evidence_id in cited:
            if evidence_id in known_ids and evidence_id not in seen:
                selected.append(evidence_id)
                seen.add(evidence_id)
            if len(selected) >= final_limit:
                break
        if not selected:
            return RerankResult(fallback, False, ["reranker_invalid_response"])
        return RerankResult(selected, True, [])

    def _request(self, request: urllib.request.Request) -> tuple[bytes, str | None]:
        retried = False
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = self._response_status(response)
                    if status is not None and (status == 429 or status >= 500):
                        if not retried:
                            retried = True
                            continue
                        return b"", "reranker_http_error"
                    if status is not None and status >= 400:
                        return b"", "reranker_http_error"
                    return response.read(), None
            except urllib.error.HTTPError as error:
                if (error.code == 429 or error.code >= 500) and not retried:
                    retried = True
                    continue
                return b"", "reranker_http_error"
            except (socket.timeout, TimeoutError):
                return b"", "reranker_timeout"
            except urllib.error.URLError as error:
                if isinstance(error.reason, (socket.timeout, TimeoutError)):
                    return b"", "reranker_timeout"
                return b"", "reranker_network_error"
            except OSError:
                return b"", "reranker_network_error"

    @staticmethod
    def _response_status(response: object) -> int | None:
        status = getattr(response, "status", None)
        if isinstance(status, int):
            return status
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            try:
                status = getcode()
            except (AttributeError, OSError):
                return None
            return status if isinstance(status, int) else None
        return None

    @staticmethod
    def _cited_ids(payload: object) -> list[str] | None:
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        cited = result.get("citedDocuments")
        if not isinstance(cited, list) or not cited:
            return None
        ids: list[str] = []
        for item in cited:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                return None
            ids.append(item["id"])
        return ids
