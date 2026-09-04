"""Fail-closed client for the optional full-corpus dense retrieval sidecar."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Mapping, Protocol
from urllib import error, request

from .freeform_evaluation import decide_embedding_pilot, semantic_summary_sha256
from .hybrid_retrieval import RetrievalResult


DENSE_ADOPTION_SCHEMA_VERSION = "dense-adoption-v2"
DENSE_MINIMUM_GAIN = 0.05
DENSE_MAXIMUM_P95_MS = 2000.0
# Git-tracked trust anchor for data/derived/freeform_retrieval_summary.json.
# A new Dense evaluation must update the summary and this digest in one reviewed commit.
TRUSTED_DENSE_EVALUATION_SHA256 = "54c29b6c8da3b5d099e2995959df7209d176d643ca07edb97cc17af3ac263de8"
DENSE_RUNTIME_IDENTITY_FIELDS = (
    "runtime_manifest_schema_version",
    "python_version",
    "numpy_version",
    "faiss_version",
    "model",
    "model_revision",
    "dimension",
    "vector_count",
    "index_type",
    "metric",
    "normalized",
    "dense_manifest_sha256",
    "faiss_index_sha256",
    "chunk_metadata_sha256",
    "model_identity_sha256",
)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_identity_is_valid(identity: Mapping[str, object], *, vector_count: int) -> bool:
    if set(identity) != set(DENSE_RUNTIME_IDENTITY_FIELDS):
        return False
    if (
        type(identity.get("dimension")) is not int
        or int(identity["dimension"]) <= 0
        or type(identity.get("vector_count")) is not int
        or int(identity["vector_count"]) != vector_count
        or identity.get("normalized") is not True
    ):
        return False
    for name in (
        "runtime_manifest_schema_version", "python_version", "numpy_version",
        "faiss_version", "model", "model_revision", "index_type", "metric",
    ):
        if not isinstance(identity.get(name), str) or not str(identity[name]).strip():
            return False
    for name in (
        "dense_manifest_sha256", "faiss_index_sha256",
        "chunk_metadata_sha256", "model_identity_sha256",
    ):
        value = identity.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value.casefold())
        ):
            return False
    return True


def _load_adopted_evaluation(
    path: Path,
    *,
    expected_file_sha256: str,
    expected_semantic_sha256: str,
    runtime_identity: Mapping[str, object],
) -> dict[str, object] | None:
    try:
        if _sha256_file(path) != expected_file_sha256:
            return None
        summary = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or summary.get("status") != "ok":
            return None
        if summary.get("semantic_sha256") != expected_semantic_sha256:
            return None
        if semantic_summary_sha256(summary) != expected_semantic_sha256:
            return None
        metrics = summary.get("metrics")
        if not isinstance(metrics, dict):
            return None
        dense_pilot = metrics.get("dense_pilot")
        if (
            not isinstance(dense_pilot, dict)
            or dense_pilot.get("runtime_identity") != dict(runtime_identity)
        ):
            return None
        decision = decide_embedding_pilot(metrics)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
        return None
    return decision if decision.get("status") == "ADOPTED" else None


def load_dense_adoption_artifact(
    path: Path,
    *,
    base_sha256: str,
    base_size_bytes: int,
    corpus_revision: str,
    dense_url: str,
    dense_vector_count: int,
    evaluation_summary_path: Path,
    runtime_identity: Mapping[str, object],
    trusted_evaluation_sha256: str,
) -> dict[str, object] | None:
    """Load an adopted Dense decision only when metrics and serving identity match."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if set(payload) != {
        "schema_version",
        "status",
        "identity",
        "evaluation",
        "runtime_identity",
        "measured_gain",
        "wrong_issuer_count",
        "wrong_version_count",
        "p95_ms",
    }:
        return None
    identity = payload.get("identity")
    evaluation = payload.get("evaluation")
    declared_runtime_identity = payload.get("runtime_identity")
    if (
        payload.get("schema_version") != DENSE_ADOPTION_SCHEMA_VERSION
        or payload.get("status") != "ADOPTED"
        or not isinstance(identity, dict)
        or identity != {
            "base_sha256": base_sha256,
            "base_size_bytes": base_size_bytes,
            "corpus_revision": corpus_revision,
            "dense_url": dense_url,
            "dense_vector_count": dense_vector_count,
        }
        or not isinstance(evaluation, dict)
        or set(evaluation) != {"summary_sha256", "semantic_sha256"}
        or not isinstance(declared_runtime_identity, dict)
        or not _runtime_identity_is_valid(runtime_identity, vector_count=dense_vector_count)
        or declared_runtime_identity != dict(runtime_identity)
    ):
        return None
    summary_sha256 = evaluation.get("summary_sha256")
    semantic_sha256 = evaluation.get("semantic_sha256")
    if (
        not isinstance(summary_sha256, str)
        or len(summary_sha256) != 64
        or summary_sha256.casefold() != trusted_evaluation_sha256.casefold()
        or not isinstance(semantic_sha256, str)
        or len(semantic_sha256) != 64
    ):
        return None
    decision = _load_adopted_evaluation(
        Path(evaluation_summary_path),
        expected_file_sha256=summary_sha256.casefold(),
        expected_semantic_sha256=semantic_sha256.casefold(),
        runtime_identity=runtime_identity,
    )
    if decision is None:
        return None
    measured_gain = _finite_number(payload.get("measured_gain"))
    p95_ms = _finite_number(payload.get("p95_ms"))
    wrong_issuer = payload.get("wrong_issuer_count")
    wrong_version = payload.get("wrong_version_count")
    if (
        measured_gain is None
        or measured_gain < DENSE_MINIMUM_GAIN
        or p95_ms is None
        or p95_ms < 0
        or p95_ms > DENSE_MAXIMUM_P95_MS
        or type(wrong_issuer) is not int
        or type(wrong_version) is not int
        or wrong_issuer != 0
        or wrong_version != 0
        or abs(measured_gain - float(decision.get("measured_gain", math.inf))) > 1e-12
        or wrong_issuer != decision.get("dense_wrong_issuer_count")
        or wrong_version != decision.get("dense_wrong_version_count")
        or abs(p95_ms - float(decision.get("dense_p95_ms", math.inf))) > 1e-12
    ):
        return None
    return payload


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
    def from_environment(
        cls,
        *,
        base_sha256: str | None = None,
        base_size_bytes: int | None = None,
        corpus_revision: str | None = None,
    ) -> "DenseSearchClient | None":
        url = os.getenv("DISCLOSURE_DENSE_URL", "").strip()
        artifact_path = os.getenv("DISCLOSURE_DENSE_ADOPTION_ARTIFACT", "").strip()
        evaluation_path = os.getenv("DISCLOSURE_DENSE_EVALUATION_SUMMARY", "").strip()
        if not all((url, artifact_path, evaluation_path, base_sha256, corpus_revision)) or base_size_bytes is None:
            return None
        if not Path(artifact_path).is_file() or not Path(evaluation_path).is_file():
            return None
        try:
            timeout_seconds = float(os.getenv("DISCLOSURE_DENSE_TIMEOUT_SECONDS", "5"))
            vector_count = int(os.getenv("DISCLOSURE_DENSE_VECTOR_COUNT", "0"))
            client = cls(url, timeout_seconds=timeout_seconds, vector_count=vector_count)
        except (TypeError, ValueError):
            return None
        try:
            runtime_identity = client.health_identity()
            adopted = load_dense_adoption_artifact(
                Path(artifact_path),
                base_sha256=base_sha256,
                base_size_bytes=base_size_bytes,
                corpus_revision=corpus_revision,
                dense_url=client.base_url,
                dense_vector_count=client.vector_count,
                evaluation_summary_path=Path(evaluation_path),
                runtime_identity=runtime_identity,
                trusted_evaluation_sha256=TRUSTED_DENSE_EVALUATION_SHA256,
            )
        except (RuntimeError, ValueError, OSError):
            return None
        return client if adopted is not None else None

    def health_identity(self) -> dict[str, object]:
        call = request.Request(
            f"{self.base_url}/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with request.urlopen(call, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("dense_service_unavailable") from exc
        if not isinstance(payload, dict) or payload.get("ready") is not True:
            raise RuntimeError("dense_health_invalid")
        identity = {name: payload.get(name) for name in DENSE_RUNTIME_IDENTITY_FIELDS}
        if not _runtime_identity_is_valid(identity, vector_count=self.vector_count):
            raise RuntimeError("dense_health_identity_invalid")
        return identity

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
    "DENSE_ADOPTION_SCHEMA_VERSION",
    "DENSE_RUNTIME_IDENTITY_FIELDS",
    "TRUSTED_DENSE_EVALUATION_SHA256",
    "DenseChunkHit",
    "DenseSearchClient",
    "EvidenceServiceDenseRetriever",
    "flatten_evidence_ids",
    "load_dense_adoption_artifact",
]
