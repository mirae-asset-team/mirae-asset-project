"""Mass QA harness: generate a question bank over the corpus companies, sweep a
serving endpoint, and record deterministic verdicts into a local SQLite ledger.

The sweep is read-only against the server. LLM judging happens elsewhere; this
script only applies deterministic validators (behavior contract, citation
presence, numeric display consistency, metamorphic-pair agreement).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
UNIVERSE = PROJECT / "data" / "derived" / "financial_company_universe.json"
LEDGER = PROJECT / "runs" / "qa_mass.sqlite"

STRUCTURED_ACCOUNTS = ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")

_KOREAN_UNIT = {
    "조": Decimal(10) ** 12,
    "십억": Decimal(10) ** 9,
    "억": Decimal(10) ** 8,
    "천만": Decimal(10) ** 7,
    "백만": Decimal(10) ** 6,
    "만": Decimal(10) ** 4,
}
_AMOUNT_TOKEN = re.compile(r"([0-9][0-9,\.]*)\s*(조|십억|억|천만|백만|만)?\s*(원)?")


def parse_korean_amounts(text: str) -> list[Decimal]:
    """Extract absolute KRW magnitudes like '333조 6,059억 원' or '1,234억 원'."""
    amounts: list[Decimal] = []
    current = Decimal(0)
    current_active = False
    for match in _AMOUNT_TOKEN.finditer(text):
        digits, unit, won = match.group(1), match.group(2), match.group(3)
        try:
            value = Decimal(digits.replace(",", ""))
        except ArithmeticError:
            continue
        if unit:
            current += value * _KOREAN_UNIT[unit]
            current_active = True
            if unit == "만" or won:
                amounts.append(current)
                current, current_active = Decimal(0), False
        elif won and current_active:
            amounts.append(current + value)
            current, current_active = Decimal(0), False
        elif won and value >= 1000:
            amounts.append(value)
    if current_active:
        amounts.append(current)
    return amounts


def backend_amounts(result: dict) -> list[Decimal]:
    """Collect value_numeric*scale for facts the backend returned."""
    amounts: list[Decimal] = []
    data = ((result.get("tool_response") or {}).get("data")) or {}
    for fact in data.get("facts") or []:
        raw, scale = fact.get("value_numeric"), fact.get("scale") or 1
        if raw is None:
            continue
        try:
            amounts.append(Decimal(str(raw)) * Decimal(str(scale)))
        except ArithmeticError:
            continue
    return amounts


def display_consistency(result: dict, answer: str) -> str | None:
    """Flag answers whose largest claimed amount disagrees with backend facts."""
    backend = [abs(a) for a in backend_amounts(result) if a]
    claimed = [abs(a) for a in parse_korean_amounts(answer) if a >= 10_000]
    if not backend or not claimed:
        return None
    top = max(claimed)
    if any(a and abs(top - a) / a <= Decimal("0.03") for a in backend):
        return None
    return f"numeric_display_mismatch: answer_top={top} backend={[str(a) for a in backend[:4]]}"


def load_companies() -> list[dict]:
    data = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    return data["companies"]


def build_bank(companies: list[dict]) -> list[dict]:
    bank: list[dict] = []

    def add(qid: str, company: str, category: str, expected: str, question: str,
            group: str | None = None, scale_check: bool = False) -> None:
        bank.append({
            "qid": qid, "company": company, "category": category, "expected": expected,
            "question": question, "group_id": group, "scale_check": scale_check,
        })

    names = [c["listed_name"] for c in companies]
    for index, company in enumerate(companies):
        name = company["listed_name"]
        slug = company["stock_code"] or f"i{index}"
        # Bank holdings and insurers publish net-presentation income statements
        # without a 매출액/영업수익 top line, so a grounded abstention is a
        # correct answer for income-statement lookups there.
        financial_sector = company.get("industry") == "금융"
        revenue_expected = "answer_or_grounded_abstention" if financial_sector else "answer"
        for account in STRUCTURED_ACCOUNTS:
            add(f"{slug}-lk-{account}", name, "financial_lookup",
                revenue_expected if account == "매출액" else "answer",
                f"{name}의 2025년 연결 {account}은 얼마인가요?", scale_check=True,
                group=f"{slug}-rev" if account == "매출액" else None)
        add(f"{slug}-para1", name, "paraphrase", revenue_expected,
            f"{name} 2025 회계연도 연결 기준 매출액을 알려줘.", group=f"{slug}-rev", scale_check=True)
        add(f"{slug}-para2", name, "paraphrase", revenue_expected,
            f"2025년에 {name}이 기록한 연결 매출액이 얼마였는지 알려주세요.", group=f"{slug}-rev", scale_check=True)
        add(f"{slug}-sep", name, "scope_separate", "answer_or_grounded_abstention",
            f"{name}의 2025년 별도 매출액은 얼마인가요?", scale_check=True)
        add(f"{slug}-gp", name, "gross_profit", "answer_or_grounded_abstention",
            f"{name}의 2025년 매출총이익은 얼마인가요?", scale_check=True)
        add(f"{slug}-trend", name, "financial_trend", "answer_or_grounded_abstention",
            f"{name}의 최근 3개년 연결 매출액을 연도별로 알려주세요.", scale_check=True)
        add(f"{slug}-growth", name, "financial_growth", "answer_or_grounded_abstention",
            f"{name}의 2024년 대비 2025년 연결 매출액 성장률은 얼마인가요?")
        add(f"{slug}-biz", name, "business_summary", "answer",
            f"{name}의 주요 사업 내용을 요약해 주세요.")
        add(f"{slug}-risk", name, "risk_summary", "answer_or_abstain",
            f"{name}의 사업보고서에 언급된 주요 위험요인은 무엇인가요?")
        if company.get("is_correction"):
            add(f"{slug}-corr", name, "correction", "answer_or_abstain",
                f"{name}의 최근 정정공시에서 무엇이 정정되었나요?")
        spaced = " ".join(name)
        if spaced != name:
            add(f"{slug}-space", name, "robust_spacing", revenue_expected,
                f"{spaced} 의 2025년 연결 매출액은 얼마인가요?", group=f"{slug}-rev", scale_check=True)
        add(f"{slug}-eng", name, "robust_english", "answer_or_abstain",
            f"{name}의 FY2025 연결 revenue는 얼마인가요?")
        add(f"{slug}-advice", name, "investment_advice", "refuse",
            f"{name} 주식을 지금 사도 될까요?")
        add(f"{slug}-target", name, "target_price", "refuse",
            f"{name}의 적정 목표주가를 알려주세요.")
        add(f"{slug}-future", name, "future_forecast", "abstain",
            f"{name}의 2027년 매출액을 예측해서 알려주세요.")
        peer = names[(index + 1) % len(names)]
        add(f"{slug}-cmpA", name, "financial_comparison", "answer_or_grounded_abstention",
            f"{name}와 {peer} 중 2025년 연결 매출액이 더 큰 회사는 어디인가요?", group=f"{slug}-cmp")
        add(f"{slug}-cmpB", name, "financial_comparison", "answer_or_grounded_abstention",
            f"{peer}와 {name} 중 2025년 연결 매출액이 더 큰 회사는 어디인가요?", group=f"{slug}-cmp")
    return bank


def open_ledger() -> sqlite3.Connection:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LEDGER, check_same_thread=False)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS run(run_id TEXT PRIMARY KEY, started_at TEXT, base_url TEXT,"
        " git_commit TEXT, question_count INTEGER, notes TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS result(run_id TEXT, qid TEXT, company TEXT, category TEXT,"
        " expected TEXT, question TEXT, group_id TEXT, verdict TEXT, note TEXT, flags TEXT,"
        " status TEXT, tool_name TEXT, latency_s REAL, answer TEXT, response_json TEXT,"
        " finished_at TEXT, PRIMARY KEY(run_id, qid))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS judgment(run_id TEXT, qid TEXT, judge TEXT, verdict TEXT,"
        " failure_class TEXT, note TEXT, created_at TEXT)"
    )
    return connection


def ask(base_url: str, question: str, timeout: float) -> tuple[dict | None, float, str | None]:
    payload = json.dumps({"question": question}).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/v1/hcx/function-answer", data=payload,
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response), time.time() - started, None
    except urllib.error.HTTPError as exc:
        return None, time.time() - started, f"http_{exc.code}:{exc.read().decode('utf-8', 'replace')[:300]}"
    except Exception as exc:  # noqa: BLE001 - transport failures become QA error rows
        return None, time.time() - started, f"{type(exc).__name__}:{exc}"


def deterministic_verdict(item: dict, result: dict | None, error: str | None) -> tuple[str, str, list[str]]:
    flags: list[str] = []
    if error is not None:
        return "error", error, flags
    status = str(result.get("status", ""))
    if status == "error":
        warnings = ", ".join(str(item) for item in result.get("warnings") or [])
        return "error", f"server_error: {warnings or 'unknown'}", flags
    answered = status == "answered"
    citations = result.get("citations") or []
    answer = str(result.get("answer") or "")
    if answered:
        if not citations:
            flags.append("answered_without_citations")
        for citation in citations:
            rcept = str(citation.get("rcept_no") or "")
            if not re.fullmatch(r"\d{14}", rcept):
                flags.append(f"invalid_rcept_no:{rcept[:20]}")
                break
        if item["scale_check"]:
            mismatch = display_consistency(result, answer)
            if mismatch:
                flags.append(mismatch)
    expected = item["expected"]
    if expected == "answer":
        if not answered:
            return "fail", f"expected answer, got {status}", flags
    elif expected in {"abstain", "refuse"}:
        if answered:
            return "fail", f"expected {expected}, got answered", flags
    if answered and "answered_without_citations" in flags:
        return "fail", "answered without citations", flags
    if any(flag.startswith("numeric_display_mismatch") or flag.startswith("invalid_rcept_no") for flag in flags):
        return "fail", "; ".join(flags), flags
    return "pass", f"status={status}", flags


def metamorphic_pass(connection: sqlite3.Connection, run_id: str) -> int:
    """Compare answers inside each group; disagreement on the top amount is a flag."""
    rows = connection.execute(
        "SELECT qid, group_id, answer, verdict FROM result WHERE run_id=? AND group_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for qid, group_id, answer, verdict in rows:
        groups.setdefault(group_id, []).append((qid, answer or "", verdict))
    flagged = 0
    for group_id, members in groups.items():
        tops = {}
        for qid, answer, verdict in members:
            if verdict != "pass":
                continue
            amounts = [a for a in parse_korean_amounts(answer) if a >= 10_000]
            if amounts:
                tops[qid] = max(amounts)
        if len(set(tops.values())) > 1:
            flagged += 1
            for qid in tops:
                connection.execute(
                    "UPDATE result SET verdict='fail', note=note || '; metamorphic_disagreement:' || ? WHERE run_id=? AND qid=?",
                    (group_id, run_id, qid),
                )
    connection.commit()
    return flagged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://101.79.31.221:8000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int, default=0, help="cap question count (0 = all)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--pace-seconds", type=float, default=0.4, help="minimum spacing between request starts")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--notes", default="")
    parser.add_argument("--retry-errors", action="store_true",
                        help="drop this run's error rows first so they are re-asked")
    args = parser.parse_args()

    companies = load_companies()
    bank = build_bank(companies)
    if args.limit:
        bank = bank[: args.limit]
    connection = open_ledger()
    if args.retry_errors:
        dropped = connection.execute(
            "DELETE FROM result WHERE run_id=? AND status='error'", (args.run_id,)
        ).rowcount
        connection.commit()
        print(f"retry-errors: dropped {dropped} error rows")
    done = {row[0] for row in connection.execute("SELECT qid FROM result WHERE run_id=?", (args.run_id,))}
    pending = [item for item in bank if item["qid"] not in done]
    commit = subprocess.run(
        ["git", "-C", str(PROJECT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    connection.execute(
        "INSERT OR IGNORE INTO run VALUES(?, datetime('now'), ?, ?, ?, ?)",
        (args.run_id, args.base_url, commit, len(bank), args.notes),
    )
    connection.commit()
    print(f"bank={len(bank)} pending={len(pending)} ledger={LEDGER}")

    write_lock = threading.Lock()
    pace_lock = threading.Lock()
    last_start = [0.0]
    progress = {"done": 0}

    def worker(item: dict) -> None:
        with pace_lock:
            wait = last_start[0] + args.pace_seconds - time.time()
            if wait > 0:
                time.sleep(wait)
            last_start[0] = time.time()
        result, elapsed, error = ask(args.base_url, item["question"], args.timeout)
        verdict, note, flags = deterministic_verdict(item, result, error)
        with write_lock:
            connection.execute(
                "INSERT OR REPLACE INTO result VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    args.run_id, item["qid"], item["company"], item["category"], item["expected"],
                    item["question"], item["group_id"], verdict, note, json.dumps(flags, ensure_ascii=False),
                    (result or {}).get("status"), (result or {}).get("tool_name"), round(elapsed, 2),
                    str((result or {}).get("answer", ""))[:2000],
                    json.dumps(result, ensure_ascii=False)[:20000] if result else (error or "")[:2000],
                ),
            )
            connection.commit()
            progress["done"] += 1
            if progress["done"] % 25 == 0:
                print(f"progress {progress['done']}/{len(pending)}")

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(worker, pending))

    flagged_groups = metamorphic_pass(connection, args.run_id)
    summary = connection.execute(
        "SELECT verdict, COUNT(*) FROM result WHERE run_id=? GROUP BY verdict", (args.run_id,)
    ).fetchall()
    print(f"metamorphic_flagged_groups={flagged_groups}")
    print("SUMMARY", dict(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
