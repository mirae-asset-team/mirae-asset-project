from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from disclosure_db.bge_m3_faiss import (
    BgeM3SentenceEncoder,
    CHECKPOINT_FILENAME,
    FAILURES_FILENAME,
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
    PreparedText,
    build_bge_m3_faiss,
)


class _FakeIndexFlatIP:
    def __init__(self, dimension: int) -> None:
        self.d = dimension
        self.vectors: list[list[float]] = []

    @property
    def ntotal(self) -> int:
        return len(self.vectors)

    def add(self, matrix: object) -> None:
        values = np.asarray(matrix, dtype="float32")
        if values.ndim != 2 or values.shape[1] != self.d:
            raise ValueError("invalid fake matrix")
        self.vectors.extend(values.tolist())

    def remove_ids(self, identifiers: object) -> int:
        removed = 0
        for identifier in sorted((int(item) for item in identifiers), reverse=True):
            if 0 <= identifier < len(self.vectors):
                del self.vectors[identifier]
                removed += 1
        return removed


class _FakeFaiss:
    IndexFlatIP = _FakeIndexFlatIP

    @staticmethod
    def write_index(index: _FakeIndexFlatIP, path: str) -> None:
        Path(path).write_text(
            json.dumps({"dimension": index.d, "vectors": index.vectors}),
            encoding="utf-8",
        )

    @staticmethod
    def read_index(path: str) -> _FakeIndexFlatIP:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        index = _FakeIndexFlatIP(int(value["dimension"]))
        index.vectors = [[float(item) for item in row] for row in value["vectors"]]
        return index


class _FakeEncoder:
    model_name = "BAAI/bge-m3"
    model_revision = None
    dimension = 3

    def __init__(
        self,
        *,
        failing_text: str | None = None,
        interrupt_on_call: int | None = None,
    ) -> None:
        self.failing_text = failing_text
        self.interrupt_on_call = interrupt_on_call
        self.calls: list[list[str]] = []

    def prepare_text(self, text: str, *, max_length: int) -> PreparedText:
        tokens = text.split()
        embedded = tokens[:max_length]
        return PreparedText(
            text=" ".join(embedded),
            source_token_count=len(tokens),
            embedded_token_count=len(embedded),
            truncated=len(tokens) > max_length,
        )

    def encode(self, texts: list[str], *, batch_size: int) -> object:
        self.calls.append(list(texts))
        if self.interrupt_on_call == len(self.calls):
            raise KeyboardInterrupt("simulated interruption")
        if self.failing_text is not None and any(self.failing_text in text for text in texts):
            raise RuntimeError("simulated model failure")
        return np.asarray([
            [float(len(text) + 1), float(index + 1), 1.0]
            for index, text in enumerate(texts)
        ], dtype="float32")


def _chunk(number: int, text: str | None = None) -> dict[str, object]:
    body = text or f"chunk text {number}"
    return {
        "schema_version": "1.0.0",
        "chunk_version": "chunk-v1",
        "chunk_id": f"chunk-{number:03d}",
        "text": body,
        "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "token_count": len(body.split()),
        "chunk_kind": "narrative",
        "filing_id": "filing-1",
        "source_id": "source-1",
        "source_format": "xml",
        "quality_status": "verified",
        "table_structure_verified": True,
        "section_path": ["section"],
        "table_id": None,
        "evidence_ids": [f"evidence-{number:03d}"],
    }


