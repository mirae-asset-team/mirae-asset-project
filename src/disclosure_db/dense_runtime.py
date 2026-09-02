"""Full-corpus FAISS/BGE-M3 runtime used only by the temporary QA server."""

from __future__ import annotations

from array import array
from contextlib import closing
import hashlib
import json
import mmap
import os
from pathlib import Path
import platform
import sqlite3
import struct
import sys
import threading
from typing import Any


EXPECTED_MODEL = "BAAI/bge-m3"
EXPECTED_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_IDENTITY_FILENAME = "model_identity.json"
MODEL_IDENTITY_SCHEMA_VERSION = "1.0.0"
RUNTIME_MANIFEST_SCHEMA_VERSION = "1.0.0"
RUNTIME_IDENTITY_FIELDS = (
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
)


def _public_runtime_identity(runtime: object) -> dict[str, object]:
    identity = getattr(runtime, "runtime_identity")
    return {name: identity[name] for name in RUNTIME_IDENTITY_FIELDS}


def _write_runtime_manifest(path: Path, identity: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_file_paths(model_path: Path, *, allow_external_symlinks: bool = False) -> dict[str, Path]:
    root = model_path.resolve()
    files: dict[str, Path] = {}
    for path in sorted(model_path.rglob("*")):
        if not path.is_file() or path.name == MODEL_IDENTITY_FILENAME:
            continue
        resolved = path.resolve()
        if not allow_external_symlinks:
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError("dense_mounted_model_path_invalid") from exc
        relative = path.relative_to(model_path).as_posix()
        files[relative] = path
    return files


def build_model_identity(
    model_path: Path,
    *,
    reference_model_path: Path,
) -> dict[str, object]:
    """Bind staged model files to a locally resolved pinned upstream snapshot."""
    files = _model_file_paths(Path(model_path))
    reference_files = _model_file_paths(
        Path(reference_model_path), allow_external_symlinks=True
    )
    if not files or set(files) != set(reference_files):
        raise ValueError("dense_mounted_model_files_missing")
    contracts: dict[str, dict[str, object]] = {}
    for relative, path in files.items():
        reference = reference_files[relative]
        size_bytes = path.stat().st_size
        sha256 = _sha256_file(path)
        if size_bytes != reference.stat().st_size or sha256 != _sha256_file(reference):
            raise ValueError(f"dense_mounted_model_reference_mismatch:{relative}")
        contracts[relative] = {"size_bytes": size_bytes, "sha256": sha256}
    return {
        "schema_version": MODEL_IDENTITY_SCHEMA_VERSION,
        "model": EXPECTED_MODEL,
        "model_revision": EXPECTED_MODEL_REVISION,
        "provenance": {
            "repository": EXPECTED_MODEL,
            "revision": EXPECTED_MODEL_REVISION,
            "verification": "pinned_snapshot_byte_match",
        },
        "files": contracts,
    }


def _validate_model_identity(model_path: Path, identity: dict[str, object]) -> None:
    declared = identity.get("files")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("dense_mounted_model_identity_mismatch")
    actual = _model_file_paths(model_path)
    if set(declared) != set(actual):
        raise ValueError("dense_mounted_model_file_set_mismatch")
    for relative, path in actual.items():
        contract = declared.get(relative)
        if not isinstance(contract, dict):
            raise ValueError(f"dense_mounted_model_file_mismatch:{relative}")
        sha256 = contract.get("sha256")
        size_bytes = contract.get("size_bytes")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or int(size_bytes or -1) != path.stat().st_size
            or _sha256_file(path) != sha256.casefold()
        ):
            raise ValueError(f"dense_mounted_model_file_mismatch:{relative}")


def _validate_normalized_index(index: object, numpy: object, *, batch_size: int = 4096) -> None:
    vector_count = int(getattr(index, "ntotal"))
    dimension = int(getattr(index, "d"))
    for start in range(0, vector_count, batch_size):
        count = min(batch_size, vector_count - start)
        try:
            vectors = index.reconstruct_n(start, count)  # type: ignore[attr-defined]
        except TypeError:
            vectors = numpy.empty((count, dimension), dtype="float32")  # type: ignore[attr-defined]
            index.reconstruct_n(start, count, vectors)  # type: ignore[attr-defined]
        array_value = numpy.asarray(vectors, dtype="float32")  # type: ignore[attr-defined]
        if tuple(array_value.shape) != (count, dimension):
            raise ValueError("dense_vector_normalization_mismatch")
        norms = numpy.linalg.norm(array_value, axis=1)  # type: ignore[attr-defined]
        if not bool(numpy.all(numpy.isfinite(norms))) or not bool(  # type: ignore[attr-defined]
            numpy.allclose(norms, 1.0, rtol=0.0, atol=1e-4)  # type: ignore[attr-defined]
        ):
            raise ValueError("dense_vector_normalization_mismatch")


