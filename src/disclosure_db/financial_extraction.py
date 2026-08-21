"""Rule-based candidate extraction for core annual financial facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


_NOTE_RE = re.compile(r"[\(\[]\s*주[^\)\]]*[\)\]]")
_ROMAN_PREFIX_RE = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[IVX]+)[\.\s]*", re.IGNORECASE)
_NUMBER_PREFIX_RE = re.compile(r"^\(?\d+\)?[\.\s]+")
_TERM_RE = re.compile(r"제\s*(\d+)\s*기")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_ACCOUNT_ALIASES = {
    "revenue": {"매출", "매출액", "수익(매출액)", "영업수익", "보험영업수익"},
    "operating_income": {"영업이익", "영업이익(손실)", "영업손익", "영업손실"},
    "net_income": {
        "당기순이익",
        "당기순이익(손실)",
        "당기순손익",
        "당기순손실",
        "연결당기순이익",
        "연결당기순이익(손실)",
    },
    "total_assets": {"자산총계", "자산합계"},
    "total_liabilities": {"부채총계", "부채합계"},
    "total_equity": {"자본총계"},
}
_ACCOUNT_ORDER = {name: index for index, name in enumerate(_ACCOUNT_ALIASES)}
_UNITS = (
    ("백만원", 1_000_000),
    ("천원", 1_000),
    ("억원", 100_000_000),
    ("원", 1),
)


def _normalized_label(raw: str) -> str:
    value = _NOTE_RE.sub("", str(raw)).strip()
    value = _ROMAN_PREFIX_RE.sub("", value)
    value = _NUMBER_PREFIX_RE.sub("", value)
    return re.sub(r"\s+", "", value)


def canonical_account_id(raw: str) -> str | None:
    """Map an exact primary-statement label to the six canonical metrics."""

    label = _normalized_label(raw)
    for account_id, aliases in _ACCOUNT_ALIASES.items():
        if label in aliases:
            return account_id
    return None


def parse_unit_text(raw: str | None) -> tuple[str, int] | None:
    """Return the disclosed Korean won unit and integer scale."""

    value = re.sub(r"\s+", "", str(raw or ""))
    for unit, scale in _UNITS:
        if unit in value:
            return unit, scale
    return None


def parse_numeric_value(raw: str | None) -> str | None:
    """Parse a table amount without interpreting blank/dash cells as zero."""

    value = str(raw or "").strip()
    if not value or value in {"-", "—", "–"}:
        return None
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative = True
        value = value[1:-1].strip()
    if value.startswith("△") or value.startswith("▲"):
        negative = True
        value = value[1:].strip()
    value = value.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    if negative and parsed > 0:
        value = f"-{value.lstrip('+')}"
    return value.lstrip("+")


def classify_statement_table(table: Mapping[str, object]) -> dict[str, object] | None:
    """Classify only successful, unit-bearing main statement tables."""

    if str(table.get("parse_status") or "") != "success":
        return None
    try:
        section_path = json.loads(str(table.get("section_path_json") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(section_path, list) or not section_path:
        return None
    section = str(section_path[-1]).strip()
    if section == "2. 연결재무제표":
        scope = "consolidated"
    elif section == "4. 재무제표":
        scope = "separate"
    else:
        return None
    unit = parse_unit_text(table.get("unit_text") if isinstance(table.get("unit_text"), str) else None)
    if unit is None:
        return None
    caption = re.sub(r"\s+", "", str(table.get("caption") or ""))
    if "재무상태표" in caption:
        statement_type, priority = "BS", 0
    elif "포괄손익계산서" in caption:
        statement_type, priority = "CIS", 1
    elif "손익계산서" in caption:
        statement_type, priority = "IS", 0
    else:
        return None
    return {
        "scope": scope,
        "statement_type": statement_type,
        "priority": priority,
        "unit_raw": unit[0],
        "scale": unit[1],
    }


def _header_years(cells: list[Mapping[str, object]], fiscal_year: int) -> dict[int, int]:
    headers: dict[int, str] = {}
    for cell in cells:
        column = int(cell.get("column_index") or 0)
        if column <= 0:
            continue
        if str(cell.get("cell_kind") or "") == "header" and str(cell.get("text_raw") or "").strip():
            headers.setdefault(column, str(cell["text_raw"]))
        try:
            path = json.loads(str(cell.get("column_header_path_json") or "[]"))
        except json.JSONDecodeError:
            path = []
        if isinstance(path, list) and path:
            headers[column] = str(path[-1])
    term_by_column = {
        column: int(match.group(1))
        for column, header in headers.items()
        if (match := _TERM_RE.search(header)) is not None
    }
    if term_by_column:
        latest_term = max(term_by_column.values())
        return {
            column: fiscal_year - (latest_term - term)
            for column, term in term_by_column.items()
        }
    return {
        column: int(match.group(1))
        for column, header in headers.items()
        if (match := _YEAR_RE.search(header)) is not None
    }


def _reject(table: Mapping[str, object], reason_code: str, **extra: object) -> dict[str, object]:
    return {
        "filing_id": str(table.get("filing_id") or ""),
        "table_id": str(table.get("table_id") or ""),
        "reason_code": reason_code,
        **extra,
    }


def extract_table_candidates(
    table: Mapping[str, object],
    cells: Iterable[Mapping[str, object]],
    *,
    fiscal_year: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Extract strict candidate facts from one classified annual statement table."""

    classification = classify_statement_table(table)
    if classification is None:
        return [], [_reject(table, "ineligible_statement_table")]
    cell_rows = [dict(cell) for cell in cells]
    years = _header_years(cell_rows, int(fiscal_year))
    if not years:
        return [], [_reject(table, "period_columns_unresolved")]
    rows: dict[int, list[dict[str, object]]] = defaultdict(list)
    for cell in cell_rows:
        rows[int(cell.get("row_index") or 0)].append(cell)
    facts: list[dict[str, object]] = []
    rejects: list[dict[str, object]] = []
    for row_index in sorted(rows):
        row = sorted(rows[row_index], key=lambda item: int(item.get("column_index") or 0))
        labels = [
            (cell, account_id)
            for cell in row
            if (account_id := canonical_account_id(str(cell.get("text_raw") or ""))) is not None
        ]
        if not labels:
            continue
        if len(labels) != 1:
            rejects.append(_reject(table, "ambiguous_metric_label", row_index=row_index))
            continue
        label_cell, account_id = labels[0]
        value_count = 0
        for value_cell in row:
            column = int(value_cell.get("column_index") or 0)
            if column not in years or value_cell is label_cell:
                continue
            year = years[column]
            if year not in {fiscal_year, fiscal_year - 1, fiscal_year - 2}:
                continue
            value = parse_numeric_value(str(value_cell.get("text_raw") or ""))
            if value is None:
                rejects.append(
                    _reject(
                        table,
                        "metric_value_unparseable",
                        row_index=row_index,
                        column_index=column,
                        account_id=account_id,
                        fiscal_year=year,
                        evidence_id=str(value_cell.get("evidence_id") or ""),
                    )
                )
                continue
            value_count += 1
            period_type = "instant" if classification["statement_type"] == "BS" else "duration"
            identity = "|".join(
                (
                    str(table.get("filing_id") or ""),
                    str(table.get("table_id") or ""),
                    str(account_id),
                    str(year),
                    str(value_cell.get("evidence_id") or ""),
                )
            )
            facts.append(
                {
                    "candidate_id": f"ffc_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:28]}",
                    "filing_id": str(table.get("filing_id") or ""),
                    "table_id": str(table.get("table_id") or ""),
                    "row_index": row_index,
                    "column_index": column,
                    "account_id": account_id,
                    "account_name_raw": str(label_cell.get("text_raw") or ""),
                    "statement_type": classification["statement_type"],
                    "statement_priority": classification["priority"],
                    "scope": classification["scope"],
                    "fiscal_year": year,
                    "period_type": period_type,
                    "period_start": f"{year}-01-01" if period_type == "duration" else None,
                    "period_end": f"{year}-12-31" if period_type == "duration" else None,
                    "instant_date": f"{year}-12-31" if period_type == "instant" else None,
                    "value_numeric": value,
                    "currency": "KRW",
                    "scale": classification["scale"],
                    "unit_raw": classification["unit_raw"],
                    "account_evidence_id": str(label_cell.get("evidence_id") or ""),
                    "evidence_ids": [str(value_cell.get("evidence_id") or "")],
                    "extraction_method": "annual_statement_rule_v1",
                    "validation_status": "candidate",
                }
            )
        if value_count == 0:
            rejects.append(
                _reject(table, "metric_row_without_values", row_index=row_index, account_id=account_id)
            )
    facts.sort(key=lambda item: (_ACCOUNT_ORDER[str(item["account_id"])], -int(item["fiscal_year"])))
    return facts, rejects


