"""Deterministic, resumable embedding chunks built without mutating source evidence."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
from itertools import chain, groupby
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable, Iterator, Mapping, Sequence


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
_QUERY_STRATEGY = "chunk-keyset-v1"


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


def _chunk_identity_sha256(config: ChunkConfig) -> str:
    """Keep chunk IDs independent from page and checkpoint tuning."""
    value = asdict(config)
    value["batch_size"] = ChunkConfig.__dataclass_fields__["batch_size"].default
    value["checkpoint_every_groups"] = ChunkConfig.__dataclass_fields__["checkpoint_every_groups"].default
    return _semantic_sha256(value)


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


_SOURCE_PAGE_SQL = """
SELECT source_id,filing_id,detected_format,parse_status AS source_parse_status
FROM source_document
WHERE source_id > ?
ORDER BY source_id
LIMIT ?
"""


_SOURCE_RESUME_PAGE_SQL = """
SELECT source_id,filing_id,detected_format,parse_status AS source_parse_status
FROM source_document
WHERE source_id >= ?
ORDER BY source_id
LIMIT ?
"""


_NARRATIVE_PAGE_SQL = """
SELECT rowid AS fragment_rowid,evidence_id,filing_id,source_id,fragment_type,sequence_no,
       section_path_json,text_normalized
FROM fragment INDEXED BY idx_fragment_source
WHERE source_id=?
  AND (sequence_no,rowid) > (?,?)
  AND fragment_type <> 'table_row'
ORDER BY sequence_no,rowid
LIMIT ?
"""


_TABLE_PAGE_SQL = """
SELECT tr.table_id,tr.filing_id,tr.source_id,tr.sequence_no AS table_sequence_no,
       tr.section_path_json,tr.caption,tr.unit_text,tr.parse_status AS table_parse_status,
       s.detected_format,s.parse_status AS source_parse_status
FROM table_record tr
JOIN source_document s ON s.source_id=tr.source_id
WHERE tr.table_id > ?
ORDER BY tr.table_id
LIMIT ?
"""


_TABLE_CELL_PAGE_SQL = """
SELECT rowid AS cell_rowid,row_index,column_index,cell_kind,column_header_path_json,
       evidence_id,text_normalized
FROM table_cell INDEXED BY idx_cell_table
WHERE table_id=?
  AND (row_index,column_index,rowid) > (?,?,?)
ORDER BY row_index,column_index,rowid
LIMIT ?
"""


_TABLE_ROW_PAGE_SQL = """
SELECT rowid AS fragment_rowid,
       CAST(json_extract(locator_json,'$.row') AS INTEGER) AS row_index,
       evidence_id,text_normalized
FROM fragment INDEXED BY idx_fragment_table_row
WHERE table_id=?
  AND rowid>?
  AND fragment_type='table_row'
