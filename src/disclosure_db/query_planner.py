"""Deterministic query planning before any LLM call."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
import json
from pathlib import Path

from .agent_contracts import QueryPlan
from .financial_accounts import load_financial_account_catalog, resolve_financial_account
from .input_hardening import detect_prompt_injection, preflight_public_question


_STATEMENT_CODES = {
    "income_statement": "IS",
    "balance_sheet": "BS",
    "cash_flow_statement": "CF",
    "changes_in_equity": "SCE",
    "per_share_information": "IS",
}


def _statement_type_for_resolution(statement_type: str | None, required_accounts: list[str]) -> str | None:
    if statement_type != "derived_metric":
        return _STATEMENT_CODES.get(str(statement_type))
    if len(required_accounts) != 1:
        return None
    source = load_financial_account_catalog().by_id.get(required_accounts[0])
    return _STATEMENT_CODES.get(source.statement_type) if source is not None else None


def _threshold_krw(text: str) -> Decimal | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(조|억|만)?\s*(?:원)?\s*(?:넘|초과|이상)", text)
    if not match:
        return None
    multipliers = {None: Decimal(1), "만": Decimal(10_000), "억": Decimal(100_000_000), "조": Decimal(1_000_000_000_000)}
    return Decimal(match.group(1)) * multipliers[match.group(2)]


def _resolve_company(question: str, candidates: Iterable[str]) -> str | None:
    matches = [str(name) for name in candidates if str(name) and str(name) in question]
    return max(matches, key=len) if matches else None


def _normalize_configured_company_aliases(question: str, candidates: Iterable[str]) -> str:
    """Apply only aliases committed in the project catalog for eligible issuers."""

    candidate_set = {str(item) for item in candidates if str(item)}
    project_root = Path(__file__).resolve().parents[2]
    if not candidate_set:
        return question
    rows: list[dict[str, object]] = []
    universe_path = project_root / "data" / "derived" / "financial_company_universe.json"
    config_path = project_root / "config" / "company_aliases.json"
    for path, kind in ((universe_path, "universe"), (config_path, "config")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        source_rows = payload.get("companies", []) if isinstance(payload, dict) else []
        for raw in source_rows:
            if not isinstance(raw, dict):
                continue
            canonical = raw.get("issuer_name") if kind == "universe" else raw.get("canonical")
            aliases = list(raw.get("aliases", [])) if isinstance(raw.get("aliases"), list) else []
            if kind == "universe":
                aliases.extend([raw.get("listed_name"), raw.get("stock_code")])
            rows.append({"canonical": canonical, "aliases": aliases})
    text = question
    for row in rows:
        if not isinstance(row, dict) or str(row.get("canonical")) not in candidate_set:
            continue
        canonical = str(row["canonical"])
        aliases = sorted(
            (str(item) for item in row.get("aliases", []) if item),
            key=len,
            reverse=True,
        )
        for alias in aliases:
            boundary = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])" if any(ch.isascii() and ch.isalnum() for ch in alias) else r"{}"
            text = re.sub(boundary.format(re.escape(alias)), canonical, text, flags=re.IGNORECASE)
    return text


def _fiscal_years(text: str) -> list[int]:
    """Return every explicit fiscal year, normalized and chronologically ordered."""
    years = {int(match.group(1)) for match in re.finditer(r"(?<!\d)(20\d{2})(?!\d)", text)}
    years.update(
        2000 + int(match.group(1))
        for match in re.finditer(r"(?<!\d)(\d{2})\s*년", text)
    )
    return sorted(years)


def plan_query(
    question: str,
    *,
    company_candidates: Iterable[str] = (),
    company_hint: str | None = None,
    as_of: str | None = None,
) -> QueryPlan:
    candidates = tuple(company_candidates)
    text = preflight_public_question(question)
    text = _normalize_configured_company_aliases(text, candidates)
    company = company_hint or _resolve_company(text, candidates)
    fiscal_years = _fiscal_years(text)
    date_matches = list(re.finditer(
        r"(20\d{2})\s*[-./년]\s*(0?[1-9]|1[0-2])"
        r"(?:\s*[-./월]\s*(0?[1-9]|[12]\d|3[01]))?(?!\d)",
        text,
    ))
    date_match = date_matches[0] if date_matches else None
    resolved_as_of = as_of
    as_of_source = "api" if as_of is not None else None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    parsed_calendar_date: str | None = None
    filing_date: str | None = None
    reason_codes: list[str] = []
    # Parse the accounting period independently of the point-in-time filing cutoff.
    # An API-provided as_of remains authoritative for version selection.
    if date_match:
        year, month, day = int(date_match.group(1)), int(date_match.group(2)), date_match.group(3)
        if day:
            parsed_calendar_date = f"{year:04d}-{month:02d}-{int(day):02d}"
            instant_date = parsed_calendar_date
        else:
            import calendar
            period_end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
            period_start = f"{year:04d}-{month:02d}-01"
        reason_codes.append("explicit_date")
    elif fiscal_years:
        period_start = f"{fiscal_years[0]}-01-01"
        period_end = f"{fiscal_years[0]}-12-31"
        reason_codes.append("explicit_year" if len(fiscal_years) == 1 else "explicit_multiple_fiscal_years")
    account_resolution = resolve_financial_account(text)
    account_id = account_resolution.canonical_id
    account_terms = [
        str(account_resolution.matched_text or account_resolution.label_ko)
    ] if account_resolution.status == "resolved" and (
        account_resolution.matched_text or account_resolution.label_ko
    ) else []
    required_account_ids = list(account_resolution.required_accounts)
    structured_account = account_resolution.support_level == "structured"
    threshold_value = _threshold_krw(text)
    asks_company_universe = any(term in text for term in ("기업", "회사"))
    asks_company_count = any(
        term in text for term in ("몇 개", "몇개", "몇 곳", "몇곳", "기업 수", "기업수", "회사 수", "회사수")
    )
    asks_company_list = any(term in text for term in ("목록", "어떤 기업", "어느 기업", "어떤 회사", "어느 회사"))
    asks_rank = any(term in text for term in ("순위", "랭킹", "상위", "하위"))
    if structured_account and company is None and asks_company_universe and asks_rank:
        operation = "rank"
    elif structured_account and threshold_value is not None and asks_company_universe and asks_company_count:
        operation = "count_above"
    elif structured_account and threshold_value is not None and asks_company_universe and asks_company_list:
        operation = "list_above"
    elif account_resolution.support_level == "derived" and account_resolution.formula:
        operation = str(account_resolution.formula.get("operation") or "derived")
    elif "증가율" in text or "성장률" in text or "증감률" in text:
        operation = "growth_rate"
    elif "차이" in text or "증감액" in text:
        operation = "difference"
    elif "비율" in text or "비중" in text:
        operation = "ratio"
    elif "합계" in text or "총액" in text:
        operation = "sum"
    else:
        operation = "lookup"
    scope = "consolidated" if "연결" in text else "separate" if "별도" in text else None
    correction_policy = "original" if "최초" in text or "원문" in text else "current"
    if "최초" in text and "정정" in text and "각각" in text:
        correction_policy = "both"
    elif "정정" in text:
        correction_policy = "corrected"
    statement_type = _statement_type_for_resolution(
        account_resolution.statement_type,
        required_account_ids,
    )
    if parsed_calendar_date and statement_type in {"IS", "CIS", "CF"}:
        year = parsed_calendar_date[:4]
        period_start = f"{year}-01-01"
        period_end = parsed_calendar_date
        instant_date = None
    elif parsed_calendar_date and statement_type == "BS":
        instant_date = parsed_calendar_date
    question_type = "numeric" if operation != "lookup" or account_resolution.status != "unknown" else "text"
    if detect_prompt_injection(text):
        question_type = "adversarial"
        reason_codes.append("prompt_injection_question")
    elif any(term in text for term in ("주가", "목표주가", "내년", "예상", "전망")):
        question_type = "out_of_scope"
        reason_codes.append("out_of_scope_question")
    elif question_type == "text" and any(term in text for term in ("얼마", "금액", "몇", "수량", "가격", "증가", "감소")):
        question_type = "numeric"
    if question_type == "numeric" and any(term in text for term in ("계약금액", "공급계약", "보유주식", "발행주식", "보통주식", "자기주식", "신주", "권리")):
        question_type = "event_numeric"
        reason_codes.append("event_fact_required")
    event_terms = [
        term for term in (
            "계약금액", "계약상대", "계약상대방", "발행주식", "발행주식수",
            "보통주식", "신주", "자기주식", "보유주식", "내부자매수",
        )
        if term in text
    ]
    if "보통주식" in text and "issued_shares" not in event_terms:
        event_terms.append("issued_shares")
    if "처분예정주식" in text and "treasury_disposal_shares" not in event_terms:
        event_terms.append("treasury_disposal_shares")
    unknown_account_intent = (
        account_resolution.status == "unknown"
        and any(marker in text for marker in ("재무계정", "재무 지표", "재무지표", "회계계정", "회계 계정"))
    )
    if unknown_account_intent:
        reason_codes.append("financial_account_unknown")
    if account_resolution.support_level == "retrieval_only":
        reason_codes.append("financial_account_retrieval_only")
    elif account_resolution.support_level == "derived":
        reason_codes.append("financial_account_derived")
    elif account_resolution.status == "ambiguous":
        reason_codes.append("financial_account_clarification_required")
    elif account_resolution.status == "unsupported":
        reason_codes.append("financial_account_unsupported")
    if question_type in {"adversarial", "out_of_scope"}:
        fact_domain = "none"
    elif account_resolution.status in {"ambiguous", "unsupported"} or unknown_account_intent:
        fact_domain = "none"
    elif account_resolution.support_level == "structured":
        fact_domain = "financial"
    elif account_resolution.support_level == "retrieval_only":
        fact_domain = "text"
    elif account_resolution.support_level == "derived":
        fact_domain = "financial" if operation == "growth_rate" and len(required_account_ids) == 1 else "financial_derived"
    elif event_terms:
        fact_domain = "event"
    else:
        fact_domain = "text"
    if parsed_calendar_date and statement_type is None:
        filing_date = parsed_calendar_date
        instant_date = None
    target_periods: list[dict[str, str | None]] = []
    if len(date_matches) > 1:
        for match in date_matches:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3)) if match.group(3) else None
            if day is None:
                import calendar
                end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
                target_periods.append({
                    "period_type": "duration",
                    "start": f"{year:04d}-{month:02d}-01",
                    "end": end,
                    "instant": None,
                })
            elif statement_type == "BS":
                target_periods.append({
                    "period_type": "instant",
                    "start": None,
                    "end": None,
                    "instant": f"{year:04d}-{month:02d}-{day:02d}",
                })
            else:
                target_periods.append({
                    "period_type": "duration",
                    "start": f"{year:04d}-01-01",
                    "end": f"{year:04d}-{month:02d}-{day:02d}",
                    "instant": None,
                })
    elif len(fiscal_years) > 1 and not date_match:
        target_periods.extend(
            {
                "period_type": "duration",
                "start": f"{year}-01-01",
                "end": f"{year}-12-31",
                "instant": None,
            }
            for year in fiscal_years
        )
    elif instant_date:
        target_periods.append({"period_type": "instant", "start": None, "end": None, "instant": instant_date})
    elif period_start or period_end:
        target_periods.append({"period_type": "duration", "start": period_start, "end": period_end, "instant": None})
    latest_period_count = 3 if re.search(r"(?<!\d)(?:최근\s*)?3\s*개?년", text) or "3개년" in text else 1
    latest_period_count = max(latest_period_count, len(target_periods))
    if operation == "growth_rate":
        latest_period_count = max(latest_period_count, 2)
    requires_complete = len(target_periods) > 1 or operation in {"growth_rate", "difference", "ratio", "percentage_ratio", "sum", "count_above", "list_above", "rank"}
    if structured_account and company is None and asks_company_universe and operation in {"count_above", "list_above", "rank"}:
        reason_codes.append("corpus_wide_financial_coverage_required")
    if company is None and operation not in {"count_above", "list_above", "rank"}:
        reason_codes.append("company_unresolved")
    top_match = re.search(r"(?:상위|하위)?\s*(\d+)\s*개", text)
    top_n = max(1, min(int(top_match.group(1)), 70)) if top_match else 10
    return QueryPlan(
        question=text, company=company, as_of=resolved_as_of, as_of_source=as_of_source,
        period_start=period_start,
        period_end=period_end, instant_date=instant_date, operation=operation,
        account_terms=account_terms, scope=scope, statement_type=statement_type,
        correction_policy=correction_policy, question_type=question_type,
        reason_codes=reason_codes, fact_domain=fact_domain, predicate_terms=event_terms,
        target_periods=target_periods, requires_complete_evidence_set=requires_complete,
        filing_date=filing_date,
        account_id=account_id,
        account_status=account_resolution.status,
        account_match_type=account_resolution.match_type,
        account_support_level=account_resolution.support_level,
        account_retrieval_route=account_resolution.retrieval_route,
        account_candidates=list(account_resolution.candidates),
        account_warning=account_resolution.warning,
        required_account_ids=required_account_ids,
        account_formula=dict(account_resolution.formula) if account_resolution.formula is not None else None,
        latest_period_count=latest_period_count,
        threshold_value=threshold_value,
        threshold_inclusive="이상" in text,
        top_n=top_n,
    )
