"""Deterministic query planning before any LLM call."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

from .agent_contracts import QueryPlan


_ACCOUNT_ALIASES = {
    "revenue": ("매출액", "매출", "영업수익"),
    "operating_income": ("영업이익", "영업손익"),
    "net_income": ("당기순이익", "당기순손익", "순이익"),
    "total_assets": ("자산총계", "자산 합계"),
    "total_liabilities": ("부채총계", "부채 합계"),
    "total_equity": ("자본총계", "자본 합계"),
}


def _financial_account(text: str) -> tuple[str | None, list[str]]:
    for account_id, aliases in _ACCOUNT_ALIASES.items():
        matched = [alias for alias in aliases if alias in text]
        if matched:
            return account_id, matched
    return None, []


def _threshold_krw(text: str) -> Decimal | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(조|억|만)?\s*(?:원)?\s*(?:넘|초과|이상)", text)
    if not match:
        return None
    multipliers = {None: Decimal(1), "만": Decimal(10_000), "억": Decimal(100_000_000), "조": Decimal(1_000_000_000_000)}
    return Decimal(match.group(1)) * multipliers[match.group(2)]


def _resolve_company(question: str, candidates: Iterable[str]) -> str | None:
    matches = [str(name) for name in candidates if str(name) and str(name) in question]
    return max(matches, key=len) if matches else None


def plan_query(
    question: str,
    *,
    company_candidates: Iterable[str] = (),
    company_hint: str | None = None,
    as_of: str | None = None,
) -> QueryPlan:
    text = question.strip()
    company = company_hint or _resolve_company(text, company_candidates)
    date_match = re.search(
        r"(20\d{2})\s*[-./년]\s*(0?[1-9]|1[0-2])"
        r"(?:\s*[-./월]\s*(0?[1-9]|[12]\d|3[01]))?(?!\d)",
        text,
    )
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
    elif not date_match:
        year_match = re.search(r"(20\d{2})년", text)
        if year_match:
            period_start = f"{year_match.group(1)}-01-01"
            period_end = f"{year_match.group(1)}-12-31"
            reason_codes.append("explicit_year")
    account_id, account_terms = _financial_account(text)
    threshold_value = _threshold_krw(text)
    asks_company_universe = any(term in text for term in ("기업", "회사"))
    asks_company_count = any(
        term in text for term in ("몇 개", "몇개", "몇 곳", "몇곳", "기업 수", "기업수", "회사 수", "회사수")
    )
    asks_company_list = any(term in text for term in ("목록", "어떤 기업", "어느 기업", "어떤 회사", "어느 회사"))
    asks_rank = any(term in text for term in ("순위", "랭킹", "상위", "하위"))
    if account_id and company is None and asks_company_universe and asks_rank:
        operation = "rank"
    elif account_id and threshold_value is not None and asks_company_universe and asks_company_count:
        operation = "count_above"
    elif account_id and threshold_value is not None and asks_company_universe and asks_company_list:
        operation = "list_above"
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
    if "현금및현금성자산" in text:
        account_terms.append("현금및현금성자산")
    statement_type = "IS" if any(term in text for term in ("매출", "영업이익", "순이익")) else (
        "BS" if any(term in text for term in ("자산", "부채", "자본", "현금및현금성자산")) else None
    )
    if parsed_calendar_date and statement_type in {"IS", "CIS", "CF"}:
        year = parsed_calendar_date[:4]
        period_start = f"{year}-01-01"
        period_end = parsed_calendar_date
        instant_date = None
    elif parsed_calendar_date and statement_type == "BS":
        instant_date = parsed_calendar_date
    question_type = "numeric" if operation != "lookup" or account_terms else "text"
    if any(marker in text.casefold() for marker in ("ignore previous", "ignore all previous", "system prompt", "developer message", "이전 지시를 무시", "지시를 무시", "시스템 프롬프트")):
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
    if question_type in {"adversarial", "out_of_scope"}:
        fact_domain = "none"
    elif account_terms:
        fact_domain = "financial"
    elif event_terms:
        fact_domain = "event"
    else:
        fact_domain = "text"
    if parsed_calendar_date and statement_type is None:
        filing_date = parsed_calendar_date
        instant_date = None
    target_periods: list[dict[str, str | None]] = []
    if instant_date:
        target_periods.append({"period_type": "instant", "start": None, "end": None, "instant": instant_date})
    elif period_start or period_end:
        target_periods.append({"period_type": "duration", "start": period_start, "end": period_end, "instant": None})
    latest_period_count = 3 if re.search(r"(?:최근\s*)?3\s*개?년", text) or "3개년" in text else 1
    requires_complete = operation in {"growth_rate", "difference", "ratio", "sum", "count_above", "list_above", "rank"}
    if account_terms and company is None and asks_company_universe and operation in {"count_above", "list_above", "rank"}:
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
        latest_period_count=latest_period_count,
        threshold_value=threshold_value,
        threshold_inclusive="이상" in text,
        top_n=top_n,
    )
