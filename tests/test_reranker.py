from __future__ import annotations

import io
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from disclosure_db.agent_contracts import EvidenceRef
from disclosure_db.reranker import ClovaReranker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class RerankerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = [
            EvidenceRef("ev1", "f1", "s1", "관련 없음"),
            EvidenceRef("ev2", "f1", "s1", "계약금액은 2000원"),
        ]

    def test_cited_document_order_selects_known_ids(self) -> None:
        payload = {"result": {"citedDocuments": [{"id": "ev2", "doc": "계약금액은 2000원"}]}}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())):
            result = ClovaReranker(api_key="key").rerank("계약금액?", self.evidence, limit=1)
        self.assertEqual(result.evidence_ids, ["ev2"])
        self.assertTrue(result.used_provider)

    def test_nonpositive_limit_returns_no_ids_without_provider_call(self) -> None:
        with patch("urllib.request.urlopen") as opened:
            zero = ClovaReranker(api_key="key").rerank("q", self.evidence, limit=0)
            negative = ClovaReranker(api_key="key").rerank("q", self.evidence, limit=-1)
        self.assertEqual(zero.evidence_ids, [])
        self.assertEqual(negative.evidence_ids, [])
        self.assertFalse(zero.used_provider)
        self.assertFalse(negative.used_provider)
        self.assertEqual(zero.reason_codes, ["reranker_empty_input"])
        self.assertEqual(negative.reason_codes, ["reranker_empty_input"])
        opened.assert_not_called()

    def test_empty_question_returns_local_fallback_without_provider_call(self) -> None:
        with patch("urllib.request.urlopen") as opened:
            result = ClovaReranker(api_key="key").rerank("  \n", self.evidence, limit=1)
        self.assertEqual(result.evidence_ids, ["ev1"])
        self.assertFalse(result.used_provider)
        self.assertEqual(result.reason_codes, ["reranker_empty_input"])
        opened.assert_not_called()

    def test_empty_evidence_returns_empty_without_provider_call(self) -> None:
        with patch("urllib.request.urlopen") as opened:
            result = ClovaReranker(api_key="key").rerank("q", [], limit=8)
        self.assertEqual(result.evidence_ids, [])
        self.assertFalse(result.used_provider)
        self.assertEqual(result.reason_codes, ["reranker_empty_input"])
        opened.assert_not_called()

    def test_fallback_deduplicates_input_ids_in_local_order(self) -> None:
        evidence = [self.evidence[0], self.evidence[0], self.evidence[1]]
        with patch.dict("os.environ", {}, clear=True):
            result = ClovaReranker(api_key=None).rerank("q", evidence, limit=8)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertFalse(result.used_provider)

    def test_request_is_bounded_and_uses_clova_headers(self) -> None:
        evidence = [EvidenceRef(f"ev{i}", "f", "s", "x" * 1300) for i in range(35)]
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response(json.dumps({"result": {"citedDocuments": [{"id": "ev0"}]}}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = ClovaReranker(api_key="key", timeout=2.5).rerank("q", evidence, limit=99)
        request = captured["request"]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(captured["timeout"], 2.5)
        self.assertEqual(request.full_url, "https://clovastudio.stream.ntruss.com/v1/api-tools/reranker")
        self.assertEqual(request.get_header("Authorization"), "Bearer key")
        self.assertTrue(request.get_header("X-ncp-clovastudio-request-id"))
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(body["query"], "q")
        self.assertEqual(body["maxTokens"], 512)
        self.assertEqual(len(body["documents"]), 30)
        self.assertTrue(all(len(document["doc"]) <= 1200 for document in body["documents"]))
        self.assertEqual(result.evidence_ids, ["ev0"])

    def test_missing_key_returns_original_order(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = ClovaReranker(api_key=None).rerank("계약금액?", self.evidence, limit=1)
        self.assertEqual(result.evidence_ids, ["ev1"])
        self.assertIn("reranker_not_configured", result.reason_codes)

    def test_unknown_provider_ids_are_dropped(self) -> None:
        payload = {"result": {"citedDocuments": [{"id": "invented", "doc": "x"}]}}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())):
            result = ClovaReranker(api_key="key").rerank("계약금액?", self.evidence, limit=1)
        self.assertEqual(result.evidence_ids, ["ev1"])
        self.assertIn("reranker_invalid_response", result.reason_codes)

    def test_duplicate_provider_ids_are_returned_once_in_provider_order(self) -> None:
        payload = {"result": {"citedDocuments": [{"id": "ev2"}, {"id": "ev2"}, {"id": "ev1"}]}}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())):
            result = ClovaReranker(api_key="key").rerank("q", self.evidence, limit=8)
        self.assertEqual(result.evidence_ids, ["ev2", "ev1"])
        self.assertTrue(result.used_provider)

    def test_empty_citations_fall_back(self) -> None:
        payload = {"result": {"citedDocuments": []}}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(payload).encode())):
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertEqual(result.reason_codes, ["reranker_invalid_response"])

    def test_malformed_json_falls_back(self) -> None:
        with patch("urllib.request.urlopen", return_value=Response(b"not-json")):
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertEqual(result.reason_codes, ["reranker_invalid_json"])

    def test_schema_error_does_not_retry(self) -> None:
        with patch("urllib.request.urlopen", return_value=Response(json.dumps({"result": {}}).encode())) as opened:
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertEqual(result.reason_codes, ["reranker_invalid_response"])

    def test_rate_limit_retries_once(self) -> None:
        error = HTTPError("https://example.invalid", 429, "busy", {}, None)
        payload = {"result": {"citedDocuments": [{"id": "ev2"}]}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[error, Response(json.dumps(payload).encode())],
        ) as opened:
            result = ClovaReranker(api_key="key").rerank("q", self.evidence, limit=1)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(result.evidence_ids, ["ev2"])
        self.assertTrue(result.used_provider)

    def test_server_error_retries_once_then_falls_back(self) -> None:
        error = HTTPError("https://example.invalid", 503, "down", {}, None)
        with patch("urllib.request.urlopen", side_effect=[error, error]) as opened:
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertEqual(result.reason_codes, ["reranker_http_error"])

    def test_auth_error_does_not_retry(self) -> None:
        error = HTTPError("https://example.invalid", 401, "unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=error) as opened:
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(result.reason_codes, ["reranker_http_error"])

    def test_timeout_falls_back(self) -> None:
        with patch("urllib.request.urlopen", side_effect=socket.timeout("slow")):
            result = ClovaReranker(api_key="key").rerank("q", self.evidence)
        self.assertEqual(result.evidence_ids, ["ev1", "ev2"])
        self.assertEqual(result.reason_codes, ["reranker_timeout"])


if __name__ == "__main__":
    unittest.main()