ORDER BY rowid
LIMIT ?
"""


def explain_chunk_query_plans(connection: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Return plans for every corpus-scale page query against the installed schema."""
    queries = {
        "source_page": (_SOURCE_PAGE_SQL, ("", 10)),
        "narrative_page": (_NARRATIVE_PAGE_SQL, ("source", -1, 0, 10)),
        "table_page": (_TABLE_PAGE_SQL, ("", 10)),
        "table_cell_page": (_TABLE_CELL_PAGE_SQL, ("table", -1, -1, 0, 10)),
        "table_row_page": (_TABLE_ROW_PAGE_SQL, ("table", 0, 10)),
    }
    return {
        name: tuple(str(row[3]) for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", params))
        for name, (sql, params) in queries.items()
    }


def _source_pages(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    resume_source_id: str | None,
) -> Iterator[sqlite3.Row]:
    cursor = resume_source_id or ""
    include_cursor = resume_source_id is not None
    while True:
        sql = _SOURCE_RESUME_PAGE_SQL if include_cursor else _SOURCE_PAGE_SQL
        batch = connection.execute(sql, (cursor, batch_size)).fetchmany(batch_size)
        if not batch:
            return
        for row in batch:
            yield row
        if len(batch) < batch_size:
            return
        cursor = str(batch[-1]["source_id"])
        include_cursor = False


def _narrative_rows(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    start_cursor: Mapping[str, object] | None,
) -> Iterator[dict[str, object]]:
    resume_source_id = str(start_cursor["source_id"]) if start_cursor else None
    for source in _source_pages(
        connection,
        batch_size=batch_size,
        resume_source_id=resume_source_id,
    ):
        source_id = str(source["source_id"])
        if start_cursor and source_id == resume_source_id:
            sequence_no = int(start_cursor.get("sequence_no", -1))
            fragment_rowid = int(start_cursor.get("fragment_rowid", 0))
        else:
            sequence_no = -1
            fragment_rowid = 0
        while True:
            batch = connection.execute(
                _NARRATIVE_PAGE_SQL,
                (source_id, sequence_no, fragment_rowid, batch_size),
            ).fetchmany(batch_size)
            if not batch:
                break
            for row in batch:
                yield {
                    **dict(row),
                    "detected_format": source["detected_format"],
                    "source_parse_status": source["source_parse_status"],
                }
            if len(batch) < batch_size:
                break
            sequence_no = int(batch[-1]["sequence_no"])
            fragment_rowid = int(batch[-1]["fragment_rowid"])


class _TrackedRows:
    def __init__(self, rows: Iterable[Mapping[str, object]]) -> None:
        self._rows = iter(rows)
        self.last: Mapping[str, object] | None = None

    def __iter__(self) -> Iterator[Mapping[str, object]]:
        for row in self._rows:
            self.last = row
            yield row

    def cursor(self) -> dict[str, object]:
        for _ in self:
            pass
        if self.last is None:
            raise RuntimeError("narrative_group_not_consumed")
        return {
            "source_id": str(self.last["source_id"]),
            "sequence_no": int(self.last["sequence_no"]),
            "fragment_rowid": int(self.last["fragment_rowid"]),
        }


def _narrative_groups(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    start_cursor: Mapping[str, object] | None,
) -> Iterator[tuple[tuple[str, ...], _TrackedRows]]:
    rows = _narrative_rows(connection, batch_size=batch_size, start_cursor=start_cursor)
    key = lambda row: (str(row["filing_id"]), str(row["source_id"]), str(row["section_path_json"]))
    for group_key, group_rows in groupby(rows, key=key):
        tracked = _TrackedRows(group_rows)
        yield group_key, tracked


def _table_pages(
    connection: sqlite3.Connection,
    *,
    batch_size: int,
    start_table_id: str | None,
) -> Iterator[sqlite3.Row]:
    table_id = start_table_id or ""
    while True:
        batch = connection.execute(_TABLE_PAGE_SQL, (table_id, batch_size)).fetchmany(batch_size)
        if not batch:
            return
        for row in batch:
            yield row
        if len(batch) < batch_size:
            return
        table_id = str(batch[-1]["table_id"])


def _table_cells(
    connection: sqlite3.Connection,
    *,
    table_id: str,
    batch_size: int,
) -> Iterator[sqlite3.Row]:
    row_index, column_index, cell_rowid = -1, -1, 0
    while True:
        batch = connection.execute(
            _TABLE_CELL_PAGE_SQL,
            (table_id, row_index, column_index, cell_rowid, batch_size),
        ).fetchmany(batch_size)
        if not batch:
            return
        for row in batch:
            yield row
        if len(batch) < batch_size:
            return
        row_index = int(batch[-1]["row_index"])
        column_index = int(batch[-1]["column_index"])
        cell_rowid = int(batch[-1]["cell_rowid"])


def _table_row_fragments(
    connection: sqlite3.Connection,
    *,
    table_id: str,
    batch_size: int,
) -> Iterator[sqlite3.Row]:
    fragment_rowid = 0
    previous_row_index = -1
    while True:
        batch = connection.execute(
            _TABLE_ROW_PAGE_SQL,
            (table_id, fragment_rowid, batch_size),
        ).fetchmany(batch_size)
        if not batch:
            return
        for row in batch:
            row_index = int(row["row_index"])
            if row_index < previous_row_index:
                raise ChunkGroupError("table_row_order_not_monotonic")
            previous_row_index = row_index
            yield row
        if len(batch) < batch_size:
            return
        fragment_rowid = int(batch[-1]["fragment_rowid"])


def _logical_row_groups(
    rows: Iterable[sqlite3.Row],
) -> Iterator[tuple[int, list[sqlite3.Row]]]:
    # DB fetches stay bounded; one logical row stays intact for complete evidence linkage.
    for row_index, group in groupby(rows, key=lambda row: int(row["row_index"])):
        yield row_index, list(group)


def _merged_table_rows(
    connection: sqlite3.Connection,
    *,
    table_id: str,
    batch_size: int,
) -> Iterator[tuple[int, list[sqlite3.Row], list[sqlite3.Row]]]:
    cell_groups = iter(_logical_row_groups(
        _table_cells(connection, table_id=table_id, batch_size=batch_size),
    ))
    fragment_groups = iter(_logical_row_groups(
        _table_row_fragments(connection, table_id=table_id, batch_size=batch_size),
    ))
    cell_item = next(cell_groups, None)
    fragment_item = next(fragment_groups, None)
    while cell_item is not None or fragment_item is not None:
        row_index = min(
            item[0] for item in (cell_item, fragment_item) if item is not None
        )
        cells = cell_item[1] if cell_item is not None and cell_item[0] == row_index else []
        fragments = fragment_item[1] if fragment_item is not None and fragment_item[0] == row_index else []
        yield row_index, cells, fragments
        if cell_item is not None and cell_item[0] == row_index:
            cell_item = next(cell_groups, None)
        if fragment_item is not None and fragment_item[0] == row_index:
            fragment_item = next(fragment_groups, None)


def _narrative_records(
    rows: Iterable[Mapping[str, object]],
    *,
    config: ChunkConfig,
    config_sha256: str,
) -> Iterator[dict[str, object]]:
    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration:
        return
    section = _section_path(first["section_path_json"])
    source_format = _source_format(first["detected_format"])
    quality_status = "unverified" if source_format == "pdf" or first["source_parse_status"] != "success" else "verified"
    prefix = _prefix_tokens(section_path=section, config=config)
    units = (
        TextUnit(str(row["text_normalized"]), (str(row["evidence_id"]),), int(row["sequence_no"]))
        for row in chain((first,), iterator) if str(row["text_normalized"]).strip()
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
    connection: sqlite3.Connection,
    table: Mapping[str, object],
    *,
    config: ChunkConfig,
    config_sha256: str,
) -> Iterator[dict[str, object]]:
    section = _section_path(table["section_path_json"])
    source_format = _source_format(table["detected_format"])
    merged_rows = iter(_merged_table_rows(
        connection,
        table_id=str(table["table_id"]),
        batch_size=config.batch_size,
    ))
    header_texts: list[str] = []
    header_ids: list[str] = []
    first_body: tuple[int, list[sqlite3.Row], list[sqlite3.Row]] | None = None
    for row in merged_rows:
        _, cells, fragments = row
        if any(
            str(cell["cell_kind"]) == "data" and str(cell["text_normalized"]).strip()
            for cell in cells
        ):
            first_body = row
            break
        for cell in cells:
            text = str(cell["text_normalized"]).strip()
            if not text:
                continue
            if text not in header_texts:
                header_texts.append(text)
            header_ids.append(str(cell["evidence_id"]))
            for value in _json_string_list(cell["column_header_path_json"]):
                if value and value not in header_texts:
                    header_texts.append(value)
        header_ids.extend(str(fragment["evidence_id"]) for fragment in fragments)
        if count_tokens(" | ".join(header_texts)) >= config.max_tokens:
            raise ChunkGroupError("table_header_exceeds_token_budget")
    if first_body is None:
        raise ChunkGroupError("table_without_data_rows")

    table_context = {
        "caption": table["caption"],
        "header": " | ".join(header_texts),
        "unit": table["unit_text"],
        "header_evidence_ids": sorted(set(header_ids)),
    }
    prefix = _prefix_tokens(section_path=section, config=config, table_context=table_context)

    def body_units() -> Iterator[TextUnit]:
        emitted = False
        for row_index, cells, fragments in chain((first_body,), merged_rows):
            fragment_texts = [
                str(fragment["text_normalized"]).strip()
                for fragment in fragments if str(fragment["text_normalized"]).strip()
            ]
            text = " | ".join(fragment_texts)
            if not text:
                text = " | ".join(
                    str(cell["text_normalized"]).strip()
                    for cell in cells if str(cell["text_normalized"]).strip()
                )
            evidence_ids = [str(cell["evidence_id"]) for cell in cells]
            evidence_ids.extend(str(fragment["evidence_id"]) for fragment in fragments)
            if text:
                emitted = True
                yield TextUnit(text, tuple(sorted(set(evidence_ids))), row_index)
        if not emitted:
            raise ChunkGroupError("table_without_chunkable_rows")

    quality_status = (
        "verified"
        if source_format != "pdf"
        and table["source_parse_status"] == "success"
        and table["table_parse_status"] == "success"
        else "unverified"
    )
    table_verified = source_format != "pdf" and table["table_parse_status"] == "success"
    for ordinal, body in enumerate(_chunk_body_tokens(body_units(), prefix_count=len(prefix), config=config)):
        yield _chunk_record(
            filing_id=str(table["filing_id"]),
            source_id=str(table["source_id"]),
            source_format=source_format,
            quality_status=quality_status,
            section_path=section,
            table_id=str(table["table_id"]),
            chunk_kind="table",
            table_structure_verified=table_verified,
            ordinal=ordinal,
            prefix=prefix,
            body=body,
            config_sha256=config_sha256,
        )


def _summary(stats: ChunkStats, *, pdf_source_count: int | None) -> dict[str, object]:
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
    phase_cursor: Mapping[str, object] | None,
    source_identity: Mapping[str, object],
    config_sha256: str,
    offsets: Mapping[str, int],
    stats: ChunkStats,
) -> dict[str, object]:
    return {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_version": CHUNK_VERSION,
        "status": status,
        "query_strategy": _QUERY_STRATEGY,
        "phase": phase,
        "phase_groups_completed": phase_groups_completed,
        "phase_cursor": dict(phase_cursor) if phase_cursor is not None else None,
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
) -> tuple[
    _OutputWriter | None,
    str,
    int,
    Mapping[str, object] | None,
    ChunkStats,
    dict[str, object] | None,
]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {key: output_dir / name for key, name in _OUTPUT_NAMES.items()}
    if resume:
        checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
        if checkpoint.get("source_identity") != dict(source_identity):
            raise ValueError("checkpoint_source_identity_mismatch")
        if checkpoint.get("config_sha256") != config_sha256:
            raise ValueError("checkpoint_config_mismatch")
        strategy = checkpoint.get("query_strategy")
        completed_groups = int(checkpoint.get("phase_groups_completed", 0))
        if strategy not in (None, _QUERY_STRATEGY):
            raise ValueError("checkpoint_query_strategy_mismatch")
        if strategy is None and completed_groups:
            raise ValueError("checkpoint_query_strategy_mismatch")
        if checkpoint.get("status") == "complete":
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            return None, "complete", 0, None, ChunkStats.from_dict(checkpoint["stats"]), manifest
        phase_cursor = checkpoint.get("phase_cursor")
        if phase_cursor is not None and not isinstance(phase_cursor, dict):
            raise ValueError("checkpoint_phase_cursor_invalid")
        writer = _OutputWriter(output_dir, offsets=checkpoint.get("output_offsets", {}))
        return (
            writer,
            str(checkpoint.get("phase", "narrative")),
            completed_groups,
            phase_cursor,
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
    return _OutputWriter(output_dir), "narrative", 0, None, ChunkStats(), None


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
    chunk_identity_sha256 = _chunk_identity_sha256(config)
    source_identity = _database_identity(database)
    writer, phase, phase_groups_completed, phase_cursor, stats, completed_manifest = _prepare_output(
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

    def save_checkpoint(
        status: str,
        active_phase: str,
        completed: int,
        cursor: Mapping[str, object] | None,
    ) -> None:
        offsets = writer.flush()
        _atomic_json(paths["checkpoint"], _checkpoint_value(
            status=status,
            phase=active_phase,
            phase_groups_completed=completed,
            phase_cursor=cursor,
            source_identity=source_identity,
            config_sha256=config_sha256,
            offsets=offsets,
            stats=stats,
        ))

    def process_work(
        *,
        active_phase: str,
        work: Iterable[tuple[
            tuple[str, ...],
            Iterable[Mapping[str, object]],
            Callable[[], Mapping[str, object]],
        ]],
    ) -> tuple[bool, int, Mapping[str, object] | None]:
        nonlocal processed_this_run
        completed = phase_groups_completed if phase == active_phase else 0
        cursor = phase_cursor if phase == active_phase else None
        for group_key, records, cursor_factory in work:
            try:
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
            cursor = cursor_factory()
            completed += 1
            stats.groups_completed += 1
            processed_this_run += 1
            should_checkpoint = completed % config.checkpoint_every_groups == 0
            should_stop = max_groups is not None and processed_this_run >= max_groups
            if should_checkpoint or should_stop:
                save_checkpoint("checkpointed", active_phase, completed, cursor)
            if should_stop:
                return False, completed, cursor
        return True, completed, cursor

    if not resume:
        save_checkpoint("running", "narrative", 0, None)
    try:
        with closing(_readonly_connection(database)) as connection:
            _validate_source(connection)
            if phase == "narrative":
                def narrative_work() -> Iterator[tuple[
                    tuple[str, ...],
                    Iterable[Mapping[str, object]],
                    Callable[[], Mapping[str, object]],
                ]]:
                    for group_key, tracked in _narrative_groups(
                        connection,
                        batch_size=config.batch_size,
                        start_cursor=phase_cursor,
                    ):
                        yield (
                            group_key,
                            _narrative_records(
                                tracked,
                                config=config,
                                config_sha256=chunk_identity_sha256,
                            ),
                            tracked.cursor,
                        )

                finished, _, _ = process_work(
                    active_phase="narrative",
                    work=narrative_work(),
                )
                if not finished:
                    return ChunkBuildResult(
                        status="checkpointed",
                        output_directory=str(output_dir),
                        manifest_path=None,
                        checkpoint_path=str(paths["checkpoint"]),
                        summary=_summary(stats, pdf_source_count=None),
                    )
                phase = "table"
                phase_groups_completed = 0
                phase_cursor = None
                save_checkpoint("running", "table", 0, None)
            if phase == "table":
                start_table_id = str(phase_cursor["table_id"]) if phase_cursor else None

                def table_work() -> Iterator[tuple[
                    tuple[str, ...],
                    Iterable[Mapping[str, object]],
                    Callable[[], Mapping[str, object]],
                ]]:
                    for table in _table_pages(
                        connection,
                        batch_size=config.batch_size,
                        start_table_id=start_table_id,
                    ):
                        table_id = str(table["table_id"])
                        yield (
                            (table_id,),
                            _table_records(
                                connection,
                                table,
                                config=config,
                                config_sha256=chunk_identity_sha256,
                            ),
                            lambda table_id=table_id: {"table_id": table_id},
                        )

                finished, _, _ = process_work(
                    active_phase="table",
                    work=table_work(),
                )
                if not finished:
                    return ChunkBuildResult(
                        status="checkpointed",
                        output_directory=str(output_dir),
                        manifest_path=None,
                        checkpoint_path=str(paths["checkpoint"]),
                        summary=_summary(stats, pdf_source_count=None),
                    )
                phase = "finalize"
                phase_groups_completed = 0
                phase_cursor = None
                save_checkpoint("running", "finalize", 0, None)
            input_counts = _input_counts(connection)
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
        "chunk_identity_sha256": chunk_identity_sha256,
        "query_strategy": _QUERY_STRATEGY,
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
        phase_cursor=None,
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
    "explain_chunk_query_plans",
    "load_chunk_config",
]
