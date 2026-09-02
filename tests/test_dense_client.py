from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib import error

from disclosure_db.agent_contracts import EvidenceRef
from disclosure_db.dense_client import (
    DenseChunkHit,
    DenseSearchClient,
    EvidenceServiceDenseRetriever,
    flatten_evidence_ids,
)


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
    def test_environment_configuration_is_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(DenseSearchClient.from_environment())
        with patch.dict(os.environ, {
            "DISCLOSURE_DENSE_URL": "http://dense:8080",
            "DISCLOSURE_DENSE_TIMEOUT_SECONDS": "3.5",
            "DISCLOSURE_DENSE_VECTOR_COUNT": "2571506",
        }, clear=True):
            client = DenseSearchClient.from_environment()
        self.assertIsNotNone(client)
        self.assertEqual(client.base_url, "http://dense:8080")
        self.assertEqual(client.timeout_seconds, 3.5)
        self.assertEqual(client.vector_count, 2_571_506)

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