def _write_chunks(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def _metadata(output_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (output_dir / METADATA_FILENAME).read_text(encoding="utf-8").splitlines()
    ]


def _failures(output_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (output_dir / FAILURES_FILENAME).read_text(encoding="utf-8").splitlines()
    ]


class BgeM3FaissTests(unittest.TestCase):
    def test_real_adapter_uses_bge_m3_sentence_transformer_dense_call(self) -> None:
        captured: dict[str, object] = {}

        class Tokenizer:
            def __call__(self, text: str, **kwargs: object) -> dict[str, list[int]]:
                token_ids = list(range(len(text.split())))
                max_length = kwargs.get("max_length")
                if kwargs.get("truncation") and isinstance(max_length, int):
                    token_ids = token_ids[:max_length]
                return {"input_ids": token_ids}

            def decode(self, token_ids: list[int], **_kwargs: object) -> str:
                return " ".join(f"token-{item}" for item in token_ids)

        class Model:
            tokenizer = Tokenizer()

            @staticmethod
            def get_sentence_embedding_dimension() -> int:
                return 1024

            @staticmethod
            def encode(texts: list[str], **kwargs: object) -> object:
                captured["texts"] = texts
                captured["encode_kwargs"] = kwargs
                return np.ones((len(texts), 1024), dtype="float32")

        def loader(model_name: str, **kwargs: object) -> object:
            captured["model_name"] = model_name
            captured["loader_kwargs"] = kwargs
            return Model()

        encoder = BgeM3SentenceEncoder(model_loader=loader)
        prepared = encoder.prepare_text("one two three", max_length=2)
        vectors = encoder.encode([prepared.text], batch_size=2)

        self.assertEqual(captured["model_name"], "BAAI/bge-m3")
        self.assertEqual(captured["loader_kwargs"], {"device": "cpu"})
        self.assertTrue(prepared.truncated)
        self.assertEqual(np.asarray(vectors).shape, (1, 1024))
        self.assertEqual(captured["encode_kwargs"], {
            "batch_size": 2,
            "show_progress_bar": False,
            "convert_to_numpy": True,
            "normalize_embeddings": True,
            "precision": "float32",
            "device": "cpu",
        })

    def test_success_outputs_link_chunk_ids_to_faiss_ordinals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            _write_chunks(chunks, [_chunk(1), _chunk(2)])

            manifest = build_bge_m3_faiss(
                chunks, output, encoder=_FakeEncoder(), faiss_module=_FakeFaiss,
            )

            self.assertTrue((output / INDEX_FILENAME).is_file())
            self.assertTrue((output / METADATA_FILENAME).is_file())
            self.assertTrue((output / FAILURES_FILENAME).is_file())
            self.assertTrue((output / MANIFEST_FILENAME).is_file())
            self.assertTrue((output / CHECKPOINT_FILENAME).is_file())
            rows = _metadata(output)
            self.assertEqual([(row["chunk_id"], row["vector_id"]) for row in rows], [
                ("chunk-001", 0), ("chunk-002", 1),
            ])
            self.assertEqual(manifest["index"]["vector_count"], 2)
            self.assertEqual(manifest["index"]["metadata_order"], "vector_id_ascending")
            self.assertNotIn("text", rows[0])
            index = _FakeFaiss.read_index(str(output / INDEX_FILENAME))
            self.assertEqual(len(index.vectors), len(rows))
            self.assertTrue(all(abs(float(np.linalg.norm(vector)) - 1.0) < 1e-6 for vector in index.vectors))
            self.assertEqual([row["vector_id"] for row in rows], list(range(index.ntotal)))

    def test_limit_ten_stops_after_ten_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            _write_chunks(chunks, [_chunk(index) for index in range(12)])

            manifest = build_bge_m3_faiss(
                chunks, output, limit=10, encoder=_FakeEncoder(), faiss_module=_FakeFaiss,
            )

            self.assertEqual(manifest["completion_reason"], "limit_reached")
            self.assertEqual(manifest["counts"]["processed_count"], 10)
            self.assertEqual(len(_metadata(output)), 10)

    def test_default_batch_size_is_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            encoder = _FakeEncoder()
            _write_chunks(chunks, [_chunk(index) for index in range(5)])

            build_bge_m3_faiss(chunks, output, encoder=encoder, faiss_module=_FakeFaiss)

            self.assertEqual([len(call) for call in encoder.calls], [2, 2, 1])

    def test_text_truncation_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            _write_chunks(chunks, [_chunk(1, "one two three four")])

            manifest = build_bge_m3_faiss(
                chunks,
                output,
                max_length=2,
                encoder=_FakeEncoder(),
                faiss_module=_FakeFaiss,
            )

            row = _metadata(output)[0]
            self.assertTrue(row["text_truncated"])
            self.assertEqual(row["source_model_token_count"], 4)
            self.assertEqual(row["embedded_model_token_count"], 2)
            self.assertEqual(manifest["counts"]["truncated_count"], 1)

    def test_batch_failure_retries_individually_and_records_failed_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            encoder = _FakeEncoder(failing_text="FAIL")
            _write_chunks(chunks, [_chunk(1, "good text"), _chunk(2, "FAIL text")])

            manifest = build_bge_m3_faiss(
                chunks, output, encoder=encoder, faiss_module=_FakeFaiss,
            )

            rows = _metadata(output)
            failures = _failures(output)
            self.assertEqual([row["status"] for row in rows], ["embedded"])
            self.assertEqual([row["vector_id"] for row in rows], [0])
            self.assertEqual(failures[0]["error_code"], "embedding_failed")
            self.assertEqual([len(call) for call in encoder.calls], [2, 1, 1])
            self.assertEqual(manifest["counts"]["failure_count"], 1)

    def test_resume_continues_from_checkpoint_without_duplicate_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            _write_chunks(chunks, [_chunk(1), _chunk(2), _chunk(3)])

            with self.assertRaises(KeyboardInterrupt):
                build_bge_m3_faiss(
                    chunks,
                    output,
                    encoder=_FakeEncoder(interrupt_on_call=2),
                    faiss_module=_FakeFaiss,
                )
            manifest = build_bge_m3_faiss(
                chunks,
                output,
                resume=True,
                encoder=_FakeEncoder(),
                faiss_module=_FakeFaiss,
            )

            rows = _metadata(output)
            self.assertEqual([row["chunk_id"] for row in rows], ["chunk-001", "chunk-002", "chunk-003"])
            self.assertEqual([row["vector_id"] for row in rows], [0, 1, 2])
            self.assertEqual(manifest["index"]["vector_count"], 3)

    def test_resume_rejects_changed_chunk_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            _write_chunks(chunks, [_chunk(1), _chunk(2), _chunk(3)])
            with self.assertRaises(KeyboardInterrupt):
                build_bge_m3_faiss(
                    chunks,
                    output,
                    encoder=_FakeEncoder(interrupt_on_call=2),
                    faiss_module=_FakeFaiss,
                )
            with chunks.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_chunk(4)) + "\n")

            with self.assertRaisesRegex(ValueError, "resume_source_changed"):
                build_bge_m3_faiss(
                    chunks,
                    output,
                    resume=True,
                    encoder=_FakeEncoder(),
                    faiss_module=_FakeFaiss,
                )

    def test_invalid_json_is_recorded_and_later_chunk_is_embedded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            chunks.write_text("{not-json}\n" + json.dumps(_chunk(2)) + "\n", encoding="utf-8")

            manifest = build_bge_m3_faiss(
                chunks, output, encoder=_FakeEncoder(), faiss_module=_FakeFaiss,
            )

            rows = _metadata(output)
            self.assertEqual(_failures(output)[0]["error_code"], "invalid_json")
            self.assertEqual(rows[0]["vector_id"], 0)
            self.assertEqual(manifest["counts"]["failure_count"], 1)

    def test_text_hash_mismatch_is_recorded_without_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "chunks.jsonl"
            output = root / "output"
            row = _chunk(1)
            row["text_sha256"] = "0" * 64
            encoder = _FakeEncoder()
            _write_chunks(chunks, [row])

            manifest = build_bge_m3_faiss(
                chunks, output, encoder=encoder, faiss_module=_FakeFaiss,
            )

            self.assertEqual(_failures(output)[0]["error_code"], "text_sha256_mismatch")
            self.assertEqual(_metadata(output), [])
            self.assertEqual(encoder.calls, [])
            self.assertEqual(manifest["index"]["vector_count"], 0)


if __name__ == "__main__":
    unittest.main()
