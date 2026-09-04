"""Resumable BGE-M3 dense embedding pilot for chunk-v1 JSONL files.

The source chunk file is only read.  Raw chunk text is never copied to the
metadata output; successful rows link a chunk_id to the zero-based FAISS
vector ordinal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


PIPELINE_VERSION = "bge-m3-faiss-pilot-v1"
MANIFEST_SCHEMA_VERSION = "1.0.0"
CHUNK_VERSION = "chunk-v1"
DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_BATCH_SIZE = 2
DEFAULT_MAX_LENGTH = 1024
EXPECTED_BGE_M3_DIMENSION = 1024

INDEX_FILENAME = "index.faiss"
METADATA_FILENAME = "chunk_metadata.jsonl"
FAILURES_FILENAME = "failures.jsonl"
MANIFEST_FILENAME = "manifest.json"
CHECKPOINT_FILENAME = "checkpoint.json"
_OUTPUT_FILENAMES = (
    INDEX_FILENAME,
    METADATA_FILENAME,
    FAILURES_FILENAME,
    MANIFEST_FILENAME,
    CHECKPOINT_FILENAME,
)
_PASSTHROUGH_FIELDS = (
    "chunk_version",
    "text_sha256",
    "token_count",
    "chunk_kind",
    "filing_id",
    "source_id",
    "source_format",
    "quality_status",
    "table_structure_verified",
    "section_path",
    "table_id",
    "evidence_ids",
)


@dataclass(frozen=True, slots=True)
class PreparedText:
    text: str
    source_token_count: int
    embedded_token_count: int
    truncated: bool


class DenseEncoder(Protocol):
    model_name: str
    model_revision: str | None
    dimension: int

    def prepare_text(self, text: str, *, max_length: int) -> PreparedText: ...

    def encode(self, texts: Sequence[str], *, batch_size: int) -> object: ...


class BgeM3SentenceEncoder:
    """CPU Sentence Transformers adapter that performs a real BGE-M3 call."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        model_revision: str | None = None,
        device: str = "cpu",
        model_loader: Callable[..., object] | None = None,
    ) -> None:
        if device != "cpu":
            raise ValueError("bge_m3_pilot_device_must_be_cpu")
        if model_loader is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - depends on optional runtime
                raise RuntimeError(
                    "sentence_transformers_missing: install the bge-m3-pilot extra"
                ) from exc
            model_loader = SentenceTransformer
        loader_kwargs: dict[str, object] = {"device": device}
        if model_revision is not None:
            loader_kwargs["revision"] = model_revision
        self._model = model_loader(model_name, **loader_kwargs)
        self.model_name = model_name
        self.model_revision = model_revision
        self._tokenizer = getattr(self._model, "tokenizer", None)
        if self._tokenizer is None:
            raise RuntimeError("bge_m3_tokenizer_unavailable")
        dimension_getter = getattr(self._model, "get_sentence_embedding_dimension", None)
        if not callable(dimension_getter):
            raise RuntimeError("bge_m3_dimension_unavailable")
        dimension = dimension_getter()
        if not isinstance(dimension, int) or dimension != EXPECTED_BGE_M3_DIMENSION:
            raise RuntimeError(f"unexpected_bge_m3_dimension:{dimension}")
        self.dimension = dimension

    def prepare_text(self, text: str, *, max_length: int) -> PreparedText:
        encoded = self._tokenizer(
            text,
            add_special_tokens=True,
            truncation=False,
            return_attention_mask=False,
        )
        token_ids = encoded.get("input_ids") if isinstance(encoded, Mapping) else None
        if not isinstance(token_ids, list) or not token_ids:
            raise ValueError("bge_m3_tokenization_failed")
        source_count = len(token_ids)
        if source_count <= max_length:
            return PreparedText(text, source_count, source_count, False)
        truncated_ids = token_ids[:max_length]
        embedded_text = self._tokenizer.decode(
            truncated_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ).strip()
        if not embedded_text:
            raise ValueError("bge_m3_truncation_produced_empty_text")
        retokenized = self._tokenizer(
            embedded_text,
            add_special_tokens=True,
            truncation=True,
            max_length=max_length,
            return_attention_mask=False,
        )
        embedded_ids = retokenized.get("input_ids") if isinstance(retokenized, Mapping) else None
        embedded_count = len(embedded_ids) if isinstance(embedded_ids, list) else max_length
        return PreparedText(embedded_text, source_count, embedded_count, True)

    def encode(self, texts: Sequence[str], *, batch_size: int) -> object:
        return self._model.encode(
            list(texts),
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
            device="cpu",
        )


