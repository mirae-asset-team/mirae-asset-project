"""Build a corpus-verified ground-truth table of financial grains from QA runs.

Served answers carry both the backend fact (``value_numeric`` x ``scale``) and
the evidence cell it was read from. This script harvests those grains, re-reads
every cited cell out of the immutable base corpus, and keeps only the grains
whose served value is reproduced by the corpus itself. Grains that disagree
across questions are dropped rather than guessed at.

The result lets the QA harness check whether an answer is *correct*, not just
internally consistent.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LEDGER = PROJECT / "runs" / "qa_mass.sqlite"
CORPUS = PROJECT / "data" / "derived" / "disclosure_corpus_semantic_v1.sqlite"
OUTPUT = PROJECT / "runs" / "ground_truth_v1.json"

# Korean filings write losses as (1,234), △1,234 or -1,234; all three are
# negative and a magnitude-only reading would silently flip the sign.
_NUMBER = re.compile(r"(?P<paren>\()?\s*(?P<sign>[-△▲▽])?\s*(?P<digits>[0-9][0-9,]*)\s*(?(paren)\)|)")


def open_corpus() -> sqlite3.Connection:
    """Open the distribution corpus strictly read-only and immutable."""
    uri = f"file:{CORPUS.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.execute("PRAGMA query_only=ON")
    return connection


def cell_numbers(text: str) -> list[Decimal]:
    """Read every signed number in a statement cell, honouring loss notation."""
    values: list[Decimal] = []
    for match in _NUMBER.finditer(text or ""):
        try:
            value = Decimal(match.group("digits").replace(",", ""))
        except ArithmeticError:
            continue
        if match.group("paren") or match.group("sign"):
            value = -value
        values.append(value)
    return values


def harvest(connection: sqlite3.Connection, run_ids: list[str]) -> dict:
    """Collect every (company, account, year, scope) grain a run answered."""
    placeholders = ",".join("?" for _ in run_ids)
    rows = connection.execute(
        f"SELECT qid, company, response_json FROM result"
        f" WHERE run_id IN ({placeholders}) AND status='answered'"
        f" AND response_json LIKE '%value_numeric%'",
        run_ids,
    ).fetchall()
    grains: dict[tuple, list[dict]] = defaultdict(list)
    for qid, company, payload in rows:
        try:
            served = json.loads(payload)
        except (TypeError, ValueError):
            continue  # truncated ledger payloads are skipped, never repaired
        tool_response = served.get("tool_response") or {}
        facts = ((tool_response.get("data") or {}).get("facts")) or []
        items = {
            str(item.get("evidence_id")): item
            for item in ((tool_response.get("evidence_bundle") or {}).get("items") or [])
        }
        for fact in facts:
            raw, scale = fact.get("value_numeric"), fact.get("scale") or 1
            period = fact.get("period") or {}
            end = str(period.get("period_end") or period.get("instant_date") or "")
            identifiers = fact.get("company_identifiers") or {}
            if raw is None or len(end) < 4:
                continue
            try:
                value = Decimal(str(raw)) * Decimal(str(scale))
            except ArithmeticError:
                continue
            key = (
                str(identifiers.get("issuer_corp_code") or ""),
                str(fact.get("account_id") or ""),
                int(end[:4]),
                str(fact.get("scope") or ""),
            )
            grains[key].append({
                "qid": qid,
                "company": str(identifiers.get("listed_name") or company),
                "stock_code": str(identifiers.get("stock_code") or ""),
                "value": value,
                "value_numeric": str(raw),
                "scale": int(scale),
                "filing_id": str(fact.get("filing_id") or ""),
                "rcept_no": str(fact.get("rcept_no") or ""),
                "account_name": str(fact.get("account_name") or ""),
                "period_end": end,
                "evidence_ids": [
                    eid for eid in (fact.get("evidence_ids") or []) if eid in items
                ],
                "evidence_texts": [
                    str(items[eid].get("text") or "")
                    for eid in (fact.get("evidence_ids") or []) if eid in items
                ],
            })
    return grains


def corpus_confirms(corpus: sqlite3.Connection, observation: dict) -> tuple[bool, str]:
    """Re-read the cited cells from the corpus and rebuild the served value."""
    evidence_ids = observation["evidence_ids"]
    if not evidence_ids:
        return False, "no_resolvable_evidence"
    cells: list[Decimal] = []
    for evidence_id in evidence_ids:
        row = corpus.execute(
            "SELECT filing_id, text_normalized, text_raw FROM table_cell WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            return False, f"evidence_absent_from_corpus:{evidence_id}"
        filing_id, normalized, raw_text = row
        if observation["filing_id"] and str(filing_id) != observation["filing_id"]:
            return False, f"evidence_filing_mismatch:{evidence_id}"
        numbers = cell_numbers(str(normalized or raw_text or ""))
        if not numbers:
            return False, f"evidence_cell_without_number:{evidence_id}"
        cells.append(numbers[0])
    scale = Decimal(observation["scale"])
    scaled = [cell * scale for cell in cells]
    value = observation["value"]
    if value in scaled or value == sum(scaled):
        return True, "corpus_confirmed"
    return False, f"corpus_value_mismatch: served={value} cells={[str(c) for c in scaled[:4]]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", action="append", required=True,
                        help="ledger run to harvest; repeat for several runs")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    ledger = sqlite3.connect(LEDGER)
    grains = harvest(ledger, args.run_id)
    corpus = open_corpus()

    confirmed: dict[str, dict] = {}
    stats = {"grains": len(grains), "confirmed": 0, "conflicting": 0,
             "unconfirmed": 0, "observations": 0}
    rejects: list[dict] = []

    for key, observations in sorted(grains.items()):
        corp_code, account_id, fiscal_year, scope = key
        stats["observations"] += len(observations)
        distinct = {observation["value"] for observation in observations}
        if len(distinct) > 1:
            # The server contradicted itself across questions on one grain; a
            # ground truth cannot be asserted, and the disagreement is itself
            # a finding recorded for the failure ledger.
            stats["conflicting"] += 1
            rejects.append({
                "key": [corp_code, account_id, fiscal_year, scope],
                "company": observations[0]["company"],
                "reason": "served_values_disagree",
                "values": sorted(str(value) for value in distinct),
                "qids": [observation["qid"] for observation in observations[:6]],
            })
            continue
        witness = observations[0]
        ok, reason = corpus_confirms(corpus, witness)
        if not ok:
            stats["unconfirmed"] += 1
            rejects.append({
                "key": [corp_code, account_id, fiscal_year, scope],
                "company": witness["company"], "reason": reason,
                "qids": [observation["qid"] for observation in observations[:6]],
            })
            continue
        stats["confirmed"] += 1
        confirmed[f"{corp_code}|{account_id}|{fiscal_year}|{scope}"] = {
            "issuer_corp_code": corp_code,
            "company": witness["company"],
            "stock_code": witness["stock_code"],
            "account_id": account_id,
            "account_name": witness["account_name"],
            "fiscal_year": fiscal_year,
            "scope": scope,
            "value": str(witness["value"]),
            "value_numeric": witness["value_numeric"],
            "scale": witness["scale"],
            "filing_id": witness["filing_id"],
            "rcept_no": witness["rcept_no"],
            "period_end": witness["period_end"],
            "evidence_ids": witness["evidence_ids"],
            "witness_count": len(observations),
        }

    payload = {
        "schema_version": "qa-ground-truth-v1",
        "source_runs": args.run_id,
        "corpus": CORPUS.name,
        "stats": stats,
        "grains": confirmed,
        "rejects": rejects,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print(f"wrote {args.output} grains={len(confirmed)} rejects={len(rejects)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
