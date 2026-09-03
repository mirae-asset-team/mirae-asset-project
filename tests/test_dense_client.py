from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import error

from disclosure_db.agent_contracts import EvidenceRef
from disclosure_db.dense_client import (
    DenseChunkHit,
    DenseSearchClient,
    EvidenceServiceDenseRetriever,
    flatten_evidence_ids,
    load_dense_adoption_artifact,
)
from disclosure_db.freeform_evaluation import semantic_summary_sha256


_RUNTIME_IDENTITY = {
    "runtime_manifest_schema_version": "1.0.0",
    "python_version": "3.11.13",
    "numpy_version": "2.5.2",
    "faiss_version": "1.15.0",
    "model": "BAAI/bge-m3",
    "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
    "dimension": 1024,
    "vector_count": 2_571_506,
    "index_type": "IndexFlatIP",
    "metric": "inner_product",
    "normalized": True,
    "dense_manifest_sha256": "b" * 64,
    "faiss_index_sha256": "c" * 64,
    "chunk_metadata_sha256": "d" * 64,
    "model_identity_sha256": "e" * 64,
}


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DenseSearchClientTests(unittest.TestCase):
    @staticmethod
    def _evaluation_summary(path: Path) -> None:
        summary: dict[str, object] = {
            "schema_version": "1.0.0",
            "status": "ok",
            "metrics": {
                "target_count": 100,
                "target_hits_at_20": 80,
                "target_recall_at_20": 0.80,
                "wrong_issuer_count": 0,
                "wrong_version_count": 0,
                "residual_misses": [{
                    "case_id": "text-miss",
                    "route": "text",
                    "target_evidence_ids": [f"target-{number}" for number in range(10)],
                }],
                "dense_pilot": {
                    "scope": "overall",
                    "target_count": 100,
                    "sparse_target_hits_at_20": 80,
                    "dense_target_hits_at_20": 85,
                    "sparse_recall_at_20": 0.80,
                    "dense_recall_at_20": 0.85,
                    "measured_gain": 0.05,
                    "wrong_issuer_count": 0,
                    "wrong_version_count": 0,
                    "p95_ms": 150,
                    "runtime_identity": dict(_RUNTIME_IDENTITY),
                },
            },
        }
        summary["semantic_sha256"] = semantic_summary_sha256(summary)
        path.write_text(json.dumps(summary), encoding="utf-8")

    @staticmethod
    def _adoption_artifact(
        path: Path,
        *,
        evaluation_summary: Path | None = None,
        runtime_identity: dict[str, object] | None = None,
        **updates: object,
    ) -> None:
        evaluation_summary = evaluation_summary or path.with_name("evaluation.json")
        if not evaluation_summary.exists():
            DenseSearchClientTests._evaluation_summary(evaluation_summary)
        summary_payload = json.loads(evaluation_summary.read_text(encoding="utf-8"))
        payload: dict[str, object] = {
            "schema_version": "dense-adoption-v2",
            "status": "ADOPTED",
            "identity": {
                "base_sha256": "a" * 64,
                "base_size_bytes": 123,
                "corpus_revision": "semantic-v1",
                "dense_url": "http://dense:8080",
                "dense_vector_count": 2_571_506,
            },
            "evaluation": {
                "summary_sha256": hashlib.sha256(evaluation_summary.read_bytes()).hexdigest(),
                "semantic_sha256": summary_payload["semantic_sha256"],
            },
            "runtime_identity": runtime_identity or dict(_RUNTIME_IDENTITY),
            "measured_gain": 0.05,
            "wrong_issuer_count": 0,
            "wrong_version_count": 0,
            "p95_ms": 150,
        }
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_dense_url_alone_does_not_enable_runtime(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(DenseSearchClient.from_environment())
        with patch.dict(os.environ, {
            "DISCLOSURE_DENSE_URL": "http://dense:8080",
            "DISCLOSURE_DENSE_TIMEOUT_SECONDS": "3.5",
            "DISCLOSURE_DENSE_VECTOR_COUNT": "2571506",
        }, clear=True):
            self.assertIsNone(DenseSearchClient.from_environment(
                base_sha256="a" * 64,
                base_size_bytes=123,
                corpus_revision="semantic-v1",
            ))

    def test_identity_bound_adopted_artifact_enables_dense_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "dense-adoption.json"
            evaluation = Path(temp) / "evaluation.json"
            self._evaluation_summary(evaluation)
            self._adoption_artifact(artifact, evaluation_summary=evaluation)
            trusted_hash = hashlib.sha256(evaluation.read_bytes()).hexdigest()
            with patch.dict(os.environ, {
                "DISCLOSURE_DENSE_URL": "http://dense:8080",
                "DISCLOSURE_DENSE_TIMEOUT_SECONDS": "3.5",
                "DISCLOSURE_DENSE_VECTOR_COUNT": "2571506",
                "DISCLOSURE_DENSE_ADOPTION_ARTIFACT": str(artifact),
                "DISCLOSURE_DENSE_EVALUATION_SUMMARY": str(evaluation),
            }, clear=True), patch(
                "disclosure_db.dense_client.request.urlopen",
                return_value=_Response({"ready": True, **_RUNTIME_IDENTITY}),
            ) as urlopen:
                with patch(
                    "disclosure_db.dense_client.TRUSTED_DENSE_EVALUATION_SHA256",
                    trusted_hash,
                ):
                    client = DenseSearchClient.from_environment(
                        base_sha256="a" * 64, base_size_bytes=123,
                        corpus_revision="semantic-v1",
                    )

        self.assertIsNotNone(client)
        assert client is not None
        self.assertEqual(client.base_url, "http://dense:8080")
        self.assertEqual(client.timeout_seconds, 3.5)
        self.assertEqual(client.vector_count, 2_571_506)
        self.assertEqual(urlopen.call_args.args[0].full_url, "http://dense:8080/health")

    def test_self_asserted_artifact_or_unreachable_sidecar_never_enables_dense(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "dense-adoption.json"
            evaluation = Path(temp) / "evaluation.json"
            self._evaluation_summary(evaluation)
            self._adoption_artifact(artifact, evaluation_summary=evaluation)
            environment = {
                "DISCLOSURE_DENSE_URL": "http://dense:8080",
                "DISCLOSURE_DENSE_VECTOR_COUNT": "2571506",
                "DISCLOSURE_DENSE_ADOPTION_ARTIFACT": str(artifact),
            }
            with patch.dict(os.environ, environment, clear=True):
                self.assertIsNone(DenseSearchClient.from_environment(
                    base_sha256="a" * 64, base_size_bytes=123,
                    corpus_revision="semantic-v1",
                ))
            environment["DISCLOSURE_DENSE_EVALUATION_SUMMARY"] = str(evaluation)
            with patch.dict(os.environ, environment, clear=True), patch(
                "disclosure_db.dense_client.request.urlopen",
                side_effect=error.URLError("offline"),
            ):
                self.assertIsNone(DenseSearchClient.from_environment(
                    base_sha256="a" * 64, base_size_bytes=123,
                    corpus_revision="semantic-v1",
                ))

    def test_missing_local_trust_artifacts_do_not_contact_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            evaluation = Path(temp) / "evaluation.json"
            self._evaluation_summary(evaluation)
            with patch.dict(os.environ, {
                "DISCLOSURE_DENSE_URL": "http://dense:8080",
                "DISCLOSURE_DENSE_VECTOR_COUNT": "2571506",
                "DISCLOSURE_DENSE_ADOPTION_ARTIFACT": str(Path(temp) / "missing.json"),
                "DISCLOSURE_DENSE_EVALUATION_SUMMARY": str(evaluation),
            }, clear=True), patch(
                "disclosure_db.dense_client.request.urlopen",
                side_effect=AssertionError("network must not be contacted"),
            ) as urlopen:
                self.assertIsNone(DenseSearchClient.from_environment(
                    base_sha256="a" * 64, base_size_bytes=123,
                    corpus_revision="semantic-v1",
                ))
            urlopen.assert_not_called()

    def test_adoption_loader_rejects_unknown_fields_and_every_failed_gate(self) -> None:
        identity = {
            "base_sha256": "a" * 64,
            "base_size_bytes": 123,
            "corpus_revision": "semantic-v1",
            "dense_url": "http://dense:8080",
            "dense_vector_count": 2_571_506,
        }
        invalid_updates = (
            {"unexpected": "field"},
            {"status": "REJECTED_PILOT"},
            {"identity": {**identity, "base_sha256": "b" * 64}},
            {"identity": {**identity, "dense_vector_count": 1}},
            {"measured_gain": float("nan")},
            {"measured_gain": 0.05 - 5e-13},
            {"wrong_issuer_count": 1},
            {"wrong_version_count": 1},
            {"p95_ms": float("inf")},
            {"p95_ms": 2000.001},
        )
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "dense-adoption.json"
            evaluation = Path(temp) / "evaluation.json"
            self._evaluation_summary(evaluation)
            for updates in invalid_updates:
                with self.subTest(updates=updates):
                    self._adoption_artifact(
                        artifact, evaluation_summary=evaluation, **updates,
                    )
                    self.assertIsNone(load_dense_adoption_artifact(
                        artifact,
                        base_sha256="a" * 64,
                        base_size_bytes=123,
                        corpus_revision="semantic-v1",
                        dense_url="http://dense:8080",
                        dense_vector_count=2_571_506,
                        evaluation_summary_path=evaluation,
                        runtime_identity=_RUNTIME_IDENTITY,
                        trusted_evaluation_sha256=hashlib.sha256(evaluation.read_bytes()).hexdigest(),
                    ))

    def test_stale_evaluation_or_runtime_manifest_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "dense-adoption.json"
            evaluation = Path(temp) / "evaluation.json"
            self._evaluation_summary(evaluation)
            self._adoption_artifact(artifact, evaluation_summary=evaluation)
            evaluation.write_text("{}", encoding="utf-8")
            self.assertIsNone(load_dense_adoption_artifact(
                artifact, base_sha256="a" * 64, base_size_bytes=123,
                corpus_revision="semantic-v1", dense_url="http://dense:8080",
                dense_vector_count=2_571_506,
                evaluation_summary_path=evaluation,
                runtime_identity=_RUNTIME_IDENTITY,
                trusted_evaluation_sha256=hashlib.sha256(evaluation.read_bytes()).hexdigest(),
            ))
            self._evaluation_summary(evaluation)
            self._adoption_artifact(artifact, evaluation_summary=evaluation)
            wrong_runtime = {**_RUNTIME_IDENTITY, "faiss_index_sha256": "f" * 64}
            self.assertIsNone(load_dense_adoption_artifact(
                artifact, base_sha256="a" * 64, base_size_bytes=123,
                corpus_revision="semantic-v1", dense_url="http://dense:8080",
                dense_vector_count=2_571_506,
                evaluation_summary_path=evaluation,
                runtime_identity=wrong_runtime,
                trusted_evaluation_sha256=hashlib.sha256(evaluation.read_bytes()).hexdigest(),
            ))

    def test_search_sends_bounded_filter_and_parses_hits(self) -> None:
        client = DenseSearchClient("http://dense:8080/")
        payload = {
            "hits": [
                {
                    "vector_id": 7,
                    "score": 0.91,
                    "evidence_ids": ["ev-1", "ev-1", "ev-2"],
                    "filing_id": "f-1",
                }
            ]
        }
        with patch("disclosure_db.dense_client.request.urlopen", return_value=_Response(payload)) as urlopen:
            hits = client.search("AI 투자", limit=12, filing_ids=["f-1", "f-1", "f-2"])

        sent_request = urlopen.call_args.args[0]
        sent_body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_request.full_url, "http://dense:8080/search")
        self.assertEqual(sent_body, {"question": "AI 투자", "limit": 12, "filing_ids": ["f-1", "f-2"]})
        self.assertEqual(
            hits,
            [DenseChunkHit(vector_id=7, score=0.91, evidence_ids=("ev-1", "ev-2"), filing_id="f-1")],
        )
        self.assertEqual(flatten_evidence_ids(hits), ["ev-1", "ev-2"])

    def test_search_fails_closed_when_sidecar_is_unavailable(self) -> None:
        client = DenseSearchClient("http://dense:8080")
        with patch(
            "disclosure_db.dense_client.request.urlopen",
            side_effect=error.URLError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "dense_service_unavailable"):
                client.search("질문")

    def test_invalid_requests_are_rejected_before_network_call(self) -> None:
        client = DenseSearchClient("http://dense:8080")
        with patch("disclosure_db.dense_client.request.urlopen") as urlopen:
            for question, limit in (("", 10), ("질문", 0), ("질문", 101)):
                with self.subTest(question=question, limit=limit):
                    with self.assertRaisesRegex(ValueError, "dense_query_invalid"):
                        client.search(question, limit=limit)
            with self.assertRaisesRegex(ValueError, "dense_filing_filter_too_large"):
                client.search("질문", filing_ids=(str(index) for index in range(20_001)))
        urlopen.assert_not_called()

    def test_invalid_response_is_rejected(self) -> None:
        client = DenseSearchClient("http://dense:8080")
        with patch("disclosure_db.dense_client.request.urlopen", return_value=_Response({"hits": "bad"})):
            with self.assertRaisesRegex(RuntimeError, "dense_response_invalid"):
                client.search("질문")


class _EvidenceService:
    def __init__(self) -> None:
        self.dense_client = DenseSearchClient(
            "http://dense:8080", vector_count=2_571_506
        )
        self.calls: list[dict[str, object]] = []

    def search_dense(self, question: str, **kwargs: object):
        self.calls.append({"question": question, **kwargs})
        return ([EvidenceRef(
            "ev-1",
            "20250318000001",
            "source-1",
            "AI 투자 관련 공시 근거",
            {"section": "사업의 내용"},
            "root",
            0.91,
            filed_at="2025-03-18",
            report_name="사업보고서",
            is_current=True,
        )], {"dense_used": True})


class EvidenceServiceDenseRetrieverTests(unittest.TestCase):
    def test_hydrated_dense_evidence_matches_existing_hybrid_contract(self) -> None:
        service = _EvidenceService()
        retriever = EvidenceServiceDenseRetriever(service)

        result = retriever.search(
            "AI 투자",
            company="삼성전자",
            start_date="2025-01-01",
            end_date="2025-12-31",
            limit=10,
        )

        self.assertEqual(result.dense_status, "full_corpus")
        self.assertEqual(result.dense_corpus_size, 2_571_506)
        self.assertEqual(result.hits[0]["evidence_id"], "ev-1")
        self.assertEqual(result.hits[0]["quality_status"], "safe_search_admitted")
        self.assertEqual(result.hits[0]["retrieval_source"], "bge-m3-dense")
        self.assertEqual(service.calls[0]["company"], "삼성전자")
        self.assertEqual(service.calls[0]["start_date"], "2025-01-01")
        self.assertEqual(service.calls[0]["end_date"], "2025-12-31")


if __name__ == "__main__":
    unittest.main()
