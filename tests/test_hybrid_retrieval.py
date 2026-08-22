from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from disclosure_db.bge_m3_faiss import PreparedText
from disclosure_db.hybrid_retrieval import (
    DenseFaissRetriever,
    DenseRetrieverConfig,
    HybridRetriever,
    SparseRetriever,
)


class _FakeIndexFlatIP:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = np.asarray(vectors, dtype="float32")
        self.d = int(self.vectors.shape[1])
        self.ntotal = int(self.vectors.shape[0])
        self.last_query: np.ndarray | None = None

    def reconstruct(self, vector_id: int) -> np.ndarray:
        return self.vectors[vector_id].copy()

    def search(self, query: object, limit: int) -> tuple[np.ndarray, np.ndarray]:
        matrix = np.asarray(query, dtype="float32")
        self.last_query = matrix.copy()
        scores = matrix @ self.vectors.T
        identifiers = np.argsort(-scores, axis=1, kind="stable")[:, :limit]
        ordered_scores = np.take_along_axis(scores, identifiers, axis=1)
        return ordered_scores.astype("float32"), identifiers.astype("int64")


class _FakeFaiss:
    def __init__(self, index: _FakeIndexFlatIP) -> None:
        self.index = index

    def read_index(self, _path: str) -> _FakeIndexFlatIP:
        return self.index


class _FakeEncoder:
    model_name = "BAAI/bge-m3"
    model_revision = None

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.dimension = len(vector)
        self.calls: list[list[str]] = []

    def prepare_text(self, text: str, *, max_length: int) -> PreparedText:
        tokens = text.split()
        return PreparedText(text, len(tokens), min(len(tokens), max_length), len(tokens) > max_length)

    def encode(self, texts: list[str], *, batch_size: int) -> object:
        self.calls.append(list(texts))
        return np.asarray([self.vector for _ in texts], dtype="float32")


