from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from disclosure_db.dense_runtime import FilingVectorIndex, JsonlOffsetIndex, create_app


def _write_metadata(path: Path) -> None:
    rows = [
        {"vector_id": 0, "filing_id": "f-1", "evidence_ids": ["ev-1"]},
        {"vector_id": 1, "filing_id": "f-2", "evidence_ids": ["ev-2"]},
        {"vector_id": 2, "filing_id": "f-1", "evidence_ids": ["ev-3"]},
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class DenseRuntimeIndexTests(unittest.TestCase):
    def test_jsonl_offset_index_reads_only_requested_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "metadata.jsonl"
            offsets = root / "metadata.offsets.u64"
            _write_metadata(metadata)

            index = JsonlOffsetIndex(metadata, offsets, expected_count=3)
            try:
                self.assertEqual([row["vector_id"] for row in index.read([2, 0])], [2, 0])
                self.assertEqual(offsets.stat().st_size, 24)
            finally:
                index.close()

    def test_jsonl_offset_index_rejects_wrong_expected_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "metadata.jsonl"
            _write_metadata(metadata)
            with self.assertRaisesRegex(ValueError, "dense_metadata_count_mismatch"):
                JsonlOffsetIndex(metadata, root / "offsets.u64", expected_count=4)

    def test_filing_vector_index_returns_filtered_vector_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "metadata.jsonl"
            _write_metadata(metadata)
            index = FilingVectorIndex(metadata, root / "filing.sqlite", expected_count=3)
            self.assertEqual(sorted(index.vector_ids(["f-1"])), [0, 2])
            self.assertEqual(index.vector_ids(["missing"]), [])


class _FakeRuntime:
    model_revision = "revision"
    dimension = 1024
    vector_count = 3

    def search(self, question: str, *, limit: int, filing_ids: list[str]) -> list[dict[str, object]]:
        return [{"vector_id": 1, "score": 0.8, "question": question, "limit": limit, "filing_ids": filing_ids}]


class DenseRuntimeApiTests(unittest.TestCase):
    def test_health_search_and_openapi_contract(self) -> None:
        with TestClient(create_app(_FakeRuntime())) as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["vector_count"], 3)

            response = client.post(
                "/search",
                json={"question": "AI 투자", "limit": 5, "filing_ids": ["f-1"]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["hits"][0]["filing_ids"], ["f-1"])

            schema = client.get("/openapi.json")
            self.assertEqual(schema.status_code, 200)
            self.assertIn("/search", schema.json()["paths"])


if __name__ == "__main__":
    unittest.main()
