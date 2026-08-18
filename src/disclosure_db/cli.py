from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import build_database, export_gold_candidates, export_inventory, query_database
from .agent_contracts import to_jsonable


def _build_search_index(args: argparse.Namespace) -> object:
    from .attestation import load_distribution_attestation
    from .search_index import build_search_index
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    result = build_search_index(args.database, args.output, attestation)
    # Write only after successful atomic promotion.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(to_jsonable(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


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
    search = sub.add_parser("build-search-index")
    search.add_argument("--database", type=Path, required=True)
    search.add_argument("--output", type=Path, required=True)
    search.add_argument("--attestation", type=Path, required=True)
    search.add_argument("--report", type=Path, required=True)
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
    elif args.command == "build-search-index":
        result = _build_search_index(args)
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


def _agent_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disclosure-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("agent-query", help="Answer a question with safe evidence and verification")
    query.add_argument("--database", type=Path, required=True)
    query.add_argument("--overlay", type=Path)
    query.add_argument("--attestation", type=Path)
    query.add_argument("--search-database", type=Path)
    query.add_argument("--question", required=True)
    query.add_argument("--company")
    query.add_argument("--as-of")
    query.add_argument("--limit", type=int, default=20)
    overlay = sub.add_parser("build-financial-overlay")
    overlay.add_argument("--database", type=Path, required=True)
    overlay.add_argument("--overlay", type=Path, required=True)
    overlay.add_argument("--seed", type=Path, required=True)
    agent_overlay = sub.add_parser("build-agent-overlay")
    agent_overlay.add_argument("--database", type=Path, required=True)
    agent_overlay.add_argument("--overlay", type=Path, required=True)
    agent_overlay.add_argument("--financial-seed", type=Path, required=True)
    agent_overlay.add_argument("--predicate-config", type=Path, required=True)
    agent_overlay.add_argument("--attestation", type=Path)
    agent_overlay.add_argument("--report", type=Path, required=True)
    search = sub.add_parser("build-search-index")
    search.add_argument("--database", type=Path, required=True)
    search.add_argument("--output", type=Path, required=True)
    search.add_argument("--attestation", type=Path, required=True)
    search.add_argument("--report", type=Path, required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--database", type=Path, required=True)
    serve.add_argument("--overlay", type=Path)
    serve.add_argument("--attestation", type=Path)
    serve.add_argument("--search-database", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def agent_main() -> None:
    args = _agent_parser().parse_args()
    if args.command == "build-search-index":
        result = _build_search_index(args)
    elif args.command == "build-financial-overlay":
        from .financial_overlay import import_seed
        result = import_seed(args.database, args.overlay, args.seed)
    elif args.command == "build-agent-overlay":
        from .attestation import load_distribution_attestation
        from .financial_overlay import build_agent_overlay
        attestation = load_distribution_attestation(args.attestation, database=args.database) if args.attestation else None
        result = build_agent_overlay(
            args.database,
            args.overlay,
            args.financial_seed,
            args.predicate_config,
            attestation=attestation,
        )
        args.report.write_text(
            json.dumps(to_jsonable(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif args.command == "agent-query":
        from .agent import AgentSettings, DisclosureAgent
        settings = AgentSettings(base_database=args.database, overlay_database=args.overlay, attestation_path=args.attestation, search_database=args.search_database)
        result = to_jsonable(DisclosureAgent(settings).answer(args.question, company=args.company, as_of=args.as_of, limit=args.limit))
    else:
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("serve requires: pip install 'miraeasset-disclosure-db[agent]'") from exc
        from .agent import AgentSettings, DisclosureAgent
        from .api import create_app
        settings = AgentSettings(base_database=args.database, overlay_database=args.overlay, attestation_path=args.attestation, search_database=args.search_database)
        uvicorn.run(create_app(DisclosureAgent(settings)), host=args.host, port=args.port)
        return
    print(json.dumps(to_jsonable(result) if args.command in {"build-financial-overlay", "build-agent-overlay", "build-search-index"} else result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"agent-query", "build-financial-overlay", "build-agent-overlay", "build-search-index", "serve"}:
        agent_main()
    else:
        main()