@dataclass(slots=True)
class PilotStats:
    processed_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    truncated_count: int = 0
    failures_by_reason: dict[str, int] = field(default_factory=dict)

    def add_failure(self, reason: str) -> None:
        self.failure_count += 1
        self.failures_by_reason[reason] = self.failures_by_reason.get(reason, 0) + 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PilotStats":
        return cls(
            processed_count=int(value.get("processed_count", 0)),
            success_count=int(value.get("success_count", 0)),
            failure_count=int(value.get("failure_count", 0)),
            truncated_count=int(value.get("truncated_count", 0)),
            failures_by_reason={
                str(reason): int(count)
                for reason, count in dict(value.get("failures_by_reason", {})).items()
            },
        )


@dataclass(frozen=True, slots=True)
class _InputItem:
    line_number: int
    record: Mapping[str, object] | None
    error_code: str | None = None
    error_message: str | None = None


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, *, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open("rb") as handle:
        while remaining is None or remaining > 0:
            size = 8 * 1024 * 1024 if remaining is None else min(8 * 1024 * 1024, remaining)
            block = handle.read(size)
            if not block:
                break
            digest.update(block)
            if remaining is not None:
                remaining -= len(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _input_identity(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _configuration(
    *,
    model_name: str,
    model_revision: str | None,
    batch_size: int,
    max_length: int,
    limit: int | None,
) -> dict[str, object]:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "chunk_version": CHUNK_VERSION,
        "model": model_name,
        "model_revision": model_revision,
        "device": "cpu",
        "batch_size": batch_size,
        "max_length": max_length,
        "limit": limit,
        "normalized": True,
        "metric": "inner_product",
    }


def _load_faiss(module: object | None) -> object:
    if module is not None:
        return module
    try:
        import faiss
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError("faiss_missing: install the bge-m3-pilot extra") from exc
    return faiss


def _parse_line(raw: bytes, line_number: int) -> _InputItem:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _InputItem(line_number, None, "invalid_json", str(exc))
    if not isinstance(value, dict):
        return _InputItem(line_number, None, "chunk_not_object", "JSON value must be an object")
    chunk_id = value.get("chunk_id")
    text = value.get("text")
    if value.get("chunk_version") != CHUNK_VERSION:
        return _InputItem(line_number, value, "chunk_version_invalid", "expected chunk-v1")
    if not isinstance(chunk_id, str) or not chunk_id:
        return _InputItem(line_number, value, "chunk_id_missing", "chunk_id must be non-empty")
    if not isinstance(text, str) or not text.strip():
        return _InputItem(line_number, value, "chunk_text_missing", "text must be non-empty")
    expected_hash = _sha256_bytes(text.encode("utf-8"))
    if value.get("text_sha256") != expected_hash:
        return _InputItem(line_number, value, "text_sha256_mismatch", "text hash does not match")
    return _InputItem(line_number, value)


def _metadata_base(item: _InputItem) -> dict[str, object]:
    record = item.record or {}
    output: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "input_line_number": item.line_number,
        "chunk_id": record.get("chunk_id"),
    }
    for field_name in _PASSTHROUGH_FIELDS:
        if field_name in record:
            output[field_name] = record[field_name]
    return output


def _failure_metadata(
    item: _InputItem,
    *,
    reason: str,
    message: str,
    prepared: PreparedText | None = None,
) -> dict[str, object]:
    output = _metadata_base(item)
    output.update({
        "status": "failed",
        "vector_id": None,
        "error_code": reason,
        "error_message": message.replace("\r", " ").replace("\n", " ")[:500],
    })
    if prepared is not None:
        output.update(_truncation_metadata(prepared))
    return output