class JsonlOffsetIndex:
    def __init__(self, metadata_path: Path, offsets_path: Path, *, expected_count: int) -> None:
        self.metadata_path = Path(metadata_path)
        self.offsets_path = Path(offsets_path)
        self.expected_count = int(expected_count)
        self.manifest_path = self.offsets_path.with_suffix(self.offsets_path.suffix + ".json")
        self._ensure_current()
        self._offset_handle = self.offsets_path.open("rb")
        self._offset_map = mmap.mmap(self._offset_handle.fileno(), 0, access=mmap.ACCESS_READ)

    def _identity(self) -> dict[str, int]:
        stat = self.metadata_path.stat()
        return {"metadata_size": stat.st_size, "metadata_mtime_ns": stat.st_mtime_ns}

    def _is_current(self) -> bool:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            manifest == {**self._identity(), "line_count": self.expected_count}
            and self.offsets_path.is_file()
            and self.offsets_path.stat().st_size == self.expected_count * 8
        )

    def _ensure_current(self) -> None:
        if self._is_current():
            return
        self.offsets_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.offsets_path.with_suffix(self.offsets_path.suffix + ".tmp")
        count = 0
        offset = 0
        buffer = array("Q")
        with self.metadata_path.open("rb") as source, temporary.open("wb") as target:
            for line in source:
                buffer.append(offset)
                offset += len(line)
                count += 1
                if len(buffer) >= 65_536:
                    if sys.byteorder != "little":
                        buffer.byteswap()
                    buffer.tofile(target)
                    buffer = array("Q")
            if buffer:
                if sys.byteorder != "little":
                    buffer.byteswap()
                buffer.tofile(target)
            target.flush()
            os.fsync(target.fileno())
        if count != self.expected_count:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"dense_metadata_count_mismatch:{count}")
        temporary.replace(self.offsets_path)
        manifest = {**self._identity(), "line_count": count}
        manifest_tmp = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        manifest_tmp.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        manifest_tmp.replace(self.manifest_path)

    def read(self, vector_ids: list[int]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.metadata_path.open("rb") as metadata:
            for vector_id in vector_ids:
                if not 0 <= vector_id < self.expected_count:
                    continue
                offset = struct.unpack_from("<Q", self._offset_map, vector_id * 8)[0]
                metadata.seek(offset)
                row = json.loads(metadata.readline())
                if int(row.get("vector_id", -1)) != vector_id:
                    raise ValueError("dense_metadata_order_mismatch")
                rows.append(row)
        return rows

    def close(self) -> None:
        self._offset_map.close()
        self._offset_handle.close()


class FilingVectorIndex:
    """Persistent filing-to-vector projection for filtered FAISS searches."""

    def __init__(self, metadata_path: Path, index_path: Path, *, expected_count: int) -> None:
        self.metadata_path = Path(metadata_path)
        self.index_path = Path(index_path)
        self.expected_count = int(expected_count)
        self.manifest_path = self.index_path.with_suffix(self.index_path.suffix + ".json")
        self._ensure_current()

    def _identity(self) -> dict[str, int]:
        stat = self.metadata_path.stat()
        return {
            "metadata_size": stat.st_size,
            "metadata_mtime_ns": stat.st_mtime_ns,
            "line_count": self.expected_count,
        }

    def _is_current(self) -> bool:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if manifest != self._identity() or not self.index_path.is_file():
            return False
        try:
            with closing(sqlite3.connect(
                f"file:{self.index_path.resolve().as_posix()}?mode=ro&immutable=1",
                uri=True,
            )) as connection:
                row = connection.execute("SELECT COUNT(*) FROM filing_vector").fetchone()
        except sqlite3.Error:
            return False
        return row is not None and int(row[0]) == self.expected_count

    def _ensure_current(self) -> None:
        if self._is_current():
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        count = 0
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute(
                "CREATE TABLE filing_vector(filing_id TEXT NOT NULL, vector_id INTEGER NOT NULL, "
                "PRIMARY KEY(filing_id,vector_id)) WITHOUT ROWID"
            )
            batch: list[tuple[str, int]] = []
            with self.metadata_path.open("rb") as source:
                for line in source:
                    row = json.loads(line)
                    vector_id = int(row.get("vector_id", -1))
                    filing_id = str(row.get("filing_id") or "")
                    if vector_id != count or not filing_id:
                        raise ValueError(f"dense_filing_index_row_invalid:{count}")
                    batch.append((filing_id, vector_id))
                    count += 1
                    if len(batch) >= 10_000:
                        connection.executemany("INSERT INTO filing_vector VALUES (?,?)", batch)
                        batch.clear()
            if batch:
                connection.executemany("INSERT INTO filing_vector VALUES (?,?)", batch)
            if count != self.expected_count:
                raise ValueError(f"dense_metadata_count_mismatch:{count}")
            connection.commit()
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or str(quick_check[0]) != "ok":
                raise ValueError("dense_filing_index_quick_check_failed")
        except Exception:
            connection.close()
            temporary.unlink(missing_ok=True)
            raise
        else:
            connection.close()
        temporary.replace(self.index_path)
        manifest_tmp = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        manifest_tmp.write_text(json.dumps(self._identity(), sort_keys=True) + "\n", encoding="utf-8")
        manifest_tmp.replace(self.manifest_path)

    def vector_ids(self, filing_ids: list[str]) -> list[int]:
        unique = list(dict.fromkeys(str(item) for item in filing_ids if str(item)))
        if not unique:
            return []
        result: list[int] = []
        with closing(sqlite3.connect(
            f"file:{self.index_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )) as connection:
            for start in range(0, len(unique), 500):
                batch = unique[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT vector_id FROM filing_vector WHERE filing_id IN ({placeholders})",
                    batch,
                ).fetchall()
                result.extend(int(row[0]) for row in rows)
        return result


class DenseRuntime:
    def __init__(
        self,
        *,
        index_path: Path,
        metadata_path: Path,
        manifest_path: Path,
        offsets_path: Path,
        filing_index_path: Path,
        model_path: Path,
    ) -> None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise ValueError("dense_manifest_incomplete")
        if manifest.get("model") != EXPECTED_MODEL or manifest.get("model_revision") != EXPECTED_MODEL_REVISION:
            raise ValueError("dense_model_identity_mismatch")
        dimension = int(manifest.get("dimension") or 0)
        index_contract = manifest.get("index")
        if not isinstance(index_contract, dict):
            raise ValueError("dense_vector_contract_mismatch")
        index_type = index_contract.get("type")
        metric = index_contract.get("metric")
        normalized = index_contract.get("normalized_embeddings")
        if index_type != "IndexFlatIP" or metric != "inner_product" or normalized is not True:
            raise ValueError("dense_vector_contract_mismatch")
        vector_count = int(index_contract.get("vector_count") or 0)
        if dimension != 1024 or vector_count <= 0:
            raise ValueError("dense_manifest_invalid")
        for path, key in ((Path(index_path), "faiss_index"), (Path(metadata_path), "chunk_metadata")):
            contract = manifest.get("outputs", {}).get(key, {})
            expected = int(contract.get("size_bytes") or -1)
            expected_sha256 = contract.get("sha256")
            if not path.is_file() or path.stat().st_size != expected:
                raise ValueError(f"dense_artifact_size_mismatch:{key}")
            if (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or _sha256_file(path) != expected_sha256.casefold()
            ):
                raise ValueError(f"dense_artifact_hash_mismatch:{key}")

        model_identity_path = Path(model_path) / MODEL_IDENTITY_FILENAME
        if not model_identity_path.is_file():
            raise ValueError("dense_model_identity_manifest_missing")
        try:
            model_identity = json.loads(model_identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("dense_mounted_model_identity_mismatch") from exc
        if (
            not isinstance(model_identity, dict)
            or model_identity.get("schema_version") != MODEL_IDENTITY_SCHEMA_VERSION
            or model_identity.get("model") != EXPECTED_MODEL
            or model_identity.get("model_revision") != EXPECTED_MODEL_REVISION
        ):
            raise ValueError("dense_mounted_model_identity_mismatch")
        _validate_model_identity(Path(model_path), model_identity)

        import faiss  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-not-found]

        self._faiss = faiss
        self._numpy = np
        self.index = faiss.read_index(str(index_path))
        if int(self.index.d) != dimension or int(self.index.ntotal) != vector_count:
            raise ValueError("dense_faiss_identity_mismatch")
        if type(self.index).__name__ != index_type:
            raise ValueError("dense_faiss_identity_mismatch")
        if int(getattr(self.index, "metric_type", -1)) != int(faiss.METRIC_INNER_PRODUCT):
            raise ValueError("dense_faiss_metric_mismatch")
        _validate_normalized_index(self.index, np)
        self.metadata = JsonlOffsetIndex(metadata_path, offsets_path, expected_count=vector_count)
        self.filing_vectors = FilingVectorIndex(
            metadata_path,
            filing_index_path,
            expected_count=vector_count,
        )
        self.model = BGEM3FlagModel(str(model_path), use_fp16=False)
        self.model_revision = str(model_identity["model_revision"])
        self.vector_count = vector_count
        self.dimension = dimension
        self.runtime_identity = {
            "runtime_manifest_schema_version": RUNTIME_MANIFEST_SCHEMA_VERSION,
            "python_version": platform.python_version(),
            "numpy_version": str(np.__version__),
            "faiss_version": str(faiss.__version__),
            "model": str(model_identity["model"]),
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "vector_count": self.vector_count,
            "index_type": str(index_type),
            "metric": str(metric),
            "normalized": normalized,
        }
        self._lock = threading.Lock()

    def search(
        self,
        question: str,
        *,
        limit: int,
        filing_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not question.strip() or not 1 <= limit <= 100:
            raise ValueError("dense_query_invalid")
        allowed_vector_ids = self.filing_vectors.vector_ids(filing_ids or []) if filing_ids else []
        if filing_ids and not allowed_vector_ids:
            return []
        with self._lock:
            encoded = self.model.encode(
                [question],
                batch_size=1,
                max_length=2048,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            vector = self._numpy.asarray(encoded["dense_vecs"], dtype="float32")
            self._faiss.normalize_L2(vector)
            if allowed_vector_ids:
                allowed = self._numpy.asarray(allowed_vector_ids, dtype="int64")
                selector = self._faiss.IDSelectorBatch(allowed)
                params = self._faiss.SearchParameters(sel=selector)
                scores, vector_ids = self.index.search(vector, limit, params=params)
            else:
                scores, vector_ids = self.index.search(vector, limit)
        ranked = [
            (float(score), int(vector_id))
            for score, vector_id in zip(scores[0], vector_ids[0])
            if int(vector_id) >= 0
        ]
        ids = [vector_id for _, vector_id in ranked]
        metadata_rows = self.metadata.read(ids)
        metadata_by_id = {int(row["vector_id"]): row for row in metadata_rows}
        return [
            {
                "vector_id": vector_id,
                "score": float(score),
                "evidence_ids": list(metadata_by_id[vector_id].get("evidence_ids") or []),
                "filing_id": str(metadata_by_id[vector_id].get("filing_id") or ""),
                "chunk_id": str(metadata_by_id[vector_id].get("chunk_id") or ""),
            }
            for score, vector_id in ranked
            if vector_id in metadata_by_id
        ]


def create_app(
    runtime: DenseRuntime | None = None,
    *,
    runtime_manifest_path: Path | None = None,
):
    from fastapi import FastAPI
    from pydantic import BaseModel, Field

    app = FastAPI(title="Mirae Dense Retrieval Lab", docs_url=None, redoc_url=None)
    holder: dict[str, DenseRuntime] = {}

    class SearchRequest(BaseModel):
        question: str = Field(min_length=1, max_length=4000)
        limit: int = Field(default=40, ge=1, le=100)
        filing_ids: list[str] = Field(default_factory=list, max_length=20_000)

    @app.on_event("startup")
    def load_runtime() -> None:
        active = runtime or DenseRuntime(
            index_path=Path(os.environ["DENSE_INDEX_PATH"]),
            metadata_path=Path(os.environ["DENSE_METADATA_PATH"]),
            manifest_path=Path(os.environ["DENSE_MANIFEST_PATH"]),
            offsets_path=Path(os.environ["DENSE_OFFSETS_PATH"]),
            filing_index_path=Path(os.environ["DENSE_FILING_INDEX_PATH"]),
            model_path=Path(os.environ["DENSE_MODEL_PATH"]),
        )
        holder["runtime"] = active
        configured_manifest_path = runtime_manifest_path
        if configured_manifest_path is None and os.environ.get("DENSE_RUNTIME_MANIFEST_PATH", "").strip():
            configured_manifest_path = Path(os.environ["DENSE_RUNTIME_MANIFEST_PATH"])
        if configured_manifest_path is not None:
            _write_runtime_manifest(configured_manifest_path, _public_runtime_identity(active))

    @app.get("/health")
    def health() -> dict[str, object]:
        active = holder["runtime"]
        return {
            "ready": True,
            **_public_runtime_identity(active),
        }

    def search(payload: SearchRequest) -> dict[str, object]:
        active = holder["runtime"]
        return {
            "hits": active.search(
                payload.question,
                limit=payload.limit,
                filing_ids=payload.filing_ids,
            )
        }

    search.__annotations__["payload"] = SearchRequest
    app.post("/search")(search)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_level="info")


__all__ = ["DenseRuntime", "FilingVectorIndex", "JsonlOffsetIndex", "create_app", "main"]

