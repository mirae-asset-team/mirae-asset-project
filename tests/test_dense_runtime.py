from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from disclosure_db.dense_runtime import DenseRuntime, FilingVectorIndex, JsonlOffsetIndex, create_app


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


_EXPECTED_RUNTIME_IDENTITY = {
    "runtime_manifest_schema_version": "1.0.0",
    "python_version": "3.11.13",
    "numpy_version": "2.5.2",
    "faiss_version": "1.15.0",
    "model": "BAAI/bge-m3",
    "model_revision": "revision",
    "dimension": 1024,
    "vector_count": 3,
    "index_type": "IndexFlatIP",
    "metric": "inner_product",
    "normalized": True,
}


class _FakeRuntime:
    model_revision = "revision"
    dimension = 1024
    vector_count = 3
    runtime_identity = {**_EXPECTED_RUNTIME_IDENTITY, "credential": "must-not-be-published"}

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

    def test_health_exposes_allowlisted_runtime_identity_without_removing_existing_fields(self) -> None:
        with TestClient(create_app(_FakeRuntime())) as client:
            body = client.get("/health").json()

        for name, value in _EXPECTED_RUNTIME_IDENTITY.items():
            self.assertEqual(body[name], value)
        self.assertTrue(body["ready"])
        self.assertEqual(body["model_revision"], _FakeRuntime.model_revision)
        self.assertEqual(body["dimension"], _FakeRuntime.dimension)
        self.assertEqual(body["vector_count"], _FakeRuntime.vector_count)
        self.assertFalse({"path", "api_key", "credential", "environment"} & body.keys())

    def test_startup_persists_the_same_runtime_identity_as_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest_path = Path(temp) / "dense-runtime.json"
            with TestClient(create_app(_FakeRuntime(), runtime_manifest_path=manifest_path)) as client:
                body = client.get("/health").json()

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest, _EXPECTED_RUNTIME_IDENTITY)
        self.assertEqual(manifest, {name: body[name] for name in manifest})


class DenseRuntimeIdentityTests(unittest.TestCase):
    def test_loaded_runtime_records_dependency_model_and_vector_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            index_path = root / "index.faiss"
            metadata_path = root / "metadata.jsonl"
            manifest_path = root / "manifest.json"
            model_path = root / "model"
            index_path.write_bytes(b"index")
            metadata_path.write_text(
                json.dumps({"vector_id": 0, "filing_id": "f-1", "evidence_ids": ["ev-1"]}) + "\n",
                encoding="utf-8",
            )
            manifest_path.write_text(json.dumps({
                "status": "complete",
                "model": "BAAI/bge-m3",
                "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                "dimension": 1024,
                "index": {"vector_count": 1},
                "outputs": {
                    "faiss_index": {"size_bytes": index_path.stat().st_size},
                    "chunk_metadata": {"size_bytes": metadata_path.stat().st_size},
                },
            }), encoding="utf-8")
            model_path.mkdir()

            fake_index = type("IndexFlatIP", (), {"d": 1024, "ntotal": 1})()
            fake_faiss = SimpleNamespace(__version__="1.15.0", read_index=lambda _: fake_index)
            fake_numpy = SimpleNamespace(__version__="2.5.2")
            fake_flag_embedding = SimpleNamespace(BGEM3FlagModel=lambda *_args, **_kwargs: object())
            with patch.dict(sys.modules, {
                "faiss": fake_faiss,
                "numpy": fake_numpy,
                "FlagEmbedding": fake_flag_embedding,
            }):
                runtime = DenseRuntime(
                    index_path=index_path,
                    metadata_path=metadata_path,
                    manifest_path=manifest_path,
                    offsets_path=root / "metadata.offsets.u64",
                    filing_index_path=root / "filing.sqlite",
                    model_path=model_path,
                )

            try:
                identity = runtime.runtime_identity
                self.assertEqual(identity["numpy_version"], "2.5.2")
                self.assertEqual(identity["faiss_version"], "1.15.0")
                self.assertEqual(identity["model"], "BAAI/bge-m3")
                self.assertEqual(identity["model_revision"], "5617a9f61b028005a4858fdac845db406aefb181")
                self.assertEqual(identity["dimension"], 1024)
                self.assertEqual(identity["vector_count"], 1)
                self.assertEqual(identity["index_type"], "IndexFlatIP")
                self.assertEqual(identity["metric"], "inner_product")
                self.assertTrue(identity["normalized"])
            finally:
                runtime.metadata.close()


if __name__ == "__main__":
    unittest.main()
