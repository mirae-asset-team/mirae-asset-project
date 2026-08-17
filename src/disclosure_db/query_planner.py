"""Deterministic query planning before any LLM call."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .agent_contracts import QueryPlan


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
    date_match = re.search(r"(20\d{2})[-./년\s](\d{1,2})(?:[-./월\s](\d{1,2}))?", text)
    resolved_as_of = as_of
    reason_codes: list[str] = []
    if resolved_as_of is None and date_match:
        year, month, day = int(date_match.group(1)), int(date_match.group(2)), date_match.group(3)
        if day:
            resolved_as_of = f"{year:04d}-{month:02d}-{int(day):02d}"
        else:
            # A year/month question is interpreted at month end for PIT filtering.
            import calendar
            resolved_as_of = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        reason_codes.append("explicit_date")
    elif resolved_as_of is None:
        year_match = re.search(r"(20\d{2})년", text)
        if year_match:
            resolved_as_of = f"{year_match.group(1)}-12-31"
            reason_codes.append("explicit_year")
    if "증가율" in text or "성장률" in text or "증감률" in text:
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
    if "정정" in text:
        correction_policy = "corrected"
    account_terms = []
    for term in ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계", "현금및현금성자산"):
        if term in text:
            account_terms.append(term)
    statement_type = "IS" if any(term in text for term in ("매출", "영업이익", "순이익")) else None
    question_type = "numeric" if operation != "lookup" or account_terms else "text"
    if company is None:
        reason_codes.append("company_unresolved")
    return QueryPlan(
        question=text, company=company, as_of=resolved_as_of, operation=operation,
        account_terms=account_terms, scope=scope, statement_type=statement_type,
        correction_policy=correction_policy, question_type=question_type,
        reason_codes=reason_codes,
    )