class _FakeSafeSearchIndex:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, object]] = []

    def search(self, question: str, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append({"question": question, **kwargs})
        return [dict(row) for row in self.rows]


def _metadata_row(
    vector_id: int,
    *,
    chunk_id: str | None = None,
    evidence_id: str | None = None,
    filing_id: str = "filing-1",
    company: str | None = None,
    filed_at: str | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
    is_current: bool | None = None,
    lineage_status: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "status": "embedded",
        "vector_id": vector_id,
        "chunk_id": chunk_id or f"chunk-{vector_id}",
        "filing_id": filing_id,
        "evidence_ids": [evidence_id] if evidence_id else [],
    }
    optional = {
        "evidence_id": evidence_id,
        "company": company,
        "filed_at": filed_at,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "is_current": is_current,
        "lineage_status": lineage_status,
    }
    row.update({key: value for key, value in optional.items() if value is not None})
    return row


def _write_metadata(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def _dense(
    root: Path,
    vectors: list[list[float]],
    rows: list[dict[str, object]],
    *,
    encoder_vector: list[float] | None = None,
) -> tuple[DenseFaissRetriever, _FakeIndexFlatIP]:
    index_path = root / "index.faiss"
    metadata_path = root / "chunk_metadata.jsonl"
    index_path.write_bytes(b"fake-faiss")
    _write_metadata(metadata_path, rows)
    index = _FakeIndexFlatIP(vectors)
    config = DenseRetrieverConfig(
        index_path=index_path,
        metadata_path=metadata_path,
        expected_corpus_size=len(vectors),
        corpus_status="smoke_only",
    )
    retriever = DenseFaissRetriever(
        config,
        encoder=_FakeEncoder(encoder_vector or [1.0, 0.0]),
        faiss_module=_FakeFaiss(index),
        numpy_module=np,
    )
    return retriever, index


class HybridRetrievalTests(unittest.TestCase):
    def test_dense_config_paths_and_model_are_environment_overridable(self) -> None:
        config = DenseRetrieverConfig.from_env({
            "DISCLOSURE_DENSE_INDEX": "D:/pilot/custom.faiss",
            "DISCLOSURE_DENSE_METADATA": "D:/pilot/custom.jsonl",
            "DISCLOSURE_DENSE_MODEL": "D:/models/bge-m3",
            "DISCLOSURE_DENSE_MODEL_REVISION": "revision-1",
            "DISCLOSURE_DENSE_MAX_LENGTH": "512",
            "DISCLOSURE_DENSE_CORPUS_STATUS": "smoke_only",
            "DISCLOSURE_DENSE_EXPECTED_CORPUS_SIZE": "100",
        })

        self.assertEqual(config.index_path, Path("D:/pilot/custom.faiss"))
        self.assertEqual(config.metadata_path, Path("D:/pilot/custom.jsonl"))
        self.assertEqual(config.model_name, "D:/models/bge-m3")
        self.assertEqual(config.model_revision, "revision-1")
        self.assertEqual(config.max_length, 512)
        self.assertEqual(config.expected_corpus_size, 100)

    def test_dense_top_k_uses_normalized_inner_product_and_score_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            retriever, index = _dense(
                root,
                [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]],
                [_metadata_row(i) for i in range(3)],
                encoder_vector=[10.0, 0.0],
            )

            result = retriever.search("질문", limit=3)

            self.assertEqual([hit["chunk_id"] for hit in result.hits], ["chunk-0", "chunk-1", "chunk-2"])
            self.assertEqual([round(float(hit["dense_score"]), 6) for hit in result.hits], [1.0, 0.8, 0.0])
            self.assertAlmostEqual(float(np.linalg.norm(index.last_query[0])), 1.0, places=6)
            self.assertEqual(result.dense_status, "smoke_only")
            self.assertEqual(result.dense_corpus_size, 3)
            self.assertTrue(result.smoke_only)

    def test_dense_metadata_filters_company_filing_and_period_before_top_k(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            retriever, _index = _dense(
                root,
                [[1.0, 0.0], [0.0, 1.0]],
                [
                    _metadata_row(
                        0,
                        filing_id="filing-a",
                        company="테스트",
                        filed_at="2024-03-01",
                        effective_from="2024-03-01",
                        is_current=True,
                        lineage_status="root",
                    ),
                    _metadata_row(
                        1,
                        filing_id="filing-b",
                        company="다른회사",
                        filed_at="2024-04-01",
                        effective_from="2024-04-01",
                        is_current=True,
                        lineage_status="root",
                    ),
                ],
            )

            matching = retriever.search(
                "질문",
                company="테스트",
                filing_id="filing-a",
                filed_at="2024-03-01",
                as_of="2024-03-15",
            )
            range_matching = retriever.search(
                "질문", start_date="2024-02-01", end_date="2024-03-31",
                correction_policy="both",
            )
            range_excluded = retriever.search(
                "질문", start_date="2024-04-02", end_date="2024-12-31",
                correction_policy="both",
            )
            wrong_company = retriever.search("질문", company="없는회사")
            wrong_filing = retriever.search("질문", filing_id="missing")
            wrong_period = retriever.search("질문", filed_at="2024-12-31")

            self.assertEqual([hit["chunk_id"] for hit in matching.hits], ["chunk-0"])
            self.assertEqual([hit["chunk_id"] for hit in range_matching.hits], ["chunk-0"])
            self.assertEqual(range_excluded.hits, ())
            self.assertEqual(wrong_company.hits, ())
            self.assertEqual(wrong_filing.hits, ())
            self.assertEqual(wrong_period.hits, ())

    def test_sparse_retriever_delegates_to_safe_search_with_all_filters(self) -> None:
        safe = _FakeSafeSearchIndex([{"evidence_id": "evidence-1"}])
        retriever = SparseRetriever(safe)  # type: ignore[arg-type]

        rows = retriever.search(
            "계약 질문",
            company="테스트",
            filing_id="filing-1",
            as_of="2024-03-15",
            filed_at="2024-03-01",
            start_date="2024-01-01",
            end_date="2024-12-31",
            correction_policy="original",
            limit=7,
        )

        self.assertEqual(rows, [{"evidence_id": "evidence-1"}])
        self.assertEqual(safe.calls, [{
            "question": "계약 질문",
            "company": "테스트",
            "filing_id": "filing-1",
            "as_of": "2024-03-15",
            "filed_at": "2024-03-01",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "correction_policy": "original",
            "limit": 7,
        }])

    def test_hybrid_rrf_combines_rankings_and_deduplicates_by_chunk_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dense, _index = _dense(
                root,
                [[1.0, 0.0], [0.0, 1.0]],
                [
                    _metadata_row(0, chunk_id="shared", evidence_id="evidence-shared"),
                    _metadata_row(1, chunk_id="dense-only"),
                ],
            )
            safe = _FakeSafeSearchIndex([
                {"chunk_id": "shared", "evidence_id": "evidence-shared"},
                {"chunk_id": "shared", "evidence_id": "duplicate-row"},
                {"evidence_id": "sparse-only"},
            ])
            hybrid = HybridRetriever(SparseRetriever(safe), dense)  # type: ignore[arg-type]

            result = hybrid.search("질문", limit=5)

            identities = [hit.get("chunk_id") or hit.get("evidence_id") for hit in result.hits]
            self.assertEqual(identities.count("shared"), 1)
            self.assertEqual(identities[0], "shared")
            self.assertEqual(set(result.hits[0]["retrieval_sources"]), {"sparse", "dense"})
            self.assertAlmostEqual(float(result.hits[0]["rrf_score"]), 2.0 / 61.0)
            self.assertEqual(result.retrieval_mode, "hybrid_rrf")
            self.assertFalse(result.fallback_used)

    def test_missing_dense_index_falls_back_without_breaking_sparse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = _FakeSafeSearchIndex([{"evidence_id": "sparse-1"}])
            dense = DenseFaissRetriever(
                DenseRetrieverConfig(
                    index_path=root / "missing.faiss",
                    metadata_path=root / "missing.jsonl",
                    expected_corpus_size=100,
                ),
                encoder=_FakeEncoder([1.0, 0.0]),
                faiss_module=_FakeFaiss(_FakeIndexFlatIP([[1.0, 0.0]])),
                numpy_module=np,
            )
            hybrid = HybridRetriever(SparseRetriever(safe), dense)  # type: ignore[arg-type]

            result = hybrid.search("질문")

            self.assertEqual([hit["evidence_id"] for hit in result.hits], ["sparse-1"])
            self.assertEqual(result.dense_status, "unavailable")
            self.assertTrue(result.fallback_used)
            self.assertEqual(result.retrieval_mode, "sparse_fallback")
            self.assertTrue(result.smoke_only)
            self.assertEqual(len(safe.calls), 1)

    def test_invalid_dense_index_falls_back_to_sparse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dense, _index = _dense(
                root,
                [[2.0, 0.0]],
                [_metadata_row(0)],
            )
            safe = _FakeSafeSearchIndex([{"evidence_id": "sparse-1"}])

            result = HybridRetriever(SparseRetriever(safe), dense).search("질문")  # type: ignore[arg-type]

            self.assertEqual([hit["evidence_id"] for hit in result.hits], ["sparse-1"])
            self.assertEqual(result.dense_status, "load_failed")
            self.assertEqual(result.dense_corpus_size, 1)
            self.assertTrue(result.fallback_used)
            self.assertTrue(result.smoke_only)

    def test_no_dense_result_after_filter_falls_back_to_sparse_with_smoke_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dense, _index = _dense(
                root,
                [[1.0, 0.0]],
                [_metadata_row(0, filing_id="filing-a")],
            )
            safe = _FakeSafeSearchIndex([{"evidence_id": "sparse-1", "filing_id": "filing-b"}])

            result = HybridRetriever(SparseRetriever(safe), dense).search(  # type: ignore[arg-type]
                "질문", filing_id="filing-b",
            )

            self.assertEqual([hit["evidence_id"] for hit in result.hits], ["sparse-1"])
            self.assertEqual(result.dense_status, "smoke_only_no_filtered_results")
            self.assertEqual(result.dense_corpus_size, 1)
            self.assertTrue(result.smoke_only)
            self.assertTrue(result.fallback_used)

    def test_retrieval_result_serializes_required_smoke_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dense, _index = _dense(root, [[1.0, 0.0]], [_metadata_row(0)])

            value = dense.search("질문", limit=1).to_dict()

            self.assertEqual(value["dense_status"], "smoke_only")
            self.assertEqual(value["dense_corpus_size"], 1)
            self.assertFalse(value["fallback_used"])
            self.assertEqual(value["retrieval_mode"], "dense_only")
            self.assertTrue(value["smoke_only"])


class CurrentSmokeIndexIntegrationTests(unittest.TestCase):
    def test_current_hundred_chunk_smoke_index_loads_without_model_download(self) -> None:
        config = DenseRetrieverConfig.from_env(os.environ)
        if not config.index_path.is_file() or not config.metadata_path.is_file():
            self.skipTest("100-chunk smoke index artifacts are not available")
        try:
            import faiss
        except ImportError:
            self.skipTest("faiss is not installed")
        index = faiss.read_index(str(config.index_path))
        if int(index.ntotal) != 100:
            self.fail(f"expected the current 100-chunk smoke index, got {int(index.ntotal)}")
        probe = np.asarray(index.reconstruct(0), dtype="float32")
        encoder = _FakeEncoder(probe.tolist())
        retriever = DenseFaissRetriever(config, encoder=encoder, faiss_module=faiss, numpy_module=np)

        result = retriever.search("smoke probe", limit=3)

        self.assertEqual(result.dense_status, "smoke_only")
        self.assertEqual(result.dense_corpus_size, 100)
        self.assertTrue(result.smoke_only)
        self.assertTrue(result.hits)
        self.assertEqual(result.hits[0]["vector_id"], 0)


if __name__ == "__main__":
    unittest.main()
