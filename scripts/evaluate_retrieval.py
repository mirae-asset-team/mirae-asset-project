from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from disclosure_db.attestation import load_distribution_attestation
from disclosure_db.evidence_service import EvidenceService
from disclosure_db.retrieval_evaluation import (
    audit_financial_fact_coverage,
    evaluate_hybrid_retrieval,
    evaluate_retrieval,
)


ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate SQLite FTS retrieval against approved Gold evidence")
    parser.add_argument("--database", type=Path, default=ROOT / "data/derived/disclosure_corpus.sqlite")
    parser.add_argument("--gold", type=Path, default=ROOT / "data/derived/gold_qa.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/derived/retrieval_gold_baseline.json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--search-index", type=Path)
    parser.add_argument(
        "--financial-seed",
        type=Path,
        default=ROOT / "data/derived/financial_fact_gold_seed.jsonl",
    )
    parser.add_argument("--inventory-output", type=Path)
    args = parser.parse_args(argv)
    structured_values = (args.overlay, args.attestation, args.search_index)
    configured_count = sum(value is not None for value in structured_values)
    if configured_count not in {0, len(structured_values)}:
        parser.error("hybrid evaluation requires --overlay, --attestation, and --search-index together")
    input_paths = {
        Path(value).resolve()
        for value in (
            args.database, args.gold, args.overlay, args.attestation,
            args.search_index, args.financial_seed,
        )
        if value is not None
    }
    output_paths = [Path(args.output).resolve()]
    if args.inventory_output is not None:
        output_paths.append(Path(args.inventory_output).resolve())
    if len(set(output_paths)) != len(output_paths):
        parser.error("--output and --inventory-output must be different paths")
    if any(path in input_paths for path in output_paths):
        parser.error("output paths must not collide with any input path")
    return args


def _printable_summary(result: dict[str, object]) -> dict[str, object]:
    summary = {key: value for key, value in result.items() if key != "evaluations"}
    hybrid = summary.get("hybrid")
    if isinstance(hybrid, dict):
        summary["hybrid"] = {key: value for key, value in hybrid.items() if key != "evaluations"}
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.overlay is not None:
        attestation = load_distribution_attestation(args.attestation, database=args.database)
        service = EvidenceService(
            args.database,
            args.overlay,
            attestation=attestation,
            search_database=args.search_index,
        )
        result = evaluate_hybrid_retrieval(
            database=args.database,
            gold_path=args.gold,
            evidence_service=service,
            limit=args.limit,
        )
    else:
        result = evaluate_retrieval(database=args.database, gold_path=args.gold, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.inventory_output is not None:
        inventory = audit_financial_fact_coverage(
            seed_path=args.financial_seed,
            gold_path=args.gold,
        )
        args.inventory_output.parent.mkdir(parents=True, exist_ok=True)
        args.inventory_output.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(_printable_summary(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
