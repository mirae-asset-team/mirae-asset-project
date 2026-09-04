"""Release-gate evaluation for admitted corpus-wide financial facts."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
import math
from threading import Event
from time import perf_counter
from typing import Iterable, Mapping, Sequence


ACCOUNT_LABELS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "total_assets": "자산총계",
    "total_liabilities": "부채총계",
    "total_equity": "자본총계",
}

FACT_FIELDS = (
    "account_id",
    "account_name_raw",
    "filing_id",
    "fiscal_year",
    "issuer_corp_code",
    "scale",
    "scope",
    "statement_type",
    "unit_raw",
    "value_numeric",
)


def _fact_key(fact: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(str(fact.get(field, "")) for field in FACT_FIELDS)


def _decimal_values(values: Iterable[object]) -> list[Decimal] | None:
    try:
        numbers = [Decimal(str(value)) for value in values]
    except (InvalidOperation, ValueError):
        return None
    return numbers if all(value.is_finite() for value in numbers) else None


def build_release_cases(
    *,
    search_targets: Sequence[str],
    manifest: Mapping[str, object],
    facts: Sequence[Mapping[str, object]],
    account_labels: Mapping[str, str] = ACCOUNT_LABELS,
) -> list[dict[str, object]]:
    """Build exhaustive latest-target and admitted three-year cases."""
    companies = [item for item in manifest.get("companies", []) if isinstance(item, dict)]
    alias_to_corp: dict[str, str] = {}
    corp_to_name: dict[str, str] = {}
    corrected_corps: set[str] = set()
    for company in companies:
        corp = str(company.get("issuer_corp_code", ""))
        corp_to_name[corp] = str(company.get("listed_name", ""))
        if bool(company.get("is_correction")):
            corrected_corps.add(corp)
        for alias in company.get("aliases", []):
            alias_to_corp[str(alias)] = corp

    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for fact in facts:
        corp = str(fact.get("issuer_corp_code", ""))
        account_id = str(fact.get("account_id", ""))
        if corp and account_id:
            grouped[(corp, account_id)].append(dict(fact))
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("fiscal_year", 0)), reverse=True)

    cases: list[dict[str, object]] = []
    for target in search_targets:
        corp = alias_to_corp.get(str(target))
        for account_id, label in account_labels.items():
            rows = grouped.get((corp, account_id), []) if corp else []
            expected = rows[:1]
            cases.append({
                "case_id": f"latest:{target}:{account_id}",
                "kind": "latest",
                "question": f"{target}의 최근 사업보고서 기준 {label}을 알려줘",
                "company": str(target),
                "account_id": account_id,
                "expected_facts": expected,
                "expected_answerable": bool(expected),
                "correction_case": bool(corp in corrected_corps),
                "finance_revenue_alias_case": bool(
                    expected
                    and account_id == "revenue"
                    and str(expected[0].get("account_name_raw", "")) not in {"매출", "매출액"}
                ),
            })

    for (corp, account_id), rows in sorted(grouped.items()):
        if account_id not in account_labels or len(rows) < 3:
            continue
        selected = rows[:3]
        if len({int(row.get("fiscal_year", 0)) for row in selected}) != 3:
            continue
        name = corp_to_name.get(corp) or str(selected[0].get("listed_name", corp))
        cases.append({
            "case_id": f"three_year:{corp}:{account_id}",
            "kind": "three_year",
            "question": f"{name} 최근 3개년 {account_labels[account_id]}을 비교해줘",
            "company": name,
            "account_id": account_id,
            "expected_facts": selected,
            "expected_answerable": True,
            "correction_case": corp in corrected_corps,
            "finance_revenue_alias_case": bool(
                account_id == "revenue"
                and str(selected[0].get("account_name_raw", "")) not in {"매출", "매출액"}
            ),
        })
    return cases


def evaluate_release_answer(case: Mapping[str, object], answer: object) -> dict[str, object]:
    """Require exact admitted facts, citations, and clean abstention."""
    expected = [dict(item) for item in case.get("expected_facts", []) if isinstance(item, dict)]
    answerable = bool(getattr(answer, "answerable", False))
    verified = bool(getattr(answer, "verified", False))
    actual_facts = [dict(item) for item in getattr(answer, "financial_facts", []) if isinstance(item, dict)]
    numeric_values = list(getattr(answer, "numeric_values", []) or [])
    citations = {str(item) for item in (getattr(answer, "citation_ids", []) or [])}
    failures: list[str] = []

    if not expected:
        if answerable:
            failures.append("unexpected_answer")
        if numeric_values or actual_facts:
            failures.append("false_numeric_claim")
        if citations:
            failures.append("unexpected_citation")
    else:
        if not answerable:
            failures.append("unexpected_abstention")
        if not verified:
            failures.append("unverified_answer")
        if sorted(_fact_key(item) for item in actual_facts) != sorted(_fact_key(item) for item in expected):
            failures.append("fact_metadata_mismatch")
        expected_numbers = _decimal_values(item.get("value_numeric") for item in expected)
        actual_numbers = _decimal_values(numeric_values)
        if expected_numbers is None or actual_numbers is None or sorted(expected_numbers) != sorted(actual_numbers):
            failures.append("numeric_mismatch")
        expected_citations = {
            str(evidence_id)
            for item in expected
            for evidence_id in item.get("evidence_ids", [])
        }
        if citations != expected_citations:
            failures.append("citation_mismatch")

    return {
        "case_id": str(case.get("case_id", "")),
        "kind": str(case.get("kind", "")),
        "passed": not failures,
        "failures": failures,
        "expected_fact_count": len(expected),
        "actual_fact_count": len(actual_facts),
    }


def measure_concurrent_requests(request: object, *, request_count: int = 20) -> dict[str, object]:
    """Start a fixed request wave and report errors plus nearest-rank p95."""
    if not callable(request):
        raise TypeError("request must be callable")
    start = Event()

    def invoke() -> tuple[float, str | None]:
        start.wait()
        began = perf_counter()
        try:
            request()
        except Exception as exc:  # the release report records errors without hiding the wave
            return (perf_counter() - began) * 1_000, type(exc).__name__
        return (perf_counter() - began) * 1_000, None

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        futures = [executor.submit(invoke) for _ in range(request_count)]
        start.set()
        observations = [future.result() for future in futures]
    latencies = sorted(round(item[0], 3) for item in observations)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1)
    errors = [item[1] for item in observations if item[1] is not None]
    return {
        "request_count": request_count,
        "error_count": len(errors),
        "error_types": sorted(set(errors)),
        "p95_ms": latencies[p95_index] if latencies else None,
        "latencies_ms": latencies,
    }


def release_hard_gate(
    results: Sequence[Mapping[str, object]],
    *,
    concurrency: Mapping[str, object],
) -> tuple[bool, list[str]]:
    """Apply the non-negotiable correctness and 20-request SQLite gates."""
    reasons: list[str] = []
    if not results or any(not bool(item.get("passed")) for item in results):
        reasons.append("case_failures")
    if int(concurrency.get("request_count", 0)) != 20:
        reasons.append("concurrency_request_count")
    if int(concurrency.get("error_count", 0)) != 0:
        reasons.append("concurrency_errors")
    p95 = concurrency.get("p95_ms")
    if p95 is None or float(p95) > 2_000.0:
        reasons.append("concurrency_p95_exceeded")
    return not reasons, reasons