def _truncation_metadata(prepared: PreparedText) -> dict[str, object]:
    return {
        "text_truncated": prepared.truncated,
        "source_model_token_count": prepared.source_token_count,
        "embedded_model_token_count": prepared.embedded_token_count,
        "embedded_text_sha256": _sha256_bytes(prepared.text.encode("utf-8")),
    }


def _vector_rows(value: object, expected_count: int) -> list[object]:
    if hasattr(value, "shape"):
        shape = tuple(int(item) for item in value.shape)
        if len(shape) != 2 or shape[0] != expected_count:
            raise ValueError(f"embedding_count_mismatch:{shape}")
        return [value[index] for index in range(expected_count)]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("embedding_output_invalid")
    rows = list(value)
    if len(rows) != expected_count:
        raise ValueError(f"embedding_count_mismatch:{len(rows)}")
    return rows


def _normalized_vector(row: object, *, dimension: int, numpy: object) -> object:
    vector = numpy.asarray(row, dtype="float32")
    if getattr(vector, "ndim", None) != 1 or int(vector.shape[0]) != dimension:
        raise ValueError(f"embedding_dimension_mismatch:{getattr(vector, 'shape', None)}")
    if not bool(numpy.isfinite(vector).all()):
        raise ValueError("embedding_non_finite")
    norm = float(numpy.linalg.norm(vector))
    if not math.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding_zero_norm")
    return vector / norm


def _encode_prepared(
    prepared: Sequence[PreparedText],
    *,
    encoder: DenseEncoder,
    batch_size: int,
    numpy: object,
) -> list[tuple[object | None, str | None, str | None]]:
    texts = [item.text for item in prepared]
    try:
        raw_rows = _vector_rows(encoder.encode(texts, batch_size=batch_size), len(texts))
    except Exception as batch_error:
        results: list[tuple[object | None, str | None, str | None]] = []
        for text in texts:
            try:
                raw_row = _vector_rows(encoder.encode([text], batch_size=1), 1)[0]
                vector = _normalized_vector(raw_row, dimension=encoder.dimension, numpy=numpy)
                results.append((vector, None, None))
            except Exception as item_error:
                results.append((
                    None,
                    "embedding_failed",
                    f"batch={type(batch_error).__name__}; item={type(item_error).__name__}: {item_error}",
                ))
        return results
    results = []
    for raw_row in raw_rows:
        try:
            results.append((
                _normalized_vector(raw_row, dimension=encoder.dimension, numpy=numpy),
                None,
                None,
            ))
        except Exception as exc:
            results.append((None, "embedding_invalid", f"{type(exc).__name__}: {exc}"))
    return results


