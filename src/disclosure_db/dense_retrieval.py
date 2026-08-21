"""Bounded dense-pilot contracts; no provider or model is bundled."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable, Mapping, Sequence

from .freeform_evaluation import canonical_sha256


MAX_DENSE_FRAGMENTS = 20_000


@dataclass(frozen=True, slots=True)
class DenseHit:
    evidence_id: str
    filing_id: str
    issuer_corp_code: str
    content_sha256: str
    score: float


def _vector(value: object, *, dimension: int, reason: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != dimension:
        raise ValueError(reason)
    vector = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in vector):
        raise ValueError(reason)
    if math.sqrt(sum(item * item for item in vector)) == 0:
        raise ValueError(reason)
    return vector


class DenseRetriever:
    """Search a prebuilt pilot only after strict metadata and manifest checks."""

    def __init__(
        self,
        records: Iterable[Mapping[str, object]],
        *,
        model: str,
        manifest: Mapping[str, object],
        trusted_identity: Mapping[str, object],
        known_evidence_ids: set[str],
        query_embedder: Callable[[str], Sequence[float]],
    ) -> None:
        if manifest.get("model") != model:
            raise ValueError("model_manifest_mismatch")
        dimension = manifest.get("dimension")
        if not isinstance(dimension, int) or dimension <= 0:
            raise ValueError("dimension_manifest_invalid")
        rows = [dict(record) for record in records]
        if manifest.get("fragment_count") != len(rows):
            raise ValueError("fragment_count_manifest_mismatch")
        if manifest.get("fragment_cap") != MAX_DENSE_FRAGMENTS:
            raise ValueError("fragment_cap_manifest_invalid")
        trusted_fields = ("corpus_sha256", "search_index_sha256", "known_evidence_sha256")
        if any(manifest.get(field) != trusted_identity.get(field) for field in trusted_fields):
            raise ValueError("trusted_identity_mismatch")
        if canonical_sha256(sorted(known_evidence_ids)) != trusted_identity.get("known_evidence_sha256"):
            raise ValueError("known_evidence_identity_mismatch")
        if canonical_sha256(rows) != manifest.get("records_sha256"):
            raise ValueError("records_manifest_mismatch")
        record_ids = [str(row.get("evidence_id", "")) for row in rows]
        manifest_ids = manifest.get("evidence_ids")
        if (
            not isinstance(manifest_ids, list)
            or sorted(record_ids) != sorted(str(item) for item in manifest_ids)
            or len(record_ids) != len(set(record_ids))
        ):
            raise ValueError("unknown_evidence")
        if not set(record_ids).issubset(known_evidence_ids):
            raise ValueError("unknown_corpus_evidence")
        metadata = [
            {key: row.get(key) for key in ("evidence_id", "filing_id", "issuer_corp_code", "content_sha256")}
            for row in rows
        ]
        if canonical_sha256(metadata) != manifest.get("input_metadata_sha256"):
            raise ValueError("input_metadata_manifest_mismatch")
        normalized: list[dict[str, object]] = []
        for row in rows:
            required = ("evidence_id", "filing_id", "issuer_corp_code", "content_sha256", "vector")
            if any(not row.get(field) for field in required):
                raise ValueError("dense_record_invalid")
            content_sha256 = str(row["content_sha256"])
            if len(content_sha256) != 64:
                raise ValueError("content_hash_invalid")
            row["vector"] = _vector(row["vector"], dimension=dimension, reason="record_dimension_mismatch")
            normalized.append(row)
        self._records = tuple(normalized)
        self._dimension = dimension
        self._query_embedder = query_embedder

    def search(
        self,
        query: str,
        *,
        issuer_corp_code: str,
        filing_ids: tuple[str, ...],
        limit: int,
    ) -> list[DenseHit]:
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit_out_of_range")
        if not query or not issuer_corp_code or not filing_ids:
            raise ValueError("dense_query_invalid")
        allowed_filings = set(filing_ids)
        candidates = [
            row for row in self._records
            if row["issuer_corp_code"] == issuer_corp_code and row["filing_id"] in allowed_filings
        ]
        query_vector = _vector(
            self._query_embedder(query),
            dimension=self._dimension,
            reason="query_dimension_mismatch",
        )
        query_norm = math.sqrt(sum(item * item for item in query_vector))
        hits: list[DenseHit] = []
        for row in candidates:
            vector = row["vector"]
            vector_norm = math.sqrt(sum(item * item for item in vector))  # type: ignore[arg-type]
            score = sum(left * right for left, right in zip(query_vector, vector)) / (query_norm * vector_norm)  # type: ignore[arg-type]
            hits.append(DenseHit(
                evidence_id=str(row["evidence_id"]),
                filing_id=str(row["filing_id"]),
                issuer_corp_code=str(row["issuer_corp_code"]),
                content_sha256=str(row["content_sha256"]),
                score=score,
            ))
        hits.sort(key=lambda hit: (-hit.score, hit.evidence_id))
        return hits[:limit]


def build_dense_pilot(
    fragments: Iterable[Mapping[str, object]],
    *,
    embedder: Callable[[list[str]], list[list[float]]],
    model: str,
    dimension: int,
    max_fragments: int = 20_000,
    trusted_identity: Mapping[str, object],
    known_evidence_ids: set[str],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build bounded vector records while excluding source text from the manifest."""

    rows = [dict(fragment) for fragment in fragments]
    if len(rows) > min(max_fragments, MAX_DENSE_FRAGMENTS):
        raise ValueError("fragment_cap_exceeded")
    if not rows:
        raise ValueError("no_dense_fragments")
    rows.sort(key=lambda row: str(row.get("evidence_id", "")))
    evidence_ids = [str(row.get("evidence_id", "")) for row in rows]
    if any(not evidence_id for evidence_id in evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("dense_evidence_ids_invalid")
    if not set(evidence_ids).issubset(known_evidence_ids):
        raise ValueError("unknown_corpus_evidence")
    for field in ("corpus_sha256", "search_index_sha256"):
        if not isinstance(trusted_identity.get(field), str) or len(str(trusted_identity[field])) != 64:
            raise ValueError("trusted_identity_invalid")
    texts = [str(row.get("text", "")) for row in rows]
    if any(not text for text in texts):
        raise ValueError("dense_text_missing")
    vectors = embedder(texts)
    if len(vectors) != len(rows):
        raise ValueError("embedding_count_mismatch")
    records: list[dict[str, object]] = []
    for row, vector in zip(rows, vectors):
        content_sha256 = str(row.get("content_sha256", ""))
        if len(content_sha256) != 64:
            raise ValueError("content_hash_invalid")
        records.append({
            "evidence_id": str(row["evidence_id"]),
            "filing_id": str(row["filing_id"]),
            "issuer_corp_code": str(row["issuer_corp_code"]),
            "content_sha256": content_sha256,
            "vector": list(_vector(vector, dimension=dimension, reason="record_dimension_mismatch")),
        })
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "model": model,
        "dimension": dimension,
        "fragment_count": len(records),
        "fragment_cap": MAX_DENSE_FRAGMENTS,
        "corpus_sha256": str(trusted_identity["corpus_sha256"]),
        "search_index_sha256": str(trusted_identity["search_index_sha256"]),
        "known_evidence_sha256": canonical_sha256(sorted(known_evidence_ids)),
        "evidence_ids": evidence_ids,
        "input_metadata_sha256": canonical_sha256([
            {key: row.get(key) for key in ("evidence_id", "filing_id", "issuer_corp_code", "content_sha256")}
            for row in rows
        ]),
        "records_sha256": canonical_sha256(records),
    }
    return records, manifest
