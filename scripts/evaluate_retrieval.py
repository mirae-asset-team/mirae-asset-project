from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_db.retrieval_evaluation import evaluate_retrieval


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate SQLite FTS retrieval against approved Gold evidence")
    parser.add_argument("--database", type=Path, default=ROOT / "data/derived/disclosure_corpus.sqlite")
    parser.add_argument("--gold", type=Path, default=ROOT / "data/derived/gold_qa.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/derived/retrieval_gold_baseline.json")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    result = evaluate_retrieval(database=args.database, gold_path=args.gold, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "evaluations"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
