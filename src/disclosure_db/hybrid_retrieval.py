"""Smoke-scoped dense, existing sparse, and RRF hybrid retrieval.

The default dense configuration is deliberately ``smoke_only`` and expects
exactly 100 vectors.  It is a wiring/integration aid, not evidence of
full-corpus retrieval quality.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .bge_m3_faiss import BgeM3SentenceEncoder, DEFAULT_MAX_LENGTH, DEFAULT_MODEL, PreparedText
from .search_index import SafeSearchIndex


DEFAULT_RRF_K = 60
DEFAULT_SMOKE_CORPUS_SIZE = 100
DEFAULT_DENSE_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "derived" / "bge_m3_dense_pilot"


class QueryEncoder(Protocol):
    model_name: str
    model_revision: str | None
    dimension: int

    def prepare_text(self, text: str, *, max_length: int) -> PreparedText: ...

    def encode(self, texts: Sequence[str], *, batch_size: int) -> object: ...


@dataclass(frozen=True, slots=True)
class DenseRetrieverConfig:
    index_path: Path = DEFAULT_DENSE_DIRECTORY / "index.faiss"
    metadata_path: Path = DEFAULT_DENSE_DIRECTORY / "chunk_metadata.jsonl"
    model_name: str = DEFAULT_MODEL
    model_revision: str | None = None
    max_length: int = DEFAULT_MAX_LENGTH
    corpus_status: str = "smoke_only"
    expected_corpus_size: int | None = DEFAULT_SMOKE_CORPUS_SIZE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DenseRetrieverConfig":
        values = os.environ if env is None else env
        expected_raw = values.get("DISCLOSURE_DENSE_EXPECTED_CORPUS_SIZE", str(DEFAULT_SMOKE_CORPUS_SIZE))
        expected = None if expected_raw.casefold() in {"", "none", "any"} else int(expected_raw)
        return cls(
            index_path=Path(values.get("DISCLOSURE_DENSE_INDEX", str(DEFAULT_DENSE_DIRECTORY / "index.faiss"))),
            metadata_path=Path(values.get("DISCLOSURE_DENSE_METADATA", str(DEFAULT_DENSE_DIRECTORY / "chunk_metadata.jsonl"))),
            model_name=values.get("DISCLOSURE_DENSE_MODEL", DEFAULT_MODEL),
            model_revision=values.get("DISCLOSURE_DENSE_MODEL_REVISION") or None,
            max_length=int(values.get("DISCLOSURE_DENSE_MAX_LENGTH", str(DEFAULT_MAX_LENGTH))),
            corpus_status=values.get("DISCLOSURE_DENSE_CORPUS_STATUS", "smoke_only"),
            expected_corpus_size=expected,
        )

    def validate(self) -> None:
        if self.max_length <= 0:
            raise ValueError("dense_max_length_must_be_positive")
        if self.expected_corpus_size is not None and self.expected_corpus_size < 0:
            raise ValueError("dense_expected_corpus_size_invalid")
        if self.corpus_status not in {"smoke_only", "full_corpus"}:
            raise ValueError("dense_corpus_status_invalid")


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[Mapping[str, object], ...]
    dense_status: str
    dense_corpus_size: int
    fallback_used: bool
    retrieval_mode: str
    smoke_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "hits": [dict(hit) for hit in self.hits],
            "dense_status": self.dense_status,
            "dense_corpus_size": self.dense_corpus_size,
            "fallback_used": self.fallback_used,
            "retrieval_mode": self.retrieval_mode,
            "smoke_only": self.smoke_only,
        }


class DenseRetrievalError(RuntimeError):
    def __init__(self, status: str, message: str, *, corpus_size: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.corpus_size = corpus_size


def _load_numpy(module: object | None) -> object:
    if module is not None:
        return module
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise DenseRetrievalError("load_failed", "numpy is not installed") from exc
    return numpy


def _load_faiss(module: object | None) -> object:
    if module is not None:
        return module
    try:
        import faiss
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise DenseRetrievalError("load_failed", "faiss is not installed") from exc
    return faiss


def _l2_normalized(vector: object, *, dimension: int, numpy: object) -> object:
    value = numpy.asarray(vector, dtype="float32")
    if getattr(value, "ndim", None) != 1 or int(value.shape[0]) != dimension:
        raise ValueError(f"dense_vector_dimension_mismatch:{getattr(value, 'shape', None)}")
    if not bool(numpy.isfinite(value).all()):
        raise ValueError("dense_vector_non_finite")
    norm = float(numpy.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("dense_vector_zero_norm")
    return value / norm


def _single_vector(value: object) -> object:
    if hasattr(value, "shape"):
        shape = tuple(int(item) for item in value.shape)
        if len(shape) != 2 or shape[0] != 1:
            raise ValueError(f"query_embedding_count_mismatch:{shape}")
        return value[0]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 1:
        raise ValueError("query_embedding_count_mismatch")
    return value[0]


def _metadata_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"dense_metadata_invalid_json:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"dense_metadata_not_object:{line_number}")
            if value.get("status") not in (None, "embedded"):
                raise ValueError(f"dense_metadata_contains_non_vector:{line_number}")
            if value.get("vector_id") != len(rows):
                raise ValueError(f"dense_metadata_vector_order_mismatch:{line_number}")
            if not value.get("chunk_id") and not value.get("evidence_id"):
                raise ValueError(f"dense_metadata_identity_missing:{line_number}")
            rows.append(value)
    return rows


def _company_matches(row: Mapping[str, object], company: str) -> bool:
    fields = ("company", "issuer_name", "listed_name", "reporter_name", "stock_code", "corp_code")
    return any(str(row.get(field) or "") == company for field in fields)


def _version_matches(row: Mapping[str, object], *, as_of: str | None, correction_policy: str) -> bool:
    if correction_policy not in {"current", "original", "corrected", "both"}:
        raise ValueError("correction_policy_invalid")
    has_version_metadata = any(
        field in row for field in ("lineage_status", "is_current", "is_correction", "effective_from", "effective_to")
    )
    # chunk-v1 smoke metadata predates version columns.  Do not invent values;
    # explicit date/version requests below fail closed when the required field
    # is absent, while an unqualified current query may still exercise wiring.
    if has_version_metadata:
        lineage = str(row.get("lineage_status") or "")
        if correction_policy == "original" and lineage != "root":
            return False
        if correction_policy != "original" and lineage not in {"root", "resolved"}:
            return False
        if correction_policy == "corrected" and not bool(row.get("is_correction")):
            return False
        if as_of is None and correction_policy not in {"original", "both"} and not bool(row.get("is_current")):
            return False
    elif correction_policy != "current":
        return False
    if as_of is not None:
        effective_from = row.get("effective_from")
        if not isinstance(effective_from, str) or not effective_from or effective_from > as_of:
            return False
        effective_to = row.get("effective_to")
        if correction_policy != "both" and effective_to not in (None, "") and as_of >= str(effective_to):
            return False
    return True


def _dense_filter_matches(
    row: Mapping[str, object],
    *,
    company: str | None,
    filing_id: str | None,
    as_of: str | None,
    filed_at: str | None,
    correction_policy: str,
) -> bool:
    if company is not None and not _company_matches(row, company):
        return False
    if filing_id is not None and str(row.get("filing_id") or "") != filing_id:
        return False
    if filed_at is not None and str(row.get("filed_at") or "") != filed_at:
        return False
    return _version_matches(row, as_of=as_of, correction_policy=correction_policy)


class DenseFaissRetriever:
    """Read a normalized IndexFlatIP pilot and return smoke-scoped hits."""

    def __init__(
        self,
        config: DenseRetrieverConfig | None = None,
        *,
        encoder: QueryEncoder | None = None,
        faiss_module: object | None = None,
        numpy_module: object | None = None,
    ) -> None:
        self.config = config or DenseRetrieverConfig.from_env()
        self.config.validate()
        self._encoder = encoder
        self._faiss_module = faiss_module
        self._numpy_module = numpy_module
        self._index: object | None = None
        self._metadata: tuple[Mapping[str, object], ...] = ()

    @property
    def corpus_size(self) -> int:
        return int(self._index.ntotal) if self._index is not None else 0

    def _ensure_loaded(self) -> tuple[object, tuple[Mapping[str, object], ...], object]:
        if self._index is not None:
            return self._index, self._metadata, _load_numpy(self._numpy_module)
        if not self.config.index_path.is_file() or not self.config.metadata_path.is_file():
            raise DenseRetrievalError("unavailable", "dense smoke index or metadata is missing")
        numpy = _load_numpy(self._numpy_module)
        faiss = _load_faiss(self._faiss_module)
        try:
            index = faiss.read_index(str(self.config.index_path))
            dimension = int(getattr(index, "d", 0))
            corpus_size = int(getattr(index, "ntotal", 0))
            if dimension <= 0 or corpus_size < 0:
                raise ValueError("dense_index_shape_invalid")
            metadata = _metadata_rows(self.config.metadata_path)
            if len(metadata) != corpus_size:
                raise ValueError("dense_index_metadata_count_mismatch")
            if self.config.expected_corpus_size is not None and corpus_size != self.config.expected_corpus_size:
                raise ValueError(
                    f"dense_expected_corpus_size_mismatch:{corpus_size}!={self.config.expected_corpus_size}"
                )
            # IndexFlatIP represents cosine similarity only when stored vectors
            # are normalized.  The current smoke index is bounded to 100 rows,
            # so validate every stored vector before admitting the index.
            for vector_id in range(corpus_size):
                vector = numpy.asarray(index.reconstruct(vector_id), dtype="float32")
                norm = float(numpy.linalg.norm(vector))
                if not math.isfinite(norm) or abs(norm - 1.0) > 1e-4:
                    raise ValueError(f"dense_index_vector_not_normalized:{vector_id}")
        except DenseRetrievalError:
            raise
        except Exception as exc:
            raise DenseRetrievalError(
                "load_failed",
                f"dense smoke index validation failed: {type(exc).__name__}: {exc}",
                corpus_size=int(getattr(locals().get("index"), "ntotal", 0)),
            ) from exc
        self._index = index
        self._metadata = tuple(metadata)
        return index, self._metadata, numpy

    def _query_encoder(self, *, dimension: int) -> QueryEncoder:
        encoder = self._encoder
        if encoder is None:
            try:
                encoder = BgeM3SentenceEncoder(
                    self.config.model_name,
                    model_revision=self.config.model_revision,
                    device="cpu",
                )
            except Exception as exc:
                raise DenseRetrievalError(
                    "query_failed",
                    f"dense query model load failed: {type(exc).__name__}: {exc}",
                    corpus_size=self.corpus_size,
                ) from exc
            self._encoder = encoder
        if encoder.model_name != self.config.model_name or encoder.model_revision != self.config.model_revision:
            raise DenseRetrievalError("query_failed", "dense query model identity mismatch", corpus_size=self.corpus_size)
        if encoder.dimension != dimension:
            raise DenseRetrievalError("query_failed", "dense query model dimension mismatch", corpus_size=self.corpus_size)
        return encoder

    def search(
        self,
        question: str,
        *,
        company: str | None = None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        correction_policy: str = "current",
        limit: int = 20,
    ) -> RetrievalResult:
        if limit <= 0:
            return RetrievalResult((), self.config.corpus_status, self.corpus_size, False, "dense_only", self.config.corpus_status == "smoke_only")
        index, metadata, numpy = self._ensure_loaded()
        dimension = int(index.d)
        encoder = self._query_encoder(dimension=dimension)
        try:
            prepared = encoder.prepare_text(question, max_length=self.config.max_length)
            raw_vector = _single_vector(encoder.encode([prepared.text], batch_size=1))
            query_vector = _l2_normalized(raw_vector, dimension=dimension, numpy=numpy)
            query_matrix = numpy.ascontiguousarray(numpy.asarray([query_vector]), dtype="float32")
            scores, identifiers = index.search(query_matrix, int(index.ntotal))
        except DenseRetrievalError:
            raise
        except Exception as exc:
            raise DenseRetrievalError(
                "query_failed",
                f"dense query failed: {type(exc).__name__}: {exc}",
                corpus_size=int(index.ntotal),
            ) from exc
        hits: list[Mapping[str, object]] = []
        for dense_rank, (raw_score, raw_identifier) in enumerate(zip(scores[0], identifiers[0]), start=1):
            vector_id = int(raw_identifier)
            if vector_id < 0 or vector_id >= len(metadata):
                continue
            row = metadata[vector_id]
            if not _dense_filter_matches(
                row,
                company=company,
                filing_id=filing_id,
                as_of=as_of,
                filed_at=filed_at,
                correction_policy=correction_policy,
            ):
                continue
            hits.append({
                **row,
                "score": float(raw_score),
                "dense_score": float(raw_score),
                "dense_rank": dense_rank,
                "retrieval_source": "dense",
                "smoke_only": self.config.corpus_status == "smoke_only",
            })
            if len(hits) >= limit:
                break
        return RetrievalResult(
            tuple(hits),
            self.config.corpus_status,
            int(index.ntotal),
            False,
            "dense_only",
            self.config.corpus_status == "smoke_only",
        )


class SparseRetriever:
    """Thin adapter over SafeSearchIndex; it does not reimplement FTS or RRF."""

    def __init__(self, search_index: SafeSearchIndex) -> None:
        self.search_index = search_index

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        base_sha256: str,
        expected_base_size: int | None = None,
    ) -> "SparseRetriever":
        return cls(SafeSearchIndex(path, base_sha256=base_sha256, expected_base_size=expected_base_size))

    def search(
        self,
        question: str,
        *,
        company: str | None = None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        correction_policy: str = "current",
        limit: int = 20,
    ) -> list[dict[str, object]]:
        return self.search_index.search(
            question,
            company=company,
            filing_id=filing_id,
            as_of=as_of,
            filed_at=filed_at,
            correction_policy=correction_policy,
            limit=limit,
        )


def _result_identity(row: Mapping[str, object]) -> str | None:
    chunk_id = row.get("chunk_id")
    if chunk_id:
        return f"chunk:{chunk_id}"
    evidence_id = row.get("evidence_id")
    if evidence_id:
        return f"evidence:{evidence_id}"
    return None


def _rrf_hybrid(
    sparse_hits: Sequence[Mapping[str, object]],
    dense_hits: Sequence[Mapping[str, object]],
    *,
    limit: int,
    rrf_k: int,
) -> list[Mapping[str, object]]:
    fused: dict[str, dict[str, object]] = {}
    for source, ranking in (("sparse", sparse_hits), ("dense", dense_hits)):
        seen_in_ranking: set[str] = set()
        for rank, raw_row in enumerate(ranking, start=1):
            identity = _result_identity(raw_row)
            if identity is None or identity in seen_in_ranking:
                continue
            seen_in_ranking.add(identity)
            if identity not in fused:
                fused[identity] = {**raw_row, "rrf_score": 0.0, "retrieval_sources": []}
            target = fused[identity]
            target["rrf_score"] = float(target["rrf_score"]) + 1.0 / (rrf_k + rank)
            target[f"{source}_rank"] = rank
            sources = list(target["retrieval_sources"])
            if source not in sources:
                sources.append(source)
            target["retrieval_sources"] = sources
    return sorted(
        fused.values(),
        key=lambda row: (-float(row["rrf_score"]), _result_identity(row) or ""),
    )[:limit]


class HybridRetriever:
    def __init__(
        self,
        sparse: SparseRetriever,
        dense: DenseFaissRetriever | None,
        *,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k_must_be_positive")
        self.sparse = sparse
        self.dense = dense
        self.rrf_k = rrf_k

    def search(
        self,
        question: str,
        *,
        company: str | None = None,
        filing_id: str | None = None,
        as_of: str | None = None,
        filed_at: str | None = None,
        correction_policy: str = "current",
        limit: int = 20,
    ) -> RetrievalResult:
        sparse_hits = self.sparse.search(
            question,
            company=company,
            filing_id=filing_id,
            as_of=as_of,
            filed_at=filed_at,
            correction_policy=correction_policy,
            limit=limit,
        )
        if self.dense is None:
            return RetrievalResult(tuple(sparse_hits[:limit]), "unavailable", 0, True, "sparse_fallback", False)
        try:
            dense_result = self.dense.search(
                question,
                company=company,
                filing_id=filing_id,
                as_of=as_of,
                filed_at=filed_at,
                correction_policy=correction_policy,
                limit=limit,
            )
        except DenseRetrievalError as exc:
            return RetrievalResult(
                tuple(sparse_hits[:limit]),
                exc.status,
                exc.corpus_size,
                True,
                "sparse_fallback",
                self.dense.config.corpus_status == "smoke_only",
            )
        except Exception:
            # Dense is optional pilot infrastructure.  Never let an unexpected
            # model/index exception suppress a valid SafeSearchIndex result.
            return RetrievalResult(
                tuple(sparse_hits[:limit]),
                "query_failed",
                self.dense.corpus_size,
                True,
                "sparse_fallback",
                self.dense.config.corpus_status == "smoke_only",
            )
        if not dense_result.hits:
            status = "smoke_only_no_filtered_results" if dense_result.smoke_only else "no_filtered_results"
            return RetrievalResult(
                tuple(sparse_hits[:limit]),
                status,
                dense_result.dense_corpus_size,
                True,
                "sparse_fallback",
                dense_result.smoke_only,
            )
        fused = _rrf_hybrid(sparse_hits, dense_result.hits, limit=limit, rrf_k=self.rrf_k)
        return RetrievalResult(
            tuple(fused),
            dense_result.dense_status,
            dense_result.dense_corpus_size,
            False,
            "hybrid_rrf",
            dense_result.smoke_only,
        )


__all__ = [
    "DEFAULT_RRF_K",
    "DEFAULT_SMOKE_CORPUS_SIZE",
    "DenseFaissRetriever",
    "DenseRetrievalError",
    "DenseRetrieverConfig",
    "HybridRetriever",
    "RetrievalResult",
    "SparseRetriever",
]
