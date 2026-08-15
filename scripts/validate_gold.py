"""Validate Gold QA candidates and keep review readiness separate from Gold release."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_db.gold_validation import load_jsonl, validate_gold_dataset

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "data/derived/gold_qa.jsonl"
DEFAULT_SCHEMA = ROOT / "config/gold_annotation_schema.json"
DEFAULT_DB = ROOT / "data/derived/disclosure_corpus.sqlite"
DEFAULT_OUTPUT = ROOT / "data/derived/gold_qa_validation.json"


def validate(gold_path: Path, schema_path: Path, database: Path) -> dict[str, object]:
    # Parse the declared schema so a malformed/missing contract fails before data validation.
    json.loads(schema_path.read_text(encoding="utf-8"))
    candidates_path = gold_path.with_name("gold_candidates.jsonl")
    result = validate_gold_dataset(
        database=database,
        gold_path=gold_path,
        candidates_path=candidates_path,
    )
    rows = load_jsonl(gold_path)
    by_type: dict[str, int] = {}
    for item in rows:
        by_type[str(item.get("question_type"))] = by_type.get(str(item.get("question_type")), 0) + 1
    errors = [f"{item['question_id']}: {item['rule_id']} - {item['message']}" for item in result["issues"]]
    return {
        **result,
        "gold": str(gold_path),
        "rows": len(rows),
        "by_type": by_type,
        "errors": errors,
        # Backward-compatible name: this means safe to hand to a human reviewer, not released Gold.
        "ok": bool(result["candidate_review_ready"]),
        "release_ok": bool(result["gold_release_gate_passed"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = validate(args.gold, args.schema, args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["candidate_review_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
