"""Build a per-company disclosure-type inventory from the local corpus.

Read-only scan of the base corpus; output feeds coverage-aware question
generation so event questions are only asked where the filing type exists.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CORPUS = PROJECT / "data" / "derived" / "disclosure_corpus_semantic_v1.sqlite"
UNIVERSE = PROJECT / "data" / "derived" / "financial_company_universe.json"
OUTPUT = PROJECT / "runs" / "disclosure_inventory.json"

TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("contract_signed", "단일판매ㆍ공급계약체결"),
    ("contract_terminated", "단일판매ㆍ공급계약해지"),
    ("holding_report", "주식등의대량보유상황보고서"),
    ("treasury_acquire", "자기주식취득결정"),
    ("treasury_dispose", "자기주식처분결정"),
    ("treasury_trust_sign", "자기주식취득신탁계약체결"),
    ("treasury_trust_end", "자기주식취득신탁계약해지"),
    ("rights_issue", "유상증자결정"),
    ("bonus_issue", "무상증자결정"),
    ("convertible_bond", "전환사채권발행결정"),
    ("contingent_capital", "조건부자본증권발행결정"),
    ("merger", "회사합병결정"),
    ("company_split", "회사분할"),
    ("capital_reduction", "감자결정"),
    ("lawsuit", "소송등의제기"),
    ("share_exchange", "주식교환ㆍ이전결정"),
    ("investment_judgment", "투자판단관련주요경영사항"),
    ("facility_investment", "신규시설투자"),
    ("annual_report", "사업보고서"),
    ("half_report", "반기보고서"),
    ("quarter_report", "분기보고서"),
)


def main() -> int:
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    corp_names = {c["issuer_corp_code"]: c["listed_name"] for c in universe["companies"]}
    connection = sqlite3.connect(f"file:{CORPUS.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    inventory: dict[str, dict[str, object]] = {}
    placeholders = ",".join(f"'{code}'" for code in corp_names)
    rows = connection.execute(
        f"SELECT issuer_corp_code, report_name_raw, filed_at, filing_id FROM filing"
        f" WHERE issuer_corp_code IN ({placeholders})"
    ).fetchall()
    for corp, report_name, filed_at, filing_id in rows:
        name = str(report_name or "")
        base = re.sub(r"\[[^\]]*\]", "", name)
        entry = inventory.setdefault(corp, {"listed_name": corp_names[corp], "types": {}})
        for type_key, pattern in TYPE_PATTERNS:
            if pattern in base:
                slot = entry["types"].setdefault(type_key, {"count": 0, "latest_filed_at": "", "latest_filing_id": ""})
                slot["count"] += 1
                if str(filed_at) > str(slot["latest_filed_at"]):
                    slot["latest_filed_at"] = str(filed_at)
                    slot["latest_filing_id"] = str(filing_id)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(inventory, ensure_ascii=False, indent=1), encoding="utf-8")
    covered = sum(1 for entry in inventory.values() if entry["types"])
    type_totals: dict[str, int] = {}
    for entry in inventory.values():
        for type_key, slot in entry["types"].items():
            type_totals[type_key] = type_totals.get(type_key, 0) + 1
    print(f"companies with types: {covered}/{len(corp_names)} -> {OUTPUT}")
    for type_key, company_count in sorted(type_totals.items(), key=lambda item: -item[1]):
        print(f"  {type_key:22s} {company_count} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
