"""Atomic, answer-safe sparse search projection for the immutable corpus."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .attestation import CorpusAttestation
from .retrieval_evaluation import retrieval_tokens


INDEX_SCHEMA_REVISION = "safe-search-v1"


SEARCH_SCHEMA = """
CREATE TABLE index_revision(
    revision TEXT PRIMARY KEY,
    base_sha256 TEXT NOT NULL,
    base_size_bytes INTEGER NOT NULL,
    built_at TEXT NOT NULL,
    row_count INTEGER NOT NULL
);
CREATE TABLE search_document(
    rowid INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    filing_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    company TEXT NOT NULL,
    issuer_name TEXT NOT NULL,
    listed_name TEXT NOT NULL,
    reporter_name TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    corp_code TEXT NOT NULL,
    doc_group TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    is_current INTEGER NOT NULL,
    lineage_status TEXT NOT NULL,
    is_correction INTEGER NOT NULL,
    fragment_type TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    text_normalized TEXT NOT NULL
);
CREATE INDEX search_document_company_time ON search_document(company,effective_from,effective_to);
CREATE VIRTUAL TABLE search_fts USING fts5(
    text_normalized,
    content='search_document',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 0'
);
CREATE VIRTUAL TABLE search_trigram USING fts5(
    evidence_id UNINDEXED,
    text_normalized,
    tokenize='trigram'
);
"""


@dataclass(slots=True)
class SearchIndexBuildResult:
    base_sha256: str = ""
    base_size_bytes: int = 0
    indexed_rows: int = 0
    trigram_rows: int = 0
    quick_check: str = ""
    promoted_output_path: str = ""
    elapsed_ms: int = 0
    revision: str = ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_stale_temp(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        raise IsADirectoryError(f"search index temporary path is a directory: {path}")
    path.unlink()


def build_search_index(
    base_database: Path,
    output: Path,
    attestation: CorpusAttestation,
) -> SearchIndexBuildResult:
    """Build and atomically promote a projection containing only safe fragments."""
    started = time.perf_counter()
    base_database = Path(base_database)
    output = Path(output)
    temp_path = output.with_name(output.name + ".tmp")
    _remove_stale_temp(temp_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    base_size = base_database.stat().st_size
    base_sha256 = _sha256_file(base_database)
    if base_size != attestation.size_bytes or base_sha256 != attestation.sha256:
        raise ValueError("base database does not match attestation")

    connection: sqlite3.Connection | None = None
    promoted = False
    result = SearchIndexBuildResult(
        base_sha256=base_sha256,
        base_size_bytes=base_size,
        promoted_output_path=str(output),
        revision=INDEX_SCHEMA_REVISION,
    )
    try:
        connection = sqlite3.connect(temp_path)
        connection.executescript(SEARCH_SCHEMA)
        with closing(sqlite3.connect(base_database)) as source_connection:
            source_connection.row_factory = sqlite3.Row
            rows = source_connection.execute(
            """SELECT fr.evidence_id,fr.filing_id,fr.source_id,
                      f.issuer_name,f.listed_name,f.reporter_name,f.stock_code,f.issuer_corp_code,
                      f.doc_group,
                      v.effective_from,v.effective_to,v.is_current,v.lineage_status,
                      f.is_correction,
                      fr.fragment_type,fr.locator_json,fr.text_normalized
               FROM fragment fr
               JOIN filing f ON f.filing_id=fr.filing_id
               JOIN source_document s ON s.source_id=fr.source_id
               JOIN filing_version v ON v.filing_id=fr.filing_id
               WHERE v.lineage_status IN ('root','resolved')
                 AND s.parse_status='success'
                 AND lower(s.detected_format) <> 'pdf'
                 AND COALESCE(CAST(json_extract(s.coverage_json,'$.image_reference_count') AS INTEGER),0)=0
               ORDER BY fr.evidence_id"""
            )
            indexed_rows = 0
            trigram_rows = 0
            for row in rows:
                connection.execute(
                    """INSERT INTO search_document(
                        evidence_id,filing_id,source_id,company,issuer_name,listed_name,reporter_name,
                        stock_code,corp_code,doc_group,effective_from,effective_to,is_current,
                        lineage_status,is_correction,fragment_type,locator_json,text_normalized
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["evidence_id"], row["filing_id"], row["source_id"], row["issuer_name"],
                        row["issuer_name"], row["listed_name"], row["reporter_name"], row["stock_code"],
                        row["issuer_corp_code"], row["doc_group"], row["effective_from"], row["effective_to"],
                        row["is_current"], row["lineage_status"], row["is_correction"], row["fragment_type"],
                        row["locator_json"], row["text_normalized"],
                    ),
                )
                indexed_rows += 1
                if str(row["fragment_type"]) in {"heading", "table_row"}:
                    connection.execute(
                        "INSERT INTO search_trigram(evidence_id,text_normalized) VALUES(?,?)",
                        (str(row["evidence_id"]), str(row["text_normalized"])),
                    )
                    trigram_rows += 1
        connection.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild')")
        connection.execute("PRAGMA optimize")
        result.indexed_rows = indexed_rows
        result.trigram_rows = trigram_rows

        # Detect a corpus changing while the projection is being built.  The
        # index revision records the identity that was actually projected.
        current_size = base_database.stat().st_size
        current_sha256 = _sha256_file(base_database)
        if current_size != attestation.size_bytes or current_sha256 != attestation.sha256:
            raise ValueError("base database changed while building search index")
        connection.execute(
            "INSERT INTO index_revision(revision,base_sha256,base_size_bytes,built_at,row_count) VALUES(?,?,?,?,?)",
            (INDEX_SCHEMA_REVISION, base_sha256, base_size, datetime.now(UTC).isoformat(), indexed_rows),
        )
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        result.quick_check = quick_check
        if quick_check != "ok":
            raise ValueError(f"search index quick_check failed: {quick_check}")
        connection.commit()
        connection.close()
        connection = None
        os.replace(temp_path, output)
        promoted = True
        result.elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        return result
    finally:
        if connection is not None:
            connection.close()
        if not promoted and temp_path.exists() and not temp_path.is_dir():
            temp_path.unlink()


