from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import build_database, export_gold_candidates, export_inventory, query_database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disclosure-db")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Build a new immutable-revision SQLite validation database")
    build.add_argument("--corpus", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--max-filings", type=int)
    build.add_argument("--progress-every", type=int, default=100)
    build.add_argument("--no-fts", action="store_true")
    inventory = sub.add_parser("export-inventory")
    inventory.add_argument("--database", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    gold = sub.add_parser("export-gold-candidates")
    gold.add_argument("--database", type=Path, required=True)
    gold.add_argument("--output", type=Path, required=True)
    gold.add_argument("--per-stratum", type=int, default=2)
    query = sub.add_parser("query")
    query.add_argument("--database", type=Path, required=True)
    query.add_argument("--text", required=True)
    query.add_argument("--company")
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--as-of", help="Answer-time filing cutoff in YYYY-MM-DD form")
    query.add_argument(
        "--include-unsafe",
        action="store_true",
        help="Audit only: include unresolved/missing-original and obsolete filing versions",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "build":
        result = build_database(
            args.corpus,
            args.output,
            max_filings=args.max_filings,
            progress_every=args.progress_every,
            build_fts=not args.no_fts,
        )
    elif args.command == "export-inventory":
        result = {"rows": export_inventory(args.database, args.output), "output": str(args.output)}
    elif args.command == "export-gold-candidates":
        result = {"rows": export_gold_candidates(args.database, args.output, args.per_stratum), "output": str(args.output)}
    else:
        result = query_database(
            args.database,
            args.text,
            company=args.company,
            limit=args.limit,
            as_of=args.as_of,
            include_unsafe=args.include_unsafe,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
