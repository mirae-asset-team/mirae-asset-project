"""Render a markdown QA report for one mass-QA run from the local ledger."""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LEDGER = PROJECT / "runs" / "qa_mass.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--clusters", default="", help="optional clusters JSON path")
    parser.add_argument("--output", default="", help="output markdown path")
    args = parser.parse_args()

    connection = sqlite3.connect(LEDGER)
    rows = connection.execute(
        "SELECT category, expected, verdict, status, company, flags, latency_s"
        " FROM result WHERE run_id=?",
        (args.run_id,),
    ).fetchall()
    if not rows:
        raise SystemExit(f"no rows for run {args.run_id}")
    run_meta = connection.execute(
        "SELECT started_at, base_url, git_commit, notes FROM run WHERE run_id=?",
        (args.run_id,),
    ).fetchone()

    total = len(rows)
    verdicts = collections.Counter(r[2] for r in rows)
    latencies = sorted(r[6] for r in rows if r[6] is not None)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    by_category = collections.defaultdict(lambda: collections.Counter())
    for category, _, verdict, *_ in rows:
        by_category[category][verdict] += 1

    by_company = collections.defaultdict(lambda: collections.Counter())
    for _, _, verdict, _, company, *_ in rows:
        by_company[company][verdict] += 1
    worst_companies = sorted(
        by_company.items(),
        key=lambda item: -(item[1]["fail"] + item[1]["error"]) / max(1, sum(item[1].values())),
    )[:10]

    judgment_rows = connection.execute(
        "SELECT failure_class, COUNT(*) FROM judgment WHERE run_id=? GROUP BY failure_class",
        (args.run_id,),
    ).fetchall()

    lines: list[str] = []
    lines.append(f"# 대량 QA 리포트 — run `{args.run_id}`")
    if run_meta:
        lines.append("")
        lines.append(f"- 시작: {run_meta[0]} / 대상: {run_meta[1]} / 하네스 커밋: {run_meta[2]}")
        if run_meta[3]:
            lines.append(f"- 메모: {run_meta[3]}")
    lines.append("")
    lines.append(f"## 총괄: {total}문항 — pass {verdicts.get('pass', 0)} / fail {verdicts.get('fail', 0)} / error {verdicts.get('error', 0)}")
    lines.append(f"- 지연 p50 {p50:.1f}s / p95 {p95:.1f}s")
    lines.append("")
    lines.append("## 카테고리별")
    lines.append("")
    lines.append("| 카테고리 | pass | fail | error | 실패율 |")
    lines.append("|---|---|---|---|---|")
    for category, counter in sorted(by_category.items(), key=lambda item: -(item[1]["fail"] + item[1]["error"])):
        subtotal = sum(counter.values())
        bad = counter["fail"] + counter["error"]
        lines.append(f"| {category} | {counter['pass']} | {counter['fail']} | {counter['error']} | {100 * bad / subtotal:.0f}% |")
    lines.append("")
    lines.append("## 실패율 상위 기업 (10)")
    lines.append("")
    lines.append("| 기업 | pass | fail | error |")
    lines.append("|---|---|---|---|")
    for company, counter in worst_companies:
        lines.append(f"| {company} | {counter['pass']} | {counter['fail']} | {counter['error']} |")
    if judgment_rows:
        lines.append("")
        lines.append("## 서브에이전트 판정 (확정 실패 분류)")
        lines.append("")
        for failure_class, count in sorted(judgment_rows, key=lambda item: -item[1]):
            lines.append(f"- {failure_class}: {count}건")
    if args.clusters and Path(args.clusters).exists():
        clusters = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
        lines.append("")
        lines.append("## 근본원인 클러스터")
        lines.append("")
        for cluster in clusters.get("clusters", []):
            lines.append(f"### [{cluster['severity']}] {cluster['name']} — {cluster['count']}건")
            lines.append(f"- 원인: {cluster['root_cause_hypothesis']}")
            lines.append(f"- 조치: {cluster['suggested_fix']}")
            lines.append("")
        summary = clusters.get("summary")
        if summary:
            lines.append("## 종합")
            lines.append("")
            lines.append(summary)

    output = Path(args.output) if args.output else PROJECT / "runs" / f"report_{args.run_id}.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
