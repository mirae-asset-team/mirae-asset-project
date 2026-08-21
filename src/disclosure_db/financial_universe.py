"""Deterministic company and annual-report universe for financial facts."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ANSWER_SAFE_LINEAGE = {"root", "resolved"}


def _readonly_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _filing_columns(connection: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in connection.execute("PRAGMA table_info(filing)")}


def _company_aliases(connection: sqlite3.Connection) -> tuple[dict[str, set[str]], set[str]]:
    columns = _filing_columns(connection)
    name_columns = [name for name in ("issuer_name", "listed_name", "reporter_name") if name in columns]
    aliases: dict[str, set[str]] = defaultdict(set)
    all_companies: set[str] = set()
    select_columns = ",".join(["issuer_corp_code", *name_columns])
    for row in connection.execute(f"SELECT {select_columns} FROM filing"):
        corp_code = str(row["issuer_corp_code"])
        all_companies.add(corp_code)
        for name in name_columns:
            value = str(row[name] or "").strip()
            if value:
                aliases[corp_code].add(value)
    return aliases, all_companies


def _current_annual_reports(connection: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    columns = _filing_columns(connection)
    reporter_select = "f.reporter_name" if "reporter_name" in columns else "NULL AS reporter_name"
    rows = connection.execute(
        f"""SELECT f.filing_id,f.issuer_corp_code,f.stock_code,f.issuer_name,f.listed_name,
                   {reporter_select},f.industry,f.sector,f.report_name_raw,f.report_name_normalized,
                   f.filed_at,f.base_year,f.base_month,f.is_correction,
                   v.event_id,v.version_no,v.parent_filing_id,v.lineage_status,v.is_current,
                   root.filing_id AS original_filing_id
              FROM filing f
              JOIN filing_version v ON v.filing_id=f.filing_id
              LEFT JOIN filing_version root
                ON root.event_id=v.event_id AND root.version_no=1
             WHERE f.doc_group='periodic'
               AND f.report_name_normalized LIKE '%사업보고서%'
               AND v.is_current=1"""
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["issuer_corp_code"])].append(row)
    for values in grouped.values():
        values.sort(
            key=lambda row: (
                int(row["base_year"]) if row["base_year"] is not None else -1,
                int(row["base_month"]) if row["base_month"] is not None else -1,
                str(row["filed_at"]),
                int(row["version_no"]),
                str(row["filing_id"]),
            ),
            reverse=True,
        )
    return grouped


def build_financial_universe(database: Path, *, source_database_sha256: str) -> dict[str, Any]:
    """Build a query-only legal-company universe and selected annual filings."""

    source_sha256 = str(source_database_sha256).lower()
    if _SHA256_RE.fullmatch(source_sha256) is None:
        raise ValueError("source_database_sha256 must be a 64-character lowercase SHA-256")
    with closing(_readonly_connection(Path(database))) as connection:
        aliases_by_company, source_companies = _company_aliases(connection)
        reports_by_company = _current_annual_reports(connection)
        companies: list[dict[str, Any]] = []
        rejects: list[dict[str, str]] = []
        selected_rows: list[tuple[str, sqlite3.Row]] = []
        for corp_code in sorted(source_companies):
            reports = reports_by_company.get(corp_code, [])
            if not reports:
                rejects.append(
                    {"issuer_corp_code": corp_code, "reason_code": "answer_safe_annual_report_missing"}
                )
                continue
            selected = reports[0]
            if str(selected["lineage_status"]) not in _ANSWER_SAFE_LINEAGE:
                rejects.append(
                    {"issuer_corp_code": corp_code, "reason_code": "answer_safe_annual_report_missing"}
                )
                continue
            if selected["base_year"] is None:
                rejects.append(
                    {"issuer_corp_code": corp_code, "reason_code": "annual_report_fiscal_year_missing"}
                )
                continue
            selected_rows.append((corp_code, selected))
        for corp_code, selected in selected_rows:
            filing_id = str(selected["filing_id"])
            companies.append(
                {
                    "issuer_corp_code": corp_code,
                    "stock_code": str(selected["stock_code"]),
                    "issuer_name": str(selected["issuer_name"]),
                    "listed_name": str(selected["listed_name"]),
                    "aliases": sorted(aliases_by_company.get(corp_code, set())),
                    "industry": str(selected["industry"]),
                    "sector": str(selected["sector"]),
                    "filing_id": filing_id,
                    "original_filing_id": str(selected["original_filing_id"] or filing_id),
                    "report_name_raw": str(selected["report_name_raw"]),
                    "report_name_normalized": str(selected["report_name_normalized"]),
                    "filed_at": str(selected["filed_at"]),
                    "fiscal_year": int(selected["base_year"]),
                    "is_correction": bool(selected["is_correction"]),
                    "lineage_status": str(selected["lineage_status"]),
                    "consolidated_statement_present": None,
                    "statement_scope_status": "pending_extraction",
                }
            )
    companies.sort(key=lambda item: str(item["issuer_corp_code"]))
    rejects.sort(key=lambda item: (item["issuer_corp_code"], item["reason_code"]))
    searchable_aliases = {
        alias
        for company in companies
        for alias in company.get("aliases", [])
        if str(alias).strip()
    }
    return {
        "schema_version": "financial-universe-v1",
        "source_database_sha256": source_sha256,
        "source_company_count": len(source_companies),
        "company_count": len(companies),
        "searchable_alias_count": len(searchable_aliases),
        "rejected_company_count": len(rejects),
        "companies": companies,
        "rejects": rejects,
    }


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    """Return stable UTF-8 JSON independent of company/reject insertion order."""

    normalized = copy.deepcopy(payload)
    if isinstance(normalized.get("companies"), list):
        normalized["companies"] = sorted(
            normalized["companies"], key=lambda item: str(item.get("issuer_corp_code", ""))
        )
    if isinstance(normalized.get("rejects"), list):
        normalized["rejects"] = sorted(
            normalized["rejects"],
            key=lambda item: (str(item.get("issuer_corp_code", "")), str(item.get("reason_code", ""))),
        )
    return (json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