def _quoted_match(token: str) -> str:
    # MATCH receives a quoted FTS phrase.  Parameters keep the value out of
    # SQL, and doubling quotes prevents FTS query operators from escaping it.
    return '"' + token.replace('"', '""') + '"'


def rrf_fuse(
    rankings: list[list[dict[str, object]]], *, limit: int, k: int = 60,
) -> list[dict[str, object]]:
    fused: dict[str, dict[str, object]] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            evidence_id = str(row["evidence_id"])
            target = fused.setdefault(evidence_id, {**row, "rrf_score": 0.0})
            target["rrf_score"] = float(target["rrf_score"]) + 1.0 / (k + rank)
    return sorted(
        fused.values(), key=lambda row: (-float(row["rrf_score"]), str(row["evidence_id"]))
    )[:limit]


class SafeSearchIndex:
    def __init__(self, path: Path, *, base_sha256: str, expected_base_size: int | None = None):
        self.path = Path(path)
        self.base_sha256 = str(base_sha256).lower()
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        try:
            with closing(sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
                revision = connection.execute(
                    "SELECT revision,base_sha256,base_size_bytes,row_count FROM index_revision ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if revision is None or str(revision[0]) != INDEX_SCHEMA_REVISION:
                    raise ValueError("search index schema revision mismatch")
                if str(revision[1]).lower() != self.base_sha256:
                    raise ValueError("search index base attestation mismatch")
                if expected_base_size is not None and int(revision[2]) != int(expected_base_size):
                    raise ValueError("search index base size mismatch")
                actual_rows = int(connection.execute("SELECT COUNT(*) FROM search_document").fetchone()[0])
                if actual_rows != int(revision[3]):
                    raise ValueError("search index row count mismatch")
                quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                if quick_check != "ok":
                    raise sqlite3.DatabaseError(f"search index quick_check failed: {quick_check}")
        except sqlite3.OperationalError:
            raise

    @staticmethod
    def _filters(company: str | None, as_of: str | None, correction_policy: str) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if company:
            clauses.append("(d.company=? OR d.issuer_name=? OR d.listed_name=? OR d.reporter_name=? OR d.stock_code=? OR d.corp_code=?)")
            params.extend([company] * 6)
        if correction_policy == "original":
            clauses.append("d.lineage_status='root'")
            if as_of is not None:
                clauses.append("d.effective_from<=? AND (d.effective_to IS NULL OR ? < d.effective_to)")
                params.extend([as_of, as_of])
        else:
            clauses.append("d.lineage_status IN ('root','resolved')")
            if correction_policy == "corrected":
                clauses.append("d.is_correction=1")
            if as_of is None:
                clauses.append("d.is_current=1")
            else:
                clauses.append("d.effective_from<=? AND (d.effective_to IS NULL OR ? < d.effective_to)")
                params.extend([as_of, as_of])
        return (" AND ".join(clauses) or "1=1"), params

    def _unicode_search(self, token: str, *, company: str | None, as_of: str | None, correction_policy: str, limit: int) -> list[dict[str, object]]:
        where, params = self._filters(company, as_of, correction_policy)
        with closing(sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""SELECT d.evidence_id,d.filing_id,d.source_id,d.company,d.stock_code,d.doc_group,
                           d.effective_from,d.effective_to,d.is_current,d.lineage_status,d.fragment_type,d.locator_json,
                           d.text_normalized,bm25(search_fts) AS score
                    FROM search_fts JOIN search_document d ON d.rowid=search_fts.rowid
                    WHERE search_fts MATCH ? AND {where}
                    ORDER BY score,d.evidence_id LIMIT ?""",
                [_quoted_match(token), *params, max(1, limit)],
            ).fetchall()
        return [{**dict(row), "matched_index": "unicode"} for row in rows]

    def _trigram_search(self, token: str, *, company: str | None, as_of: str | None, correction_policy: str, limit: int) -> list[dict[str, object]]:
        where, params = self._filters(company, as_of, correction_policy)
        with closing(sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""SELECT d.evidence_id,d.filing_id,d.source_id,d.company,d.stock_code,d.doc_group,
                           d.effective_from,d.effective_to,d.is_current,d.lineage_status,d.fragment_type,d.locator_json,
                           d.text_normalized,bm25(search_trigram) AS score
                    FROM search_trigram JOIN search_document d ON d.evidence_id=search_trigram.evidence_id
                    WHERE search_trigram MATCH ? AND {where}
                    ORDER BY score,d.evidence_id LIMIT ?""",
                [_quoted_match(token), *params, max(1, limit)],
            ).fetchall()
        return [{**dict(row), "matched_index": "trigram"} for row in rows]

    def search(
        self,
        question: str,
        *,
        company: str | None,
        as_of: str | None,
        limit: int = 30,
        correction_policy: str = "current",
    ) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        try:
            tokens = retrieval_tokens(question, company_names=[company] if company else None)
        except ValueError:
            return []
        # A bounded per-token cap prevents a long question from expanding into
        # an unbounded set of FTS candidates.
        per_token_limit = min(max(limit, 1), 100)
        rankings: list[list[dict[str, object]]] = []
        unicode_ids: set[str] = set()
        for token in tokens[:32]:
            ranking = self._unicode_search(token, company=company, as_of=as_of, correction_policy=correction_policy, limit=per_token_limit)
            rankings.append(ranking)
            unicode_ids.update(str(row["evidence_id"]) for row in ranking)
        if len(unicode_ids) < limit:
            for token in tokens[:32]:
                remaining = max(1, limit - len(unicode_ids))
                ranking = self._trigram_search(token, company=company, as_of=as_of, correction_policy=correction_policy, limit=min(remaining, 100))
                rankings.append(ranking)
                unicode_ids.update(str(row["evidence_id"]) for row in ranking)
                if len(unicode_ids) >= limit:
                    break
        return rrf_fuse(rankings, limit=limit)


__all__ = ["INDEX_SCHEMA_REVISION", "SEARCH_SCHEMA", "SafeSearchIndex", "SearchIndexBuildResult", "build_search_index", "rrf_fuse"]