def resolve_filing_candidates(
    candidates: Iterable[Mapping[str, object]],
    *,
    available_scopes: Iterable[str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str | None]:
    """Apply consolidated-first and statement-priority rules at filing grain."""

    rows = [dict(candidate) for candidate in candidates]
    scopes = set(available_scopes or (str(row.get("scope") or "") for row in rows))
    if "consolidated" in scopes:
        scope: str | None = "consolidated"
    elif "separate" in scopes:
        scope = "separate"
    else:
        return [], [], None
    scoped = [row for row in rows if row.get("scope") == scope]
    grouped: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in scoped:
        grouped[(str(row.get("account_id") or ""), int(row.get("fiscal_year") or 0))].append(row)
    admitted: list[dict[str, object]] = []
    rejects: list[dict[str, object]] = []
    for grain in sorted(grouped, key=lambda item: (_ACCOUNT_ORDER.get(item[0], 999), -item[1])):
        values = grouped[grain]
        priority = min(int(value.get("statement_priority") or 0) for value in values)
        preferred = [value for value in values if int(value.get("statement_priority") or 0) == priority]
        if len(preferred) == 1:
            admitted.append(preferred[0])
            continue
        distinct_values = {str(value.get("value_numeric") or "") for value in preferred}
        reason = "conflicting_metric_grain" if len(distinct_values) > 1 else "duplicate_metric_grain"
        for value in preferred:
            rejects.append({**value, "reason_code": reason})
    return admitted, rejects, scope


def _readonly_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _select_in(
    connection: sqlite3.Connection,
    sql_prefix: str,
    values: list[str],
) -> list[sqlite3.Row]:
    if not values:
        return []
    rows: list[sqlite3.Row] = []
    for offset in range(0, len(values), 500):
        batch = values[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        rows.extend(connection.execute(f"{sql_prefix} ({placeholders})", batch).fetchall())
    return rows


def extract_financial_candidate_corpus(
    database: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Extract deterministic candidates for every admitted manifest filing."""

    companies_value = manifest.get("companies")
    if not isinstance(companies_value, list):
        raise ValueError("manifest companies must be a list")
    companies = [dict(item) for item in companies_value if isinstance(item, Mapping)]
    company_by_filing: dict[str, dict[str, object]] = {}
    for company in companies:
        filing_id = str(company.get("filing_id") or "")
        if not filing_id or filing_id in company_by_filing:
            raise ValueError("manifest filing_id must be non-empty and unique")
        if not isinstance(company.get("fiscal_year"), int):
            raise ValueError("manifest fiscal_year must be an integer")
        company_by_filing[filing_id] = company
    filing_ids = sorted(company_by_filing)
    with closing(_readonly_connection(Path(database))) as connection:
        table_rows = _select_in(
            connection,
            """SELECT table_id,filing_id,source_id,sequence_no,section_path_json,caption,
                      unit_text,row_count,column_count,parse_status,locator_json
                 FROM table_record WHERE filing_id IN""",
            filing_ids,
        )
        eligible_tables = [dict(row) for row in table_rows if classify_statement_table(dict(row))]
        eligible_tables.sort(key=lambda row: (str(row["filing_id"]), int(row["sequence_no"]), str(row["table_id"])))
        table_ids = [str(row["table_id"]) for row in eligible_tables]
        cell_rows = _select_in(
            connection,
            """SELECT evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,
                      colspan,cell_kind,row_header_path_json,column_header_path_json,locator_json,
                      text_raw,text_normalized,parser_version
                 FROM table_cell WHERE table_id IN""",
            table_ids,
        )
    cells_by_table: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in cell_rows:
        cells_by_table[str(row["table_id"])].append(dict(row))
    tables_by_filing: dict[str, list[dict[str, object]]] = defaultdict(list)
    for table in eligible_tables:
        tables_by_filing[str(table["filing_id"])].append(table)

    all_candidates: list[dict[str, object]] = []
    all_rejects: list[dict[str, object]] = []
    scope_counts: dict[str, int] = defaultdict(int)
    for filing_id in filing_ids:
        company = company_by_filing[filing_id]
        table_candidates: list[dict[str, object]] = []
        table_rejects: list[dict[str, object]] = []
        available_scopes: set[str] = set()
        for table in tables_by_filing.get(filing_id, []):
            classification = classify_statement_table(table)
            if classification is None:
                continue
            available_scopes.add(str(classification["scope"]))
            candidates, rejects = extract_table_candidates(
                table,
                cells_by_table.get(str(table["table_id"]), []),
                fiscal_year=int(company["fiscal_year"]),
            )
            table_candidates.extend(candidates)
            table_rejects.extend(rejects)
        admitted, resolution_rejects, selected_scope = resolve_filing_candidates(
            table_candidates,
            available_scopes=available_scopes,
        )
        if selected_scope is None:
            table_rejects.append(
                {
                    "filing_id": filing_id,
                    "issuer_corp_code": str(company.get("issuer_corp_code") or ""),
                    "reason_code": "main_statement_tables_missing",
                }
            )
        else:
            scope_counts[selected_scope] += 1
        identity = {
            "issuer_corp_code": str(company.get("issuer_corp_code") or ""),
            "stock_code": str(company.get("stock_code") or ""),
            "issuer_name": str(company.get("issuer_name") or ""),
            "listed_name": str(company.get("listed_name") or ""),
        }
        admitted = [{**fact, **identity} for fact in admitted]
        table_rejects = [{**reject, **identity} for reject in table_rejects]
        resolution_rejects = [{**reject, **identity} for reject in resolution_rejects]
        admitted_grains = {
            (str(fact["account_id"]), int(fact["fiscal_year"])) for fact in admitted
        }
        for account_id in _ACCOUNT_ALIASES:
            for year in range(int(company["fiscal_year"]), int(company["fiscal_year"]) - 3, -1):
                if (account_id, year) not in admitted_grains:
                    table_rejects.append(
                        {
                            **identity,
                            "filing_id": filing_id,
                            "account_id": account_id,
                            "fiscal_year": year,
                            "scope": selected_scope,
                            "reason_code": "metric_period_missing",
                        }
                    )
        all_candidates.extend(admitted)
        all_rejects.extend(table_rejects)
        all_rejects.extend(resolution_rejects)

    all_candidates.sort(
        key=lambda item: (
            str(item["issuer_corp_code"]),
            _ACCOUNT_ORDER[str(item["account_id"])],
            -int(item["fiscal_year"]),
        )
    )
    all_rejects.sort(
        key=lambda item: (
            str(item.get("issuer_corp_code") or ""),
            str(item.get("reason_code") or ""),
            _ACCOUNT_ORDER.get(str(item.get("account_id") or ""), 999),
            -int(item.get("fiscal_year") or 0),
            str(item.get("table_id") or ""),
            int(item.get("row_index") or 0),
        )
    )
    metric_counts = {
        account_id: sum(1 for item in all_candidates if item["account_id"] == account_id)
        for account_id in _ACCOUNT_ALIASES
    }
    reject_counts = {
        reason: sum(1 for item in all_rejects if item.get("reason_code") == reason)
        for reason in sorted({str(item.get("reason_code") or "") for item in all_rejects})
    }
    return {
        "schema_version": "financial-candidates-v1",
        "source_database_sha256": str(manifest.get("source_database_sha256") or ""),
        "candidates": all_candidates,
        "rejects": all_rejects,
        "summary": {
            "company_count": len(companies),
            "expected_grain_count": len(companies) * len(_ACCOUNT_ALIASES) * 3,
            "candidate_count": len(all_candidates),
            "missing_grain_count": reject_counts.get("metric_period_missing", 0),
            "metric_counts": metric_counts,
            "scope_counts": dict(sorted(scope_counts.items())),
            "reject_counts": reject_counts,
        },
    }
