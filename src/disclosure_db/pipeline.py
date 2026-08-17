from __future__ import annotations

import codecs
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .contracts import PARSER_NAME, PARSER_VERSION, ParseResult
from .identifiers import normalize_report_name, source_id, stable_digest
from .lineage import build_lineage
from .parsers import parse_markup, parse_pdf
from .quality import evaluate_quality
from .schema import create_indexes, create_schema
from .serving import version_filter_sql


MANIFEST_FIELDS = {
    "doc_id",
    "corp_code",
    "corp_name",
    "listed_name",
    "stock_code",
    "industry",
    "sector",
    "doc_group",
    "doc_subtype",
    "report_nm",
    "is_correction",
    "rcept_no",
    "rcept_dt",
    "flr_nm",
    "base_year",
    "base_month",
    "file_path",
    "file_format",
    "n_files",
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = MANIFEST_FIELDS - row.keys()
            if missing:
                raise ValueError(f"manifest line {line_no} missing fields: {sorted(missing)}")
            rows.append(row)
    doc_ids = [str(row["doc_id"]) for row in rows]
    receipt_numbers = [str(row["rcept_no"]) for row in rows]
    if len(doc_ids) != len(set(doc_ids)):
        raise ValueError("manifest doc_id is not unique")
    if len(receipt_numbers) != len(set(receipt_numbers)):
        raise ValueError("manifest rcept_no is not unique")
    return rows


def sniff_format(path: Path) -> tuple[str, str | None, str | None]:
    head = path.read_bytes()[:8192]
    if head.startswith(b"%PDF"):
        return "pdf", None, None
    try:
        # A fixed byte window can end in the middle of a UTF-8 code point. Incremental decoding
        # validates all complete code points without misclassifying that trailing partial sequence.
        text = codecs.getincrementaldecoder("utf-8")(errors="strict").decode(head, final=False)
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            text = head.decode("cp949", errors="strict")
            encoding = "cp949"
        except UnicodeDecodeError:
            return "unknown", None, None
    lowered = text.lstrip("\ufeff\r\n\t ").lower()
    declared = None
    for token in ("charset=", "encoding="):
        if token in lowered[:1000]:
            declared = lowered.split(token, 1)[1].split()[0].strip("'\"?>;")[:40]
            break
    if "<html" in lowered[:3000]:
        kind = "viewer_html" if path.name.endswith("_viewer.html") else "exchange_html"
        return kind, declared, encoding
    if "<document" in lowered[:5000]:
        return "dart_xml", declared, encoding
    return "unknown", declared, encoding


def source_role(path: Path, receipt_no: str) -> str:
    stem = path.stem
    if path.suffix.lower() == ".pdf":
        return "pdf_main"
    if path.name.endswith("_viewer.html"):
        return "viewer_toc"
    if stem == receipt_no:
        return "main"
    if stem.endswith("_00760"):
        return "audit_separate_00760"
    if stem.endswith("_00761"):
        return "audit_consolidated_00761"
    return f"attachment_{stable_digest(path.name, length=12)}"


def source_files(corpus_root: Path, row: dict[str, object]) -> list[Path]:
    directory = corpus_root / str(row["file_path"])
    if not directory.is_dir():
        raise FileNotFoundError(f"manifest source directory does not exist: {directory}")
    return sorted(path for path in directory.iterdir() if path.is_file())


def _insert_filing(connection: sqlite3.Connection, row: dict[str, object]) -> None:
    filed = str(row["rcept_dt"])
    filed_at = f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}"
    subtype = row["doc_subtype"]
    connection.execute(
        """INSERT INTO filing(
               filing_id,doc_id,issuer_corp_code,stock_code,issuer_name,listed_name,reporter_name,industry,sector,
               doc_group,doc_subtype_raw,doc_subtype_normalized,report_name_raw,report_name_normalized,
               filed_at,base_year,base_month,is_correction,file_format_declared,source_file_count
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["rcept_no"],
            row["doc_id"],
            row["corp_code"],
            row["stock_code"],
            row["corp_name"],
            row["listed_name"],
            row["flr_nm"],
            row["industry"],
            row["sector"],
            row["doc_group"],
            subtype,
            subtype,
            row["report_nm"],
            normalize_report_name(str(row["report_nm"])),
            filed_at,
            row["base_year"],
            row["base_month"],
            int(bool(row["is_correction"])),
            row["file_format"],
            row["n_files"],
        ),
    )


def _insert_parse_result(
    connection: sqlite3.Connection,
    *,
    row: dict[str, object],
    path: Path,
    corpus_root: Path,
    src_id: str,
    source_sha: str,
    role: str,
    detected_format: str,
    declared_encoding: str | None,
    detected_encoding: str | None,
    result: ParseResult,
) -> None:
    relative_path = path.relative_to(corpus_root).as_posix()
    coverage = {
        "fragment_count": len(result.fragments),
        "table_count": len(result.tables),
        "cell_count": len(result.cells),
        "fact_count": len(result.facts),
        "page_count": result.page_count,
        "tree_text_chars": result.tree_text_chars,
        "extracted_text_chars": result.extracted_text_chars,
        "image_reference_count": result.image_reference_count,
    }
    connection.execute(
        """INSERT INTO source_document(
               source_id,filing_id,role,source_path,extension,detected_format,declared_encoding,
               detected_encoding,sha256,byte_size,modified_at,parser_name,parser_version,parse_status,
               strict_xml_ok,parser_error_count,warnings_json,coverage_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            src_id,
            row["rcept_no"],
            role,
            relative_path,
            path.suffix.lower(),
            detected_format,
            declared_encoding,
            detected_encoding,
            source_sha,
            path.stat().st_size,
            datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
            PARSER_NAME,
            PARSER_VERSION,
            result.parse_status,
            None if result.strict_xml_ok is None else int(result.strict_xml_ok),
            len(result.parser_errors),
            json.dumps(result.warnings, ensure_ascii=False),
            json.dumps(coverage, ensure_ascii=False),
        ),
    )
    connection.executemany(
        """INSERT INTO parser_error(
               error_id,source_id,ordinal,phase,level,error_type,line,column_no,message
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                f"pe_{stable_digest(src_id, index, error.get('phase'), error.get('line'), error.get('column'), error.get('message'), length=28)}",
                src_id,
                index,
                error.get("phase", "recovery"),
                error.get("level", "ERROR"),
                error.get("type", "UNKNOWN"),
                error.get("line"),
                error.get("column"),
                error.get("message", ""),
            )
            for index, error in enumerate(result.parser_errors)
        ],
    )
    connection.executemany(
        """INSERT INTO fragment(
               evidence_id,filing_id,source_id,fragment_type,sequence_no,section_path_json,page_no,
               table_id,locator_json,text_raw,text_normalized,parser_version
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                item.evidence_id,
                item.filing_id,
                item.source_id,
                item.fragment_type,
                item.sequence_no,
                json.dumps(item.section_path, ensure_ascii=False),
                item.page_no,
                item.table_id,
                json.dumps(item.locator, ensure_ascii=False, sort_keys=True),
                item.text_raw,
                item.text_normalized,
                PARSER_VERSION,
            )
            for item in result.fragments
        ],
    )
    connection.executemany(
        """INSERT INTO table_record(
               table_id,filing_id,source_id,sequence_no,section_path_json,caption,unit_text,row_count,
               column_count,parse_status,locator_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                item.table_id,
                item.filing_id,
                item.source_id,
                item.sequence_no,
                json.dumps(item.section_path, ensure_ascii=False),
                item.caption,
                item.unit_text,
                item.row_count,
                item.column_count,
                item.parse_status,
                json.dumps(item.locator, ensure_ascii=False, sort_keys=True),
            )
            for item in result.tables
        ],
    )
    connection.executemany(
        """INSERT INTO table_cell(
               evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,colspan,
               cell_kind,row_header_path_json,column_header_path_json,locator_json,text_raw,text_normalized,parser_version
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                item.evidence_id,
                item.table_id,
                item.source_id,
                item.filing_id,
                item.row_index,
                item.column_index,
                item.rowspan,
                item.colspan,
                item.cell_kind,
                json.dumps(item.row_header_path, ensure_ascii=False),
                json.dumps(item.column_header_path, ensure_ascii=False),
                json.dumps(item.locator, ensure_ascii=False, sort_keys=True),
                item.text_raw,
                item.text_normalized,
                PARSER_VERSION,
            )
            for item in result.cells
        ],
    )
    for fact in result.facts:
        connection.execute(
            """INSERT INTO fact(
                   fact_id,filing_id,fact_type,subject,predicate,value_raw,unit,extraction_method,validation_status
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                fact.fact_id,
                fact.filing_id,
                fact.fact_type,
                fact.subject,
                fact.predicate,
                fact.value_raw,
                fact.unit,
                fact.extraction_method,
                fact.validation_status,
            ),
        )
        connection.executemany(
            "INSERT INTO fact_evidence(fact_id,evidence_id) VALUES(?,?)",
            [(fact.fact_id, evidence) for evidence in fact.evidence_ids],
        )


def build_database(
    corpus_root: Path,
    output_db: Path,
    *,
    max_filings: int | None = None,
    progress_every: int = 100,
    build_fts: bool = True,
) -> dict[str, object]:
    corpus_root = corpus_root.resolve()
    manifest_path = corpus_root / "manifest.jsonl"
    rows = load_manifest(manifest_path)
    if max_filings is not None:
        rows = rows[:max_filings]
    output_db = output_db.resolve()
    output_db.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix=f"{output_db.stem}.", suffix=".tmp.sqlite", dir=output_db.parent)
    os.close(temp_fd)
    temp_path = Path(temp_name)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{stable_digest(sha256_file(manifest_path), max_filings, length=8)}"
    started = time.perf_counter()
    source_counter: Counter[str] = Counter()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temp_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-200000")
        create_schema(connection)
        connection.execute(
            "INSERT INTO pipeline_run(run_id,started_at,parser_name,parser_version,corpus_root,manifest_sha256,status) VALUES(?,?,?,?,?,?,?)",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                PARSER_NAME,
                PARSER_VERSION,
                str(corpus_root),
                sha256_file(manifest_path),
                "running",
            ),
        )
        connection.commit()
        for index, row in enumerate(rows, 1):
            files = source_files(corpus_root, row)
            if len(files) != int(row["n_files"]):
                raise ValueError(f"{row['rcept_no']} n_files mismatch: manifest={row['n_files']} actual={len(files)}")
            _insert_filing(connection, row)
            for path in files:
                detected_format, declared_encoding, detected_encoding = sniff_format(path)
                source_counter[detected_format] += 1
                source_sha = sha256_file(path)
                role = source_role(path, str(row["rcept_no"]))
                src_id = source_id(str(row["rcept_no"]), source_sha, role)
                if detected_format == "pdf":
                    result = parse_pdf(
                        path,
                        filing_id=str(row["rcept_no"]),
                        source_id=src_id,
                        source_sha256=source_sha,
                    )
                elif detected_format in {"dart_xml", "exchange_html", "viewer_html"}:
                    result = parse_markup(
                        path,
                        filing_id=str(row["rcept_no"]),
                        source_id=src_id,
                        source_sha256=source_sha,
                        detected_format=detected_format,
                        issuer_name=str(row["corp_name"]),
                        doc_group=str(row["doc_group"]),
                    )
                    if role == "viewer_toc" and result.extracted_text_chars < 1000:
                        result.parse_status = "partial"
                        result.warnings.append("viewer_shell_without_substantive_body")
                else:
                    result = ParseResult(parse_status="unsupported", strict_xml_ok=None, warnings=["unknown_source_format"])
                _insert_parse_result(
                    connection,
                    row=row,
                    path=path,
                    corpus_root=corpus_root,
                    src_id=src_id,
                    source_sha=source_sha,
                    role=role,
                    detected_format=detected_format,
                    declared_encoding=declared_encoding,
                    detected_encoding=detected_encoding,
                    result=result,
                )
            if index % progress_every == 0:
                connection.commit()
                elapsed = time.perf_counter() - started
                print(json.dumps({"progress": index, "total": len(rows), "elapsed_seconds": round(elapsed, 1)}, ensure_ascii=False), flush=True)
        connection.commit()
        lineage_stats = build_lineage(connection)
        connection.commit()
        quality_stats = evaluate_quality(connection, run_id, len(rows))
        create_indexes(connection, build_fts=build_fts)
        connection.execute("PRAGMA optimize")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        elapsed = time.perf_counter() - started
        stats: dict[str, object] = {
            **quality_stats,
            "lineage": lineage_stats,
            "detected_formats": dict(source_counter),
            "elapsed_seconds": round(elapsed, 3),
            "database_bytes": 0,
            "run_id": run_id,
        }
        connection.execute(
            "UPDATE pipeline_run SET finished_at=?,status='success',stats_json=? WHERE run_id=?",
            (datetime.now(UTC).isoformat(), json.dumps(stats, ensure_ascii=False), run_id),
        )
        connection.commit()
        connection.close()
        if output_db.exists():
            backup = output_db.with_suffix(output_db.suffix + ".previous")
            if backup.exists():
                backup.unlink()
            output_db.replace(backup)
        shutil.move(str(temp_path), output_db)
        stats["database_bytes"] = output_db.stat().st_size
        with closing(sqlite3.connect(output_db)) as final_connection:
            final_connection.execute(
                "UPDATE pipeline_run SET stats_json=? WHERE run_id=?",
                (json.dumps(stats, ensure_ascii=False), run_id),
            )
            final_connection.commit()
        return stats
    except Exception:
        if connection is not None:
            connection.close()
        if temp_path.exists():
            temp_path.unlink()
        raise


def export_inventory(database: Path, output_jsonl: Path) -> int:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with closing(sqlite3.connect(database)) as connection, output_jsonl.open("w", encoding="utf-8", newline="\n") as output:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(
            """SELECT source_id,filing_id,role,source_path,detected_format,detected_encoding,sha256,
                      byte_size,modified_at,parse_status,strict_xml_ok,parser_error_count,warnings_json,coverage_json
               FROM source_document ORDER BY filing_id,source_path"""
        ):
            item = dict(row)
            item["warnings"] = json.loads(item.pop("warnings_json"))
            item["coverage"] = json.loads(item.pop("coverage_json"))
            output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def export_gold_candidates(database: Path, output_jsonl: Path, per_stratum: int = 2) -> int:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        candidates = list(
            connection.execute(
                """WITH ranked AS (
                       SELECT f.*,s.detected_format,s.parse_status,
                              CAST(json_extract(s.coverage_json,'$.extracted_text_chars') AS INTEGER) AS extracted_chars,
                              ROW_NUMBER() OVER (
                                  PARTITION BY f.doc_group,f.is_correction,s.detected_format
                                  ORDER BY abs(CAST(substr(f.filing_id,-6) AS INTEGER)),f.filing_id
                              ) AS rn
                       FROM filing f JOIN source_document s ON s.filing_id=f.filing_id AND s.role IN ('main','pdf_main')
                   )
                   SELECT * FROM ranked WHERE rn <= ?
                   ORDER BY doc_group,is_correction,detected_format,filing_id""",
                (per_stratum,),
            )
        )
    with output_jsonl.open("w", encoding="utf-8", newline="\n") as output:
        for row in candidates:
            item = {
                "candidate_id": f"goldcand_{row['filing_id']}",
                "status": "needs_human_annotation",
                "filing_id": row["filing_id"],
                "issuer_corp_code": row["issuer_corp_code"],
                "issuer_name": row["issuer_name"],
                "doc_group": row["doc_group"],
                "doc_subtype": row["doc_subtype_normalized"],
                "report_name": row["report_name_raw"],
                "filed_at": row["filed_at"],
                "is_correction": bool(row["is_correction"]),
                "detected_format": row["detected_format"],
                "parse_status": row["parse_status"],
                "extracted_chars": row["extracted_chars"],
                "annotation_tasks": [
                    "verify_parser_coverage",
                    "verify_section_paths",
                    "verify_table_grid_and_units",
                    "write_question_answer_evidence_triplet",
                ],
            }
            output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(candidates)


def query_database(
    database: Path,
    query: str,
    *,
    company: str | None = None,
    limit: int = 10,
    as_of: str | None = None,
    include_unsafe: bool = False,
    filing_ids: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    """Search evidence with safe current/PIT lineage filtering by default.

    `include_unsafe=True` is for parser/lineage audits only. Answer-producing callers must keep
    the default so isolated unresolved corrections cannot masquerade as effective versions.
    """
    if limit <= 0:
        return []
    if filing_ids is not None and not filing_ids:
        return []
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        filing_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(filing)")}
        reporter_select = "f.reporter_name" if "reporter_name" in filing_columns else "NULL AS reporter_name"
        version_sql = ""
        version_params: list[object] = []
        if not include_unsafe:
            version_sql, version_params = version_filter_sql(as_of=as_of)
        if company or filing_ids is not None:
            eligible_where: list[str] = []
            eligible_params: list[object] = []
            if company:
                company_fields = ["f.issuer_name=?", "f.listed_name=?", "f.stock_code=?", "f.issuer_corp_code=?"]
                if "reporter_name" in filing_columns:
                    company_fields.append("f.reporter_name=?")
                eligible_where.append(f"({' OR '.join(company_fields)})")
                eligible_params.extend([company] * len(company_fields))
            if filing_ids is not None:
                unique_filing_ids = list(dict.fromkeys(str(item) for item in filing_ids))
                eligible_where.append(f"f.filing_id IN ({','.join('?' for _ in unique_filing_ids)})")
                eligible_params.extend(unique_filing_ids)
            if version_sql:
                eligible_where.append(version_sql)
                eligible_params.extend(version_params)

            # Keep entity/lineage resolution on the small filing grain.  Folding fragment into
            # this join made SQLite scan ~120k company fragments merely to rediscover their
            # filing ranges, causing a measured 4-16x regression on the 38 GB corpus.
            eligible_filings = connection.execute(
                f"""SELECT f.filing_id,f.issuer_name,{reporter_select},f.report_name_raw,f.filed_at,
                           v.lineage_status,v.is_current,v.effective_from,v.effective_to
                    FROM filing f
                    JOIN filing_version v ON v.filing_id=f.filing_id
                    WHERE {' AND '.join(eligible_where)}""",
                eligible_params,
            ).fetchall()
            filing_metadata = {str(row["filing_id"]): dict(row) for row in eligible_filings}
            filing_ranges: dict[str, tuple[int, int]] = {}
            if filing_metadata:
                placeholders = ",".join("?" for _ in filing_metadata)
                filing_ranges = {
                    str(row["filing_id"]): (int(row["first_rowid"]), int(row["last_rowid"]))
                    for row in connection.execute(
                        f"""SELECT filing_id,min(rowid) AS first_rowid,max(rowid) AS last_rowid
                            FROM fragment WHERE filing_id IN ({placeholders}) GROUP BY filing_id""",
                        list(filing_metadata),
                    )
                }
            candidates: list[dict[str, object]] = []
            for filing_id, (first_rowid, last_rowid) in filing_ranges.items():
                item_where = [
                    "fragment_fts MATCH ?",
                    "ft.rowid BETWEEN ? AND ?",
                    "fr.filing_id=?",
                    "s.parse_status='success'",
                ]
                item_params: list[object] = [query, first_rowid, last_rowid, filing_id]
                item_params.append(limit)
                matches = connection.execute(
                        f"""SELECT fr.evidence_id,fr.filing_id,fr.source_id,fr.fragment_type,fr.section_path_json,
                                   fr.locator_json,fr.text_normalized,
                                   CAST(json_extract(s.coverage_json,'$.image_reference_count') AS INTEGER)
                                       AS image_reference_count,
                                   bm25(fragment_fts) AS score
                            FROM fragment_fts ft
                            JOIN fragment fr ON fr.rowid=ft.rowid
                            JOIN source_document s ON s.source_id=fr.source_id
                            WHERE {' AND '.join(item_where)}
                            ORDER BY score LIMIT ?""",
                        item_params,
                    ).fetchall()
                metadata = filing_metadata[filing_id]
                for match in matches:
                    candidate = dict(match)
                    candidate.update(metadata)
                    candidates.append(candidate)
            rows = sorted(candidates, key=lambda row: row["score"])[:limit]
        else:
            where = ["fragment_fts MATCH ?", "s.parse_status='success'"]
            params: list[object] = [query]
            if version_sql:
                where.append(version_sql)
                params.extend(version_params)
            params.append(limit)
            rows = connection.execute(
                f"""SELECT fr.evidence_id,fr.filing_id,fr.source_id,f.issuer_name,{reporter_select},f.report_name_raw,f.filed_at,
                           fr.fragment_type,fr.section_path_json,fr.locator_json,fr.text_normalized,
                           v.lineage_status,v.is_current,v.effective_from,v.effective_to,
                           CAST(json_extract(s.coverage_json,'$.image_reference_count') AS INTEGER)
                               AS image_reference_count,
                           bm25(fragment_fts) AS score
                    FROM fragment_fts ft
                    JOIN fragment fr ON fr.rowid=ft.rowid
                    JOIN filing f ON f.filing_id=fr.filing_id
                    JOIN filing_version v ON v.filing_id=fr.filing_id
                    JOIN source_document s ON s.source_id=fr.source_id
                    WHERE {' AND '.join(where)}
                    ORDER BY score LIMIT ?""",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            image_count = int(item.pop("image_reference_count") or 0)
            item["image_reference_count"] = image_count
            item["requires_visual_verification"] = image_count > 0
            result.append(item)
        return result
