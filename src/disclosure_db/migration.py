from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evaluation import RULE_LINEAGE_UNRESOLVED, RULE_MISSING_ORIGINAL, RULE_PDF_TABLE_UNVALIDATED
from .lineage import build_lineage
from .quality import add_issue
from .schema import FTS_SQL


SEMANTIC_MIGRATION_ID = "semantic_layer_v1"


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """SELECT type,name,tbl_name,coalesce(sql,'')
           FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%'
           ORDER BY type,name"""
    ).fetchall()
    payload = json.dumps([tuple(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def core_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "filing", "source_document", "fragment", "table_record", "table_cell", "fact",
        "fact_evidence", "filing_event", "filing_version", "quality_issue", "fragment_fts",
    )
    existing = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }
    return {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) if table in existing else 0
        for table in tables
    }


def _logical_fingerprint(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        "schema_sha256": schema_sha256(connection),
        "core_counts": core_counts(connection),
        "latest_pipeline_run": connection.execute(
            "SELECT run_id,status,finished_at FROM pipeline_run ORDER BY started_at DESC LIMIT 1"
        ).fetchone(),
    }


def copy_database(
    source: Path,
    output: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    verify_source: bool = True,
) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("migration output must differ from the structural SSOT")
    if output.exists():
        raise FileExistsError(f"migration output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
        if verify_source and source_connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("source database quick_check failed")
        before = _logical_fingerprint(source_connection)
        with closing(sqlite3.connect(output)) as output_connection:
            def callback(status: int, remaining: int, total: int) -> None:
                del status
                if progress is not None:
                    progress(total - remaining, total)

            source_connection.backup(output_connection, pages=65_536, progress=callback, sleep=0.05)
    return before


def _refresh_semantic_quality_issues(connection: sqlite3.Connection) -> dict[str, int]:
    run_row = connection.execute(
        "SELECT run_id FROM pipeline_run WHERE status='success' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if run_row is None:
        raise RuntimeError("successful pipeline_run is required for quality issue lineage")
    run_id = str(run_row[0])
    connection.execute(
        """DELETE FROM quality_issue
           WHERE rule_id IN ('lineage_unresolved','correction_lineage_unresolved','lineage_missing_original',
                             'missing_original','pdf_table_structure_unvalidated')"""
    )
    counts = {"lineage": 0, "pdf": 0}
    for filing_id, status in connection.execute(
        "SELECT filing_id,lineage_status FROM filing_version WHERE lineage_status IN ('unresolved','missing_original')"
    ):
        add_issue(
            connection, run_id, "warning", "consistency", "filing", str(filing_id),
            RULE_LINEAGE_UNRESOLVED if status == "unresolved" else RULE_MISSING_ORIGINAL,
            f"Correction lineage is {status}",
        )
        counts["lineage"] += 1
    for source_id, filing_id in connection.execute(
        """SELECT source_id,filing_id FROM source_document
           WHERE instr(warnings_json,'pdf_table_structure_not_validated') > 0"""
    ):
        add_issue(
            connection, run_id, "warning", "coverage", "source", str(source_id),
            RULE_PDF_TABLE_UNVALIDATED,
            "PDF text was extracted without validated table structure",
            {"filing_id": filing_id},
        )
        counts["pdf"] += 1
    return counts


def apply_semantic_migration(
    database: Path,
    *,
    source_database: Path,
    migration_sql: Path,
    rebuild_lineage: bool = True,
    rebuild_fts: bool = True,
) -> dict[str, object]:
    script_text = migration_sql.read_text(encoding="utf-8")
    script_sha = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        before = _logical_fingerprint(connection)
        old_versions = {
            str(row[0]): tuple(row[1:])
            for row in connection.execute(
                """SELECT filing_id,event_id,parent_filing_id,lineage_status,is_current,effective_from,effective_to
                   FROM filing_version"""
            )
        }
        try:
            connection.executescript(script_text)
            renamed_facts = connection.execute(
                "UPDATE fact SET fact_type='event_kv_candidate' WHERE fact_type='table_field_candidate'"
            ).rowcount
            lineage_stats: dict[str, int] | None = None
            changed_versions = 0
            if rebuild_lineage:
                lineage_stats = build_lineage(connection)
                new_versions = {
                    str(row[0]): tuple(row[1:])
                    for row in connection.execute(
                        """SELECT filing_id,event_id,parent_filing_id,lineage_status,is_current,effective_from,effective_to
                           FROM filing_version"""
                    )
                }
                changed_versions = sum(old_versions.get(key) != value for key, value in new_versions.items())
            if rebuild_fts:
                connection.executescript(FTS_SQL)
                connection.execute("INSERT INTO fragment_fts(fragment_fts,rank) VALUES('integrity-check',1)")
            quality_refresh = _refresh_semantic_quality_issues(connection)
            connection.execute("PRAGMA user_version=1")
            applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            details = {
                "renamed_fact_rows": renamed_facts,
                "lineage_stats": lineage_stats,
                "changed_filing_versions": changed_versions,
                "fts_rebuilt": rebuild_fts,
                "quality_refresh": quality_refresh,
            }
            connection.execute(
                """INSERT OR REPLACE INTO schema_migration(
                       migration_id,applied_at,script_sha256,source_database,details_json
                   ) VALUES(?,?,?,?,?)""",
                (
                    SEMANTIC_MIGRATION_ID, applied_at, script_sha,
                    portable_path(source_database), json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        foreign_key_violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        after = _logical_fingerprint(connection)
        semantic_counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("schema_migration", "financial_fact", "financial_fact_evidence")
        }
        trigger_names = [
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        ]
    return {
        "migration_id": SEMANTIC_MIGRATION_ID,
        "source_database": portable_path(source_database),
        "output_database": portable_path(database),
        "output_bytes": database.stat().st_size,
        "quick_check": quick_check,
        "foreign_key_violations": len(foreign_key_violations),
        "before": before,
        "after": after,
        "semantic_counts": semantic_counts,
        "triggers": trigger_names,
        "details": details,
    }
