from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as real_numpy

from fastapi.testclient import TestClient

from disclosure_db.dense_runtime import (
    DenseRuntime,
    FilingVectorIndex,
    JsonlOffsetIndex,
    build_model_identity,
    create_app,
)


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
    "dense_manifest_sha256": "a" * 64,
    "faiss_index_sha256": "b" * 64,
    "chunk_metadata_sha256": "c" * 64,
    "model_identity_sha256": "d" * 64,
}

_DENSE_MODEL = "BAAI/bge-m3"
_DENSE_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def _write_dense_runtime_fixture(root: Path) -> dict[str, Path]:
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
        "model": _DENSE_MODEL,
        "model_revision": _DENSE_MODEL_REVISION,
        "dimension": 1024,
        "index": {
            "type": "IndexFlatIP",
            "metric": "inner_product",
            "normalized_embeddings": True,
            "vector_count": 1,
        },
        "outputs": {
            "faiss_index": {
                "size_bytes": index_path.stat().st_size,
                "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
            },
            "chunk_metadata": {
                "size_bytes": metadata_path.stat().st_size,
                "sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            },
        },
    }), encoding="utf-8")
    model_path.mkdir()
    model_config = model_path / "config.json"
    model_config.write_text('{"model_type":"bge-m3"}\n', encoding="utf-8")
    (model_path / "model_identity.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "model": _DENSE_MODEL,
        "model_revision": _DENSE_MODEL_REVISION,
        "files": {
            "config.json": {
                "size_bytes": model_config.stat().st_size,
                "sha256": hashlib.sha256(model_config.read_bytes()).hexdigest(),
            }
        },
    }), encoding="utf-8")
    return {
        "index_path": index_path,
        "metadata_path": metadata_path,
        "manifest_path": manifest_path,
        "model_path": model_path,
    }


def _fake_dense_modules(*, metric_type: int = 0, normalized: bool = True) -> dict[str, object]:
    def reconstruct_n(_start: int, count: int):
        vectors = real_numpy.zeros((count, 1024), dtype="float32")
        vectors[:, 0] = 1.0 if normalized else 2.0
        return vectors

    fake_index = type(
        "IndexFlatIP",
        (),
        {"d": 1024, "ntotal": 1, "metric_type": metric_type, "reconstruct_n": staticmethod(reconstruct_n)},
    )()
    return {
        "faiss": SimpleNamespace(
            METRIC_INNER_PRODUCT=0,
            METRIC_L2=1,
            __version__="1.15.0",
            read_index=lambda _: fake_index,
        ),
        "numpy": real_numpy,
        "FlagEmbedding": SimpleNamespace(BGEM3FlagModel=lambda *_args, **_kwargs: object()),
    }


def _load_dense_runtime(
    root: Path,
    paths: dict[str, Path],
    *,
    metric_type: int = 0,
    normalized: bool = True,
) -> DenseRuntime:
    with patch.dict(sys.modules, _fake_dense_modules(metric_type=metric_type, normalized=normalized)):
        return DenseRuntime(
            index_path=paths["index_path"],
            metadata_path=paths["metadata_path"],
            manifest_path=paths["manifest_path"],
            offsets_path=root / "metadata.offsets.u64",
            filing_index_path=root / "filing.sqlite",
            model_path=paths["model_path"],
        )


def _assert_dense_runtime_rejected(
    test: unittest.TestCase,
    root: Path,
    paths: dict[str, Path],
    expected_error: str,
    *,
    metric_type: int = 0,
    normalized: bool = True,
) -> None:
    runtime = None
    try:
        runtime = _load_dense_runtime(root, paths, metric_type=metric_type, normalized=normalized)
    except ValueError as exc:
        test.assertRegex(str(exc), expected_error)
    else:
        test.fail(f"ValueError matching {expected_error!r} was not raised")
    finally:
        if runtime is not None:
            runtime.metadata.close()


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
    def test_model_identity_requires_byte_identical_pinned_reference_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference"
            staged = root / "staged"
            reference.mkdir()
            staged.mkdir()
            (reference / "config.json").write_text("same\n", encoding="utf-8")
            (staged / "config.json").write_text("same\n", encoding="utf-8")

            identity = build_model_identity(staged, reference_model_path=reference)
            self.assertEqual(identity["provenance"]["verification"], "pinned_snapshot_byte_match")

            (staged / "config.json").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dense_mounted_model_reference_mismatch"):
                build_model_identity(staged, reference_model_path=reference)

    def test_loaded_runtime_records_dependency_model_and_vector_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            runtime = _load_dense_runtime(root, paths)

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
                self.assertEqual(identity["dense_manifest_sha256"], hashlib.sha256(
                    paths["manifest_path"].read_bytes(),
                ).hexdigest())
                self.assertEqual(identity["faiss_index_sha256"], hashlib.sha256(
                    paths["index_path"].read_bytes(),
                ).hexdigest())
                self.assertEqual(identity["chunk_metadata_sha256"], hashlib.sha256(
                    paths["metadata_path"].read_bytes(),
                ).hexdigest())
                self.assertEqual(identity["model_identity_sha256"], hashlib.sha256(
                    (paths["model_path"] / "model_identity.json").read_bytes(),
                ).hexdigest())
            finally:
                runtime.metadata.close()

    def test_runtime_rejects_incompatible_vector_metric_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            manifest = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
            manifest["index"]["metric"] = "l2"
            paths["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_vector_contract_mismatch",
            )

    def test_runtime_rejects_missing_normalization_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            manifest = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
            del manifest["index"]["normalized_embeddings"]
            paths["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_vector_contract_mismatch",
            )

    def test_runtime_rejects_loaded_faiss_metric_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_faiss_metric_mismatch",
                metric_type=1,
            )

    def test_runtime_rejects_faiss_artifact_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            manifest = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
            manifest["outputs"]["faiss_index"]["sha256"] = "0" * 64
            paths["manifest_path"].write_text(json.dumps(manifest), encoding="utf-8")

            _assert_dense_runtime_rejected(self, root, paths, "dense_artifact_hash_mismatch:faiss_index")

    def test_runtime_rejects_non_normalized_loaded_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_vector_normalization_mismatch",
                normalized=False,
            )

    def test_runtime_rejects_missing_mounted_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            (paths["model_path"] / "model_identity.json").unlink()

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_model_identity_manifest_missing",
            )

    def test_runtime_rejects_mismatched_mounted_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            identity_path = paths["model_path"] / "model_identity.json"
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["model_revision"] = "wrong-revision"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_mounted_model_identity_mismatch",
            )

    def test_runtime_rejects_mounted_model_file_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = _write_dense_runtime_fixture(root)
            (paths["model_path"] / "config.json").write_text("changed\n", encoding="utf-8")

            _assert_dense_runtime_rejected(
                self,
                root,
                paths,
                "dense_mounted_model_file_mismatch:config.json",
            )


if __name__ == "__main__":
    unittest.main()
