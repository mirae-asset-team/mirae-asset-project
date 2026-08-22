"""Deterministic, resumable embedding chunks built without mutating source evidence."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Iterator, Mapping, Sequence


CHUNK_VERSION = "chunk-v1"
CHUNK_SCHEMA_VERSION = "1.0.0"
TOKENIZER_VERSION = "unicode-regex-v1"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "embedding_chunk_v1.json"
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_OUTPUT_NAMES = {
    "chunks": "chunks.jsonl",
    "links": "chunk_evidence.jsonl",
    "failures": "failures.jsonl",
    "checkpoint": "checkpoint.json",
    "manifest": "manifest.json",
}
_REQUIRED_TABLES = {"source_document", "fragment", "table_record", "table_cell"}


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    chunk_version: str = CHUNK_VERSION
    tokenizer: str = TOKENIZER_VERSION
    target_tokens: int = 450
    max_tokens: int = 700
    overlap_tokens: int = 80
    batch_size: int = 1000
    checkpoint_every_groups: int = 100
    include_section_prefix: bool = True

    def validate(self) -> None:
        if self.chunk_version != CHUNK_VERSION:
            raise ValueError("chunk_version_must_be_chunk_v1")
        if self.tokenizer != TOKENIZER_VERSION:
            raise ValueError("unsupported_chunk_tokenizer")
        if not 0 < self.target_tokens <= self.max_tokens:
            raise ValueError("chunk_target_must_not_exceed_max")
        if not 0 <= self.overlap_tokens < self.target_tokens:
            raise ValueError("chunk_overlap_must_be_smaller_than_target")
        if self.batch_size <= 0:
            raise ValueError("chunk_batch_size_must_be_positive")
        if self.checkpoint_every_groups <= 0:
            raise ValueError("checkpoint_interval_must_be_positive")


@dataclass(frozen=True, slots=True)
class TextUnit:
    text: str
    evidence_ids: tuple[str, ...]
    sequence_no: int


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    evidence_ids: tuple[str, ...]
    sequence_no: int | None


@dataclass(slots=True)
class ChunkStats:
    total_chunks: int = 0
    token_sum: int = 0
    max_token_count: int = 0
    pdf_chunk_count: int = 0
    evidence_link_count: int = 0
    failure_count: int = 0
    groups_completed: int = 0
    chunks_by_source_format: dict[str, int] = field(
        default_factory=lambda: {"xml": 0, "html": 0, "pdf": 0}
    )
    failures_by_reason: dict[str, int] = field(default_factory=dict)

    def add_chunk(self, *, source_format: str, token_count: int, evidence_count: int) -> None:
        self.total_chunks += 1
        self.token_sum += token_count
        self.max_token_count = max(self.max_token_count, token_count)
        self.evidence_link_count += evidence_count
        self.chunks_by_source_format[source_format] = self.chunks_by_source_format.get(source_format, 0) + 1
        if source_format == "pdf":
            self.pdf_chunk_count += 1

    def add_failure(self, reason: str) -> None:
        self.failure_count += 1
        self.failures_by_reason[reason] = self.failures_by_reason.get(reason, 0) + 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChunkStats":
        return cls(
            total_chunks=int(value.get("total_chunks", 0)),
            token_sum=int(value.get("token_sum", 0)),
            max_token_count=int(value.get("max_token_count", 0)),
            pdf_chunk_count=int(value.get("pdf_chunk_count", 0)),
            evidence_link_count=int(value.get("evidence_link_count", 0)),
            failure_count=int(value.get("failure_count", 0)),
            groups_completed=int(value.get("groups_completed", 0)),
            chunks_by_source_format={
                str(key): int(count)
                for key, count in dict(value.get("chunks_by_source_format", {})).items()
            },
            failures_by_reason={
                str(key): int(count)
                for key, count in dict(value.get("failures_by_reason", {})).items()
            },
        )


@dataclass(frozen=True, slots=True)
class ChunkBuildResult:
    status: str
    output_directory: str
    manifest_path: str | None
    checkpoint_path: str
    summary: Mapping[str, object]


class ChunkGroupError(ValueError):
    """A bounded source group is invalid but the remaining corpus may continue."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _semantic_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path, *, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_chunk_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ChunkConfig:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("chunk_config_must_be_an_object")
    allowed = set(ChunkConfig.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown_chunk_config_fields:{','.join(sorted(unknown))}")
    config = ChunkConfig(**value)
    config.validate()
    return config


def _source_format(value: object) -> str:
    normalized = str(value or "").casefold()
    if normalized == "pdf" or normalized.endswith("_pdf"):
        return "pdf"
    if "html" in normalized:
        return "html"
    if "xml" in normalized:
        return "xml"
    raise ChunkGroupError(f"unsupported_source_format:{normalized or 'missing'}")


def _section_path(value: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ChunkGroupError("section_path_invalid") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ChunkGroupError("section_path_invalid")
    return tuple(item for item in parsed if item.strip())


def _tokenize(text: str, *, evidence_ids: Sequence[str] = (), sequence_no: int | None = None) -> list[_Token]:
    identifiers = tuple(sorted(set(str(item) for item in evidence_ids if item)))
    return [_Token(match.group(0), identifiers, sequence_no) for match in _TOKEN_RE.finditer(text)]


def count_tokens(text: str) -> int:
    """Count deterministic chunk-v1 tokens without loading an embedding model."""
    return sum(1 for _ in _TOKEN_RE.finditer(text))


def _prefix_tokens(
    *,
    section_path: Sequence[str],
    config: ChunkConfig,
    table_context: Mapping[str, object] | None = None,
) -> list[_Token]:
    lines: list[tuple[str, Sequence[str]]] = []
    if config.include_section_prefix:
        section = " > ".join(section_path) if section_path else "<root>"
        lines.append((f"Section: {section}", ()))
    if table_context is not None:
        header_ids = tuple(str(item) for item in table_context.get("header_evidence_ids", ()))
        lines.extend((
            (f"Caption: {table_context.get('caption') or '<none>'}", ()),
            (f"Header: {table_context.get('header') or '<none>'}", header_ids),
            (f"Unit: {table_context.get('unit') or '<none>'}", ()),
        ))
    tokens: list[_Token] = []
    for text, evidence_ids in lines:
        tokens.extend(_tokenize(text, evidence_ids=evidence_ids))
    return tokens


def _chunk_body_tokens(
    units: Iterable[TextUnit],
    *,
    prefix_count: int,
    config: ChunkConfig,
) -> Iterator[list[_Token]]:
    max_body = config.max_tokens - prefix_count
    if max_body <= 0:
        raise ChunkGroupError("chunk_prefix_exceeds_token_budget")
    target_body = min(max_body, config.target_tokens - prefix_count)
    if target_body <= 0:
        target_body = max_body
    overlap = min(config.overlap_tokens, max(0, target_body - 1))
    current: list[_Token] = []
    has_new_tokens = False

    def emitted_overlap() -> list[_Token]:
        return current[-overlap:] if overlap else []

    for unit in units:
        remaining = _tokenize(unit.text, evidence_ids=unit.evidence_ids, sequence_no=unit.sequence_no)
        while remaining:
            projected = len(current) + len(remaining)
            if projected <= target_body:
                current.extend(remaining)
                has_new_tokens = True
                remaining = []
                continue
            if projected <= max_body:
                current.extend(remaining)
                has_new_tokens = True
                remaining = []
                yield list(current)
                current = emitted_overlap()
                has_new_tokens = False
                continue
            capacity = max_body - len(current)
            if capacity <= 0:
                if not has_new_tokens:
                    raise ChunkGroupError("chunk_overlap_exhausted_token_budget")
                yield list(current)
                current = emitted_overlap()
                has_new_tokens = False
                continue
            current.extend(remaining[:capacity])
            remaining = remaining[capacity:]
            has_new_tokens = True
            yield list(current)
            current = emitted_overlap()
            has_new_tokens = False
    if has_new_tokens and current:
        yield list(current)


def _chunk_record(
    *,
    filing_id: str,
    source_id: str,
    source_format: str,
    quality_status: str,
    section_path: Sequence[str],
    table_id: str | None,
    chunk_kind: str,
    table_structure_verified: bool,
    ordinal: int,
    prefix: Sequence[_Token],
    body: Sequence[_Token],
    config_sha256: str,
) -> dict[str, object]:
    tokens = [*prefix, *body]
    if not tokens:
        raise ChunkGroupError("empty_chunk")
    text = " ".join(token.text for token in tokens)
    text_sha256 = _sha256_bytes(text.encode("utf-8"))
    evidence_ids = sorted({evidence for token in tokens for evidence in token.evidence_ids})
    sequences = [token.sequence_no for token in body if token.sequence_no is not None]
    identity = {
        "chunk_version": CHUNK_VERSION,
        "config_sha256": config_sha256,
        "filing_id": filing_id,
        "source_id": source_id,
        "section_path": list(section_path),
        "table_id": table_id,
        "chunk_kind": chunk_kind,
        "ordinal": ordinal,
        "text_sha256": text_sha256,
    }
    return {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_version": CHUNK_VERSION,
        "chunk_id": f"chk_v1_{_semantic_sha256(identity)}",
        "text_sha256": text_sha256,
        "tokenizer": TOKENIZER_VERSION,
        "token_count": len(tokens),
        "chunk_kind": chunk_kind,
        "filing_id": filing_id,
        "source_id": source_id,
        "source_format": source_format,
        "quality_status": quality_status,
        "table_structure_verified": bool(table_structure_verified),
        "section_path": list(section_path),
        "table_id": table_id,
        "sequence_start": min(sequences) if sequences else None,
        "sequence_end": max(sequences) if sequences else None,
        "ordinal_in_group": ordinal,
        "evidence_ids": evidence_ids,
        "text": text,
    }


class _OutputWriter:
    def __init__(self, output_dir: Path, *, offsets: Mapping[str, object] | None = None) -> None:
        self.output_dir = output_dir
        self.paths = {key: output_dir / name for key, name in _OUTPUT_NAMES.items()}
        self._handles: dict[str, object] = {}
        for key in ("chunks", "links", "failures"):
            path = self.paths[key]
            if offsets is not None:
                with path.open("r+b") as handle:
                    handle.truncate(int(offsets.get(key, 0)))
                handle = path.open("ab")
            else:
                handle = path.open("xb")
            self._handles[key] = handle

    def _write(self, key: str, value: object) -> None:
        payload = (_canonical_json(value) + "\n").encode("utf-8")
        self._handles[key].write(payload)  # type: ignore[union-attr]

    def write_chunk(self, record: Mapping[str, object], stats: ChunkStats) -> None:
        self._write("chunks", record)
        evidence_ids = [str(item) for item in record["evidence_ids"]]
        for evidence_id in evidence_ids:
            self._write("links", {
                "chunk_version": CHUNK_VERSION,
                "chunk_id": record["chunk_id"],
                "evidence_id": evidence_id,
            })
        stats.add_chunk(
            source_format=str(record["source_format"]),
            token_count=int(record["token_count"]),
            evidence_count=len(evidence_ids),
        )

    def write_failure(self, value: Mapping[str, object], stats: ChunkStats) -> None:
        self._write("failures", value)
        stats.add_failure(str(value["reason"]))

    def flush(self) -> dict[str, int]:
        offsets: dict[str, int] = {}
        for key, handle in self._handles.items():
            handle.flush()  # type: ignore[union-attr]
            os.fsync(handle.fileno())  # type: ignore[union-attr]
            offsets[key] = int(handle.tell())  # type: ignore[union-attr]
        return offsets

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()  # type: ignore[union-attr]


def _readonly_connection(database: Path) -> sqlite3.Connection:
    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _database_identity(database: Path) -> dict[str, object]:
    stat = database.stat()
    return {
        "path": str(database.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _validate_source(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        raise ValueError(f"chunk_source_tables_missing:{','.join(missing)}")


def _input_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "fragment_count": int(connection.execute("SELECT count(*) FROM fragment").fetchone()[0]),
        "table_count": int(connection.execute("SELECT count(*) FROM table_record").fetchone()[0]),
        "table_cell_count": int(connection.execute("SELECT count(*) FROM table_cell").fetchone()[0]),
        "pdf_source_count": int(connection.execute(
            "SELECT count(DISTINCT source_id) FROM source_document WHERE lower(detected_format)='pdf'"
        ).fetchone()[0]),
    }


def _stream_groups(
    cursor: sqlite3.Cursor,
    *,
    key_fields: Sequence[str],
    batch_size: int,
) -> Iterator[tuple[tuple[str, ...], list[sqlite3.Row]]]:
    current_key: tuple[str, ...] | None = None
    rows: list[sqlite3.Row] = []
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        for row in batch:
            key = tuple(str(row[field]) for field in key_fields)
            if current_key is not None and key != current_key:
                yield current_key, rows
                rows = []
            current_key = key
            rows.append(row)
    if current_key is not None:
        yield current_key, rows


_NARRATIVE_SQL = """
SELECT fr.evidence_id,fr.filing_id,fr.source_id,fr.fragment_type,fr.sequence_no,
       fr.section_path_json,fr.text_normalized,s.detected_format,s.parse_status AS source_parse_status
FROM fragment fr
JOIN source_document s ON s.source_id=fr.source_id
WHERE fr.fragment_type <> 'table_row'
ORDER BY fr.filing_id,fr.source_id,fr.section_path_json,fr.sequence_no,fr.evidence_id
"""


_TABLE_SQL = """
SELECT tr.table_id,tr.filing_id,tr.source_id,tr.sequence_no AS table_sequence_no,
       tr.section_path_json,tr.caption,tr.unit_text,tr.parse_status AS table_parse_status,
       s.detected_format,s.parse_status AS source_parse_status,
       'meta' AS record_kind,-1 AS row_index,-1 AS column_index,'' AS cell_kind,
       '[]' AS column_header_path_json,'' AS evidence_id,'' AS text_normalized
FROM table_record tr
JOIN source_document s ON s.source_id=tr.source_id
UNION ALL
SELECT tr.table_id,tr.filing_id,tr.source_id,tr.sequence_no AS table_sequence_no,
       tr.section_path_json,tr.caption,tr.unit_text,tr.parse_status AS table_parse_status,
       s.detected_format,s.parse_status AS source_parse_status,
       'cell' AS record_kind,tc.row_index,tc.column_index,tc.cell_kind,
       tc.column_header_path_json,tc.evidence_id,tc.text_normalized
FROM table_record tr
JOIN source_document s ON s.source_id=tr.source_id
JOIN table_cell tc ON tc.table_id=tr.table_id
UNION ALL
SELECT tr.table_id,tr.filing_id,tr.source_id,tr.sequence_no AS table_sequence_no,
       tr.section_path_json,tr.caption,tr.unit_text,tr.parse_status AS table_parse_status,
       s.detected_format,s.parse_status AS source_parse_status,
       'row' AS record_kind,CAST(json_extract(fr.locator_json,'$.row') AS INTEGER) AS row_index,
       -1 AS column_index,'row' AS cell_kind,'[]' AS column_header_path_json,
       fr.evidence_id,fr.text_normalized
FROM table_record tr
JOIN source_document s ON s.source_id=tr.source_id
JOIN fragment fr ON fr.table_id=tr.table_id AND fr.fragment_type='table_row'
ORDER BY table_id,row_index,record_kind,column_index,evidence_id
"""


def _narrative_records(
    rows: Sequence[sqlite3.Row],
    *,
    config: ChunkConfig,
    config_sha256: str,
) -> Iterator[dict[str, object]]:
    first = rows[0]
    section = _section_path(first["section_path_json"])
    source_format = _source_format(first["detected_format"])
    quality_status = "unverified" if source_format == "pdf" or first["source_parse_status"] != "success" else "verified"
    prefix = _prefix_tokens(section_path=section, config=config)
    units = (
        TextUnit(str(row["text_normalized"]), (str(row["evidence_id"]),), int(row["sequence_no"]))
        for row in rows if str(row["text_normalized"]).strip()
    )
    for ordinal, body in enumerate(_chunk_body_tokens(units, prefix_count=len(prefix), config=config)):
        yield _chunk_record(
            filing_id=str(first["filing_id"]),
            source_id=str(first["source_id"]),
            source_format=source_format,
            quality_status=quality_status,
            section_path=section,
            table_id=None,
            chunk_kind="narrative",
            table_structure_verified=False,
            ordinal=ordinal,
            prefix=prefix,
            body=body,
            config_sha256=config_sha256,
        )


def _json_string_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _table_records(
    rows: Sequence[sqlite3.Row],
    *,
    config: ChunkConfig,
    config_sha256: str,
) -> Iterator[dict[str, object]]:
    first = rows[0]
    section = _section_path(first["section_path_json"])
    source_format = _source_format(first["detected_format"])
    cells_by_row: dict[int, list[sqlite3.Row]] = defaultdict(list)
    row_fragments: dict[int, sqlite3.Row] = {}
    for row in rows:
        if row["record_kind"] == "cell":
            cells_by_row[int(row["row_index"])].append(row)
        elif row["record_kind"] == "row":
            row_fragments[int(row["row_index"])] = row
    for cells in cells_by_row.values():
        cells.sort(key=lambda item: (int(item["column_index"]), str(item["evidence_id"])))

    data_rows = [
        row_index for row_index, cells in cells_by_row.items()
        if any(str(cell["cell_kind"]) == "data" and str(cell["text_normalized"]).strip() for cell in cells)
    ]
    first_data_row = min(data_rows) if data_rows else None
    header_rows = {
        row_index for row_index, cells in cells_by_row.items()
        if first_data_row is None or row_index < first_data_row
        if any(str(cell["cell_kind"]) == "header" and str(cell["text_normalized"]).strip() for cell in cells)
    }
    header_cells = [
        cell for row_index in sorted(header_rows) for cell in cells_by_row[row_index]
        if str(cell["text_normalized"]).strip()
    ]
    header_texts: list[str] = []
    header_ids: list[str] = []
    for cell in header_cells:
        text = str(cell["text_normalized"]).strip()
        if text and text not in header_texts:
            header_texts.append(text)
        header_ids.append(str(cell["evidence_id"]))
        for value in _json_string_list(cell["column_header_path_json"]):
            if value and value not in header_texts:
                header_texts.append(value)
    for row_index in header_rows:
        if row_index in row_fragments:
            header_ids.append(str(row_fragments[row_index]["evidence_id"]))

    table_context = {
        "caption": first["caption"],
        "header": " | ".join(header_texts),
        "unit": first["unit_text"],
        "header_evidence_ids": sorted(set(header_ids)),
    }
    prefix = _prefix_tokens(section_path=section, config=config, table_context=table_context)
    body_rows = sorted((set(cells_by_row) | set(row_fragments)) - header_rows)
    if not body_rows:
        body_rows = sorted(set(cells_by_row) | set(row_fragments))
    units: list[TextUnit] = []
    for row_index in body_rows:
        cells = cells_by_row.get(row_index, [])
        row_fragment = row_fragments.get(row_index)
        text = str(row_fragment["text_normalized"]).strip() if row_fragment is not None else ""
        if not text:
            text = " | ".join(
                str(cell["text_normalized"]).strip()
                for cell in cells if str(cell["text_normalized"]).strip()
            )
        evidence_ids = [str(cell["evidence_id"]) for cell in cells]
        if row_fragment is not None:
            evidence_ids.append(str(row_fragment["evidence_id"]))
        if text:
            units.append(TextUnit(text, tuple(sorted(set(evidence_ids))), row_index))
    if not units:
        raise ChunkGroupError("table_without_chunkable_rows")
    quality_status = (
        "verified"
        if source_format != "pdf"
        and first["source_parse_status"] == "success"
        and first["table_parse_status"] == "success"
        else "unverified"
    )
    table_verified = source_format != "pdf" and first["table_parse_status"] == "success"
    for ordinal, body in enumerate(_chunk_body_tokens(units, prefix_count=len(prefix), config=config)):
        yield _chunk_record(
            filing_id=str(first["filing_id"]),
            source_id=str(first["source_id"]),
            source_format=source_format,
            quality_status=quality_status,
            section_path=section,
            table_id=str(first["table_id"]),
            chunk_kind="table",
            table_structure_verified=table_verified,
            ordinal=ordinal,
            prefix=prefix,
            body=body,
            config_sha256=config_sha256,
        )


def _summary(stats: ChunkStats, *, pdf_source_count: int) -> dict[str, object]:
    return {
        "total_chunk_count": stats.total_chunks,
        "chunk_count_by_source_format": {
            key: int(stats.chunks_by_source_format.get(key, 0)) for key in ("xml", "html", "pdf")
        },
        "average_token_count": round(stats.token_sum / stats.total_chunks, 3) if stats.total_chunks else 0.0,
        "max_token_count": stats.max_token_count,
        "pdf_chunk_count": stats.pdf_chunk_count,
        "pdf_source_count": pdf_source_count,
        "failure_count": stats.failure_count,
        "failures_by_reason": dict(sorted(stats.failures_by_reason.items())),
        "evidence_link_count": stats.evidence_link_count,
        "groups_completed": stats.groups_completed,
    }


def _checkpoint_value(
    *,
    status: str,
    phase: str,
    phase_groups_completed: int,
    source_identity: Mapping[str, object],
    config_sha256: str,
    offsets: Mapping[str, int],
    stats: ChunkStats,
) -> dict[str, object]:
    return {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_version": CHUNK_VERSION,
        "status": status,
        "phase": phase,
        "phase_groups_completed": phase_groups_completed,
        "source_identity": dict(source_identity),
        "config_sha256": config_sha256,
        "output_offsets": dict(offsets),
        "stats": stats.to_dict(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _prepare_output(
    output_dir: Path,
    *,
    resume: bool,
    overwrite: bool,
    source_identity: Mapping[str, object],
    config_sha256: str,
) -> tuple[_OutputWriter | None, str, int, ChunkStats, dict[str, object] | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {key: output_dir / name for key, name in _OUTPUT_NAMES.items()}
    if resume:
        checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
        if checkpoint.get("source_identity") != dict(source_identity):
            raise ValueError("checkpoint_source_identity_mismatch")
        if checkpoint.get("config_sha256") != config_sha256:
            raise ValueError("checkpoint_config_mismatch")
        if checkpoint.get("status") == "complete":
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            return None, "complete", 0, ChunkStats.from_dict(checkpoint["stats"]), manifest
        writer = _OutputWriter(output_dir, offsets=checkpoint.get("output_offsets", {}))
        return (
            writer,
            str(checkpoint.get("phase", "narrative")),
            int(checkpoint.get("phase_groups_completed", 0)),
            ChunkStats.from_dict(checkpoint.get("stats", {})),
            None,
        )
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"chunk_output_exists:{existing[0]}")
    if overwrite:
        for path in existing:
            if path.is_dir():
                raise IsADirectoryError(path)
            path.unlink()
    return _OutputWriter(output_dir), "narrative", 0, ChunkStats(), None


def build_embedding_chunks(
    database: Path | str,
    output_dir: Path | str,
    *,
    config: ChunkConfig | None = None,
    resume: bool = False,
    overwrite: bool = False,
    max_groups: int | None = None,
) -> ChunkBuildResult:
    """Build chunk-v1 JSONL and evidence links from an immutable SQLite corpus."""
    database = Path(database).resolve()
    output_dir = Path(output_dir).resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    if database == output_dir or output_dir in database.parents:
        raise ValueError("chunk_output_must_not_contain_source_database")
    if max_groups is not None and max_groups <= 0:
        raise ValueError("max_groups_must_be_positive")
    config = config or load_chunk_config()
    config.validate()
    config_value = asdict(config)
    config_sha256 = _semantic_sha256(config_value)
    source_identity = _database_identity(database)
    writer, phase, phase_groups_completed, stats, completed_manifest = _prepare_output(
        output_dir,
        resume=resume,
        overwrite=overwrite,
        source_identity=source_identity,
        config_sha256=config_sha256,
    )
    paths = {key: output_dir / name for key, name in _OUTPUT_NAMES.items()}
    if completed_manifest is not None:
        return ChunkBuildResult(
            status="complete",
            output_directory=str(output_dir),
            manifest_path=str(paths["manifest"]),
            checkpoint_path=str(paths["checkpoint"]),
            summary=completed_manifest["summary"],
        )
    assert writer is not None
    processed_this_run = 0
    phase_skip = phase_groups_completed

    def save_checkpoint(status: str, active_phase: str, completed: int) -> None:
        offsets = writer.flush()
        _atomic_json(paths["checkpoint"], _checkpoint_value(
            status=status,
            phase=active_phase,
            phase_groups_completed=completed,
            source_identity=source_identity,
            config_sha256=config_sha256,
            offsets=offsets,
            stats=stats,
        ))

    def process_phase(
        connection: sqlite3.Connection,
        *,
        active_phase: str,
        sql: str,
        key_fields: Sequence[str],
    ) -> tuple[bool, int]:
        nonlocal processed_this_run
        completed = phase_skip if phase == active_phase else 0
        for index, (group_key, rows) in enumerate(_stream_groups(
            connection.execute(sql), key_fields=key_fields, batch_size=config.batch_size
        )):
            if index < completed:
                continue
            try:
                records = (
                    _narrative_records(rows, config=config, config_sha256=config_sha256)
                    if active_phase == "narrative"
                    else _table_records(rows, config=config, config_sha256=config_sha256)
                )
                for record in records:
                    if int(record["token_count"]) > config.max_tokens:
                        raise ChunkGroupError("chunk_max_tokens_exceeded")
                    writer.write_chunk(record, stats)
            except ChunkGroupError as exc:
                writer.write_failure({
                    "chunk_version": CHUNK_VERSION,
                    "phase": active_phase,
                    "group_key": list(group_key),
                    "reason": str(exc),
                }, stats)
            completed += 1
            stats.groups_completed += 1
            processed_this_run += 1
            should_checkpoint = completed % config.checkpoint_every_groups == 0
            should_stop = max_groups is not None and processed_this_run >= max_groups
            if should_checkpoint or should_stop:
                save_checkpoint("checkpointed", active_phase, completed)
            if should_stop:
                return False, completed
        return True, completed

    if not resume:
        save_checkpoint("running", "narrative", 0)
    try:
        with closing(_readonly_connection(database)) as connection:
            _validate_source(connection)
            input_counts = _input_counts(connection)
            if phase == "narrative":
                finished, _ = process_phase(
                    connection,
                    active_phase="narrative",
                    sql=_NARRATIVE_SQL,
                    key_fields=("filing_id", "source_id", "section_path_json"),
                )
                if not finished:
                    return ChunkBuildResult(
                        status="checkpointed",
                        output_directory=str(output_dir),
                        manifest_path=None,
                        checkpoint_path=str(paths["checkpoint"]),
                        summary=_summary(stats, pdf_source_count=input_counts["pdf_source_count"]),
                    )
                phase = "table"
                phase_skip = 0
                save_checkpoint("running", "table", 0)
            if phase == "table":
                finished, _ = process_phase(
                    connection,
                    active_phase="table",
                    sql=_TABLE_SQL,
                    key_fields=("table_id",),
                )
                if not finished:
                    return ChunkBuildResult(
                        status="checkpointed",
                        output_directory=str(output_dir),
                        manifest_path=None,
                        checkpoint_path=str(paths["checkpoint"]),
                        summary=_summary(stats, pdf_source_count=input_counts["pdf_source_count"]),
                    )
                phase = "finalize"
                save_checkpoint("running", "finalize", 0)
    finally:
        writer.close()

    summary = _summary(stats, pdf_source_count=input_counts["pdf_source_count"])
    outputs = {
        key: {
            "path": str(paths[key]),
            "size_bytes": paths[key].stat().st_size,
            "sha256": _sha256_file(paths[key]),
        }
        for key in ("chunks", "links", "failures")
    }
    manifest: dict[str, object] = {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_version": CHUNK_VERSION,
        "status": "complete",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_identity": source_identity,
        "input_counts": input_counts,
        "config": config_value,
        "config_sha256": config_sha256,
        "outputs": outputs,
        "summary": summary,
        "embedding": {"status": "not_run", "model": None},
    }
    manifest["manifest_semantic_sha256"] = _semantic_sha256(manifest)
    _atomic_json(paths["manifest"], manifest)
    final_checkpoint = _checkpoint_value(
        status="complete",
        phase="complete",
        phase_groups_completed=0,
        source_identity=source_identity,
        config_sha256=config_sha256,
        offsets={key: int(outputs[key]["size_bytes"]) for key in ("chunks", "links", "failures")},
        stats=stats,
    )
    _atomic_json(paths["checkpoint"], final_checkpoint)
    return ChunkBuildResult(
        status="complete",
        output_directory=str(output_dir),
        manifest_path=str(paths["manifest"]),
        checkpoint_path=str(paths["checkpoint"]),
        summary=summary,
    )


__all__ = [
    "CHUNK_SCHEMA_VERSION",
    "CHUNK_VERSION",
    "DEFAULT_CONFIG_PATH",
    "TOKENIZER_VERSION",
    "ChunkBuildResult",
    "ChunkConfig",
    "build_embedding_chunks",
    "count_tokens",
    "load_chunk_config",
]
