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
    date_match = re.search(r"(20\d{2})\s*[-./년]\s*(\d{1,2})(?:\s*[-./월]\s*(\d{1,2}))?", text)
    resolved_as_of = as_of
    as_of_source = "api" if as_of is not None else None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    reason_codes: list[str] = []
    # Parse the accounting period independently of the point-in-time filing cutoff.
    # An API-provided as_of remains authoritative for version selection.
    if date_match:
        year, month, day = int(date_match.group(1)), int(date_match.group(2)), date_match.group(3)
        if day:
            instant_date = f"{year:04d}-{month:02d}-{int(day):02d}"
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
    statement_type = "IS" if any(term in text for term in ("매출", "영업이익", "순이익")) else (
        "BS" if any(term in text for term in ("자산", "부채", "자본", "현금및현금성자산")) else None
    )
    question_type = "numeric" if operation != "lookup" or account_terms else "text"
    if any(marker in text.casefold() for marker in ("ignore previous", "system prompt", "이전 지시를 무시", "지시를 무시", "시스템 프롬프트")):
        question_type = "adversarial"
        reason_codes.append("prompt_injection_question")
    elif any(term in text for term in ("주가", "목표주가", "내년", "예상", "전망")):
        question_type = "out_of_scope"
        reason_codes.append("out_of_scope_question")
    elif question_type == "text" and any(term in text for term in ("얼마", "금액", "몇", "수량", "가격", "증가", "감소")):
        question_type = "numeric"
    if question_type == "numeric" and any(term in text for term in ("계약금액", "공급계약", "보유주식", "발행주식", "자기주식", "신주", "권리")):
        question_type = "event_numeric"
        reason_codes.append("event_fact_required")
    event_terms = [
        term for term in (
            "계약금액", "계약상대", "계약상대방", "발행주식", "발행주식수",
            "신주", "자기주식", "보유주식",
        )
        if term in text
    ]
    if question_type in {"adversarial", "out_of_scope"}:
        fact_domain = "none"
    elif account_terms:
        fact_domain = "financial"
    elif event_terms:
        fact_domain = "event"
    else:
        fact_domain = "text"
    target_periods: list[dict[str, str | None]] = []
    if instant_date:
        target_periods.append({"period_type": "instant", "start": None, "end": None, "instant": instant_date})
    elif period_start or period_end:
        target_periods.append({"period_type": "duration", "start": period_start, "end": period_end, "instant": None})
    requires_complete = operation in {"growth_rate", "difference", "ratio", "sum"}
    if company is None:
        reason_codes.append("company_unresolved")
    return QueryPlan(
        question=text, company=company, as_of=resolved_as_of, as_of_source=as_of_source,
        period_start=period_start,
        period_end=period_end, instant_date=instant_date, operation=operation,
        account_terms=account_terms, scope=scope, statement_type=statement_type,
        correction_policy=correction_policy, question_type=question_type,
        reason_codes=reason_codes, fact_domain=fact_domain, predicate_terms=event_terms,
        target_periods=target_periods, requires_complete_evidence_set=requires_complete,
    )