def _process_batch(
    items: Sequence[_InputItem],
    *,
    encoder: DenseEncoder,
    index: object,
    batch_size: int,
    max_length: int,
    numpy: object,
    stats: PilotStats,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    outputs: list[dict[str, object] | None] = [None] * len(items)
    prepared_positions: list[int] = []
    prepared_values: list[PreparedText] = []
    for position, item in enumerate(items):
        if item.error_code is not None:
            outputs[position] = _failure_metadata(
                item,
                reason=item.error_code,
                message=item.error_message or item.error_code,
            )
            continue
        assert item.record is not None
        try:
            prepared = encoder.prepare_text(str(item.record["text"]), max_length=max_length)
        except Exception as exc:
            outputs[position] = _failure_metadata(
                item,
                reason="text_preparation_failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            continue
        prepared_positions.append(position)
        prepared_values.append(prepared)

    encoded = _encode_prepared(
        prepared_values,
        encoder=encoder,
        batch_size=batch_size,
        numpy=numpy,
    ) if prepared_values else []
    successful_vectors: list[object] = []
    successful_positions: list[int] = []
    first_vector_id = int(index.ntotal)
    for position, prepared, (vector, error_code, error_message) in zip(
        prepared_positions, prepared_values, encoded
    ):
        item = items[position]
        if prepared.truncated:
            stats.truncated_count += 1
        if vector is None:
            outputs[position] = _failure_metadata(
                item,
                reason=error_code or "embedding_failed",
                message=error_message or "embedding failed",
                prepared=prepared,
            )
            continue
        vector_id = first_vector_id + len(successful_vectors)
        output = _metadata_base(item)
        output.update({
            "status": "embedded",
            "vector_id": vector_id,
            "model": encoder.model_name,
            "model_revision": encoder.model_revision,
            "dimension": encoder.dimension,
            **_truncation_metadata(prepared),
        })
        outputs[position] = output
        successful_positions.append(position)
        successful_vectors.append(vector)

    if successful_vectors:
        matrix = numpy.ascontiguousarray(numpy.stack(successful_vectors), dtype="float32")
        index.add(matrix)
        if int(index.ntotal) != first_vector_id + len(successful_vectors):
            raise RuntimeError("faiss_vector_count_mismatch_after_add")

    metadata_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []
    success_positions = set(successful_positions)
    for position, output in enumerate(outputs):
        assert output is not None
        stats.processed_count += 1
        if position in success_positions:
            stats.success_count += 1
            metadata_rows.append(output)
        else:
            stats.add_failure(str(output["error_code"]))
            failure_rows.append(output)
    return metadata_rows, failure_rows


def _write_jsonl(handle: object, rows: Sequence[Mapping[str, object]]) -> int:
    for row in rows:
        handle.write((_canonical_json(row) + "\n").encode("utf-8"))
    handle.flush()
    os.fsync(handle.fileno())
    return int(handle.tell())


def _write_metadata(
    handle: object,
    rows: Sequence[Mapping[str, object]],
    *,
    expected_start: int,
) -> int:
    for offset, row in enumerate(rows):
        if row.get("vector_id") != expected_start + offset:
            raise RuntimeError("metadata_vector_order_mismatch")
    return _write_jsonl(handle, rows)


def _write_index_atomic(faiss: object, index: object, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    faiss.write_index(index, str(temporary))
    os.replace(temporary, path)


def _checkpoint_value(
    *,
    source_identity: Mapping[str, object],
    configuration: Mapping[str, object],
    dimension: int,
    input_offset: int,
    input_line_number: int,
    metadata_offset: int,
    failure_offset: int,
    stats: PilotStats,
    vector_count: int,
    status: str,
) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": status,
        "source_identity": dict(source_identity),
        "configuration": dict(configuration),
        "dimension": dimension,
        "input_offset": input_offset,
        "input_line_number": input_line_number,
        "metadata_offset": metadata_offset,
        "failure_offset": failure_offset,
        "stats": asdict(stats),
        "vector_count": vector_count,
    }


def _load_checkpoint(
    path: Path,
    *,
    source_identity: Mapping[str, object],
    configuration: Mapping[str, object],
) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("resume_checkpoint_missing") from exc
    if not isinstance(value, dict):
        raise ValueError("resume_checkpoint_invalid")
    if value.get("source_identity") != dict(source_identity):
        raise ValueError("resume_source_changed")
    if value.get("configuration") != dict(configuration):
        raise ValueError("resume_configuration_changed")
    return value


def _repair_index_to_checkpoint(
    *,
    faiss: object,
    index: object,
    expected_count: int,
    index_path: Path,
    numpy: object,
) -> None:
    actual_count = int(index.ntotal)
    if actual_count < expected_count:
        raise ValueError("resume_index_behind_checkpoint")
    if actual_count == expected_count:
        return
    identifiers = numpy.arange(expected_count, actual_count, dtype="int64")
    removed = int(index.remove_ids(identifiers))
    if removed != actual_count - expected_count or int(index.ntotal) != expected_count:
        raise ValueError("resume_index_repair_failed")
    _write_index_atomic(faiss, index, index_path)


def _build_manifest(
    *,
    chunks_path: Path,
    index_path: Path,
    metadata_path: Path,
    failures_path: Path,
    source_identity: Mapping[str, object],
    configuration: Mapping[str, object],
    dimension: int,
    input_offset: int,
    input_line_number: int,
    stats: PilotStats,
    completion_reason: str,
) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "status": "complete",
        "completed_at": datetime.now(UTC).isoformat(),
        "completion_reason": completion_reason,
        "chunk_version": CHUNK_VERSION,
        "model": configuration["model"],
        "model_revision": configuration["model_revision"],
        "device": "cpu",
        "batch_size": configuration["batch_size"],
        "max_length": configuration["max_length"],
        "limit": configuration["limit"],
        "dimension": dimension,
        "index": {
            "type": "IndexFlatIP",
            "metric": "inner_product",
            "normalized_embeddings": True,
            "vector_count": stats.success_count,
            "metadata_order": "vector_id_ascending",
        },
        "counts": asdict(stats),
        "input": {
            **dict(source_identity),
            "processed_bytes": input_offset,
            "processed_line_count": input_line_number,
            "processed_prefix_sha256": _sha256_file(chunks_path, limit=input_offset),
        },
        "outputs": {
            "faiss_index": {
                "path": str(index_path.resolve()),
                "size_bytes": index_path.stat().st_size,
                "sha256": _sha256_file(index_path),
            },
            "chunk_metadata": {
                "path": str(metadata_path.resolve()),
                "size_bytes": metadata_path.stat().st_size,
                "sha256": _sha256_file(metadata_path),
            },
            "failures": {
                "path": str(failures_path.resolve()),
                "size_bytes": failures_path.stat().st_size,
                "sha256": _sha256_file(failures_path),
            },
        },
    }


def build_bge_m3_faiss(
    chunks_path: Path | str,
    output_dir: Path | str,
    *,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = False,
    max_length: int = DEFAULT_MAX_LENGTH,
    model_name: str = DEFAULT_MODEL,
    model_revision: str | None = None,
    encoder: DenseEncoder | None = None,
    faiss_module: object | None = None,
    numpy_module: object | None = None,
) -> dict[str, object]:
    """Embed a chunk-v1 JSONL prefix and persist a resumable CPU FAISS index."""

    if limit is not None and limit <= 0:
        raise ValueError("limit_must_be_positive")
    if batch_size <= 0:
        raise ValueError("batch_size_must_be_positive")
    if max_length <= 0:
        raise ValueError("max_length_must_be_positive")
    chunks = Path(chunks_path)
    if not chunks.is_file():
        raise FileNotFoundError(chunks)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    index_path = output / INDEX_FILENAME
    metadata_path = output / METADATA_FILENAME
    failures_path = output / FAILURES_FILENAME
    manifest_path = output / MANIFEST_FILENAME
    checkpoint_path = output / CHECKPOINT_FILENAME
    source_identity = _input_identity(chunks)
    configuration = _configuration(
        model_name=model_name,
        model_revision=model_revision,
        batch_size=batch_size,
        max_length=max_length,
        limit=limit,
    )

    checkpoint: dict[str, object] | None = None
    if resume:
        checkpoint = _load_checkpoint(
            checkpoint_path,
            source_identity=source_identity,
            configuration=configuration,
        )
        if checkpoint.get("status") == "complete":
            if not manifest_path.is_file():
                raise ValueError("resume_complete_manifest_missing")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("resume_complete_manifest_invalid")
            return manifest
    elif any((output / name).exists() for name in _OUTPUT_FILENAMES):
        raise FileExistsError("pilot_output_exists_use_resume_or_new_directory")

    if numpy_module is None:
        try:
            import numpy as numpy_module
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("numpy_missing: install the bge-m3-pilot extra") from exc
    numpy = numpy_module
    faiss = _load_faiss(faiss_module)
    if encoder is None:
        encoder = BgeM3SentenceEncoder(
            model_name,
            model_revision=model_revision,
            device="cpu",
        )
    if encoder.model_name != model_name or encoder.model_revision != model_revision:
        raise ValueError("encoder_identity_mismatch")
    if not isinstance(encoder.dimension, int) or encoder.dimension <= 0:
        raise ValueError("encoder_dimension_invalid")

    if checkpoint is None:
        index = faiss.IndexFlatIP(encoder.dimension)
        metadata_path.open("xb").close()
        failures_path.open("xb").close()
        _write_index_atomic(faiss, index, index_path)
        stats = PilotStats()
        input_offset = 0
        input_line_number = 0
        metadata_offset = 0
        failure_offset = 0
        _atomic_json(checkpoint_path, _checkpoint_value(
            source_identity=source_identity,
            configuration=configuration,
            dimension=encoder.dimension,
            input_offset=input_offset,
            input_line_number=input_line_number,
            metadata_offset=metadata_offset,
            failure_offset=failure_offset,
            stats=stats,
            vector_count=0,
            status="in_progress",
        ))
    else:
        if int(checkpoint.get("dimension", 0)) != encoder.dimension:
            raise ValueError("resume_dimension_changed")
        if not index_path.is_file() or not metadata_path.is_file() or not failures_path.is_file():
            raise ValueError("resume_outputs_missing")
        index = faiss.read_index(str(index_path))
        if int(getattr(index, "d", 0)) != encoder.dimension:
            raise ValueError("resume_index_dimension_mismatch")
        _repair_index_to_checkpoint(
            faiss=faiss,
            index=index,
            expected_count=int(checkpoint["vector_count"]),
            index_path=index_path,
            numpy=numpy,
        )
        metadata_offset = int(checkpoint["metadata_offset"])
        failure_offset = int(checkpoint["failure_offset"])
        with metadata_path.open("r+b") as repair_handle:
            repair_handle.truncate(metadata_offset)
        with failures_path.open("r+b") as repair_handle:
            repair_handle.truncate(failure_offset)
        stats = PilotStats.from_mapping(dict(checkpoint.get("stats", {})))
        input_offset = int(checkpoint["input_offset"])
        input_line_number = int(checkpoint["input_line_number"])
        if int(index.ntotal) != stats.success_count:
            raise ValueError("resume_success_count_mismatch")

    reached_eof = False
    with (
        chunks.open("rb") as input_handle,
        metadata_path.open("ab") as metadata_handle,
        failures_path.open("ab") as failure_handle,
    ):
        input_handle.seek(input_offset)
        while limit is None or stats.processed_count < limit:
            batch: list[_InputItem] = []
            while len(batch) < batch_size and (limit is None or stats.processed_count + len(batch) < limit):
                raw = input_handle.readline()
                if not raw:
                    reached_eof = True
                    break
                input_line_number += 1
                batch.append(_parse_line(raw, input_line_number))
            if not batch:
                break
            metadata_rows, failure_rows = _process_batch(
                batch,
                encoder=encoder,
                index=index,
                batch_size=batch_size,
                max_length=max_length,
                numpy=numpy,
                stats=stats,
            )
            metadata_offset = _write_metadata(
                metadata_handle,
                metadata_rows,
                expected_start=stats.success_count - len(metadata_rows),
            )
            failure_offset = _write_jsonl(failure_handle, failure_rows)
            _write_index_atomic(faiss, index, index_path)
            input_offset = int(input_handle.tell())
            _atomic_json(checkpoint_path, _checkpoint_value(
                source_identity=source_identity,
                configuration=configuration,
                dimension=encoder.dimension,
                input_offset=input_offset,
                input_line_number=input_line_number,
                metadata_offset=metadata_offset,
                failure_offset=failure_offset,
                stats=stats,
                vector_count=int(index.ntotal),
                status="in_progress",
            ))
            if reached_eof:
                break

    completion_reason = "limit_reached" if limit is not None and stats.processed_count >= limit else "end_of_input"
    if int(index.ntotal) != stats.success_count:
        raise RuntimeError("faiss_vector_count_final_mismatch")
    manifest = _build_manifest(
        chunks_path=chunks,
        index_path=index_path,
        metadata_path=metadata_path,
        failures_path=failures_path,
        source_identity=source_identity,
        configuration=configuration,
        dimension=encoder.dimension,
        input_offset=input_offset,
        input_line_number=input_line_number,
        stats=stats,
        completion_reason=completion_reason,
    )
    _atomic_json(manifest_path, manifest)
    _atomic_json(checkpoint_path, _checkpoint_value(
        source_identity=source_identity,
        configuration=configuration,
        dimension=encoder.dimension,
        input_offset=input_offset,
        input_line_number=input_line_number,
        metadata_offset=metadata_offset,
        failure_offset=failure_offset,
        stats=stats,
        vector_count=int(index.ntotal),
        status="complete",
    ))
    return manifest
