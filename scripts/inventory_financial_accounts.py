"""Read-only inventory of financial account IDs, raw labels, and catalog mentions."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from disclosure_db.financial_accounts import resolve_financial_account


def _readonly_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def inventory_database(database: Path) -> dict[str, object]:
    """Inventory existing structured facts and statement-row label candidates."""

    result: dict[str, object] = {
        "path": str(database),
        "financial_fact_accounts": [],
        "primary_statement_label_candidates": [],
    }
    with closing(_readonly_connection(database)) as connection:
        tables = _tables(connection)
        if "financial_fact" in tables:
            result["financial_fact_accounts"] = [
                {
                    "account_id": row["account_id"],
                    "account_name_raw": row["account_name_raw"],
                    "count": int(row["count"]),
                }
                for row in connection.execute(
                    """SELECT account_id,account_name_raw,count(*) AS count
                         FROM financial_fact
                        GROUP BY account_id,account_name_raw
                        ORDER BY account_id,account_name_raw"""
                )
            ]
        if {"table_cell", "table_record"} <= tables:
            result["primary_statement_label_candidates"] = [
                {"account_name_raw": row["text_raw"], "count": int(row["count"])}
                for row in connection.execute(
                    """SELECT c.text_raw,count(*) AS count
                         FROM table_cell c
                         JOIN table_record t ON t.table_id=c.table_id
                        WHERE t.parse_status='success'
                          AND c.column_index=0
                          AND trim(c.text_raw)<>''
                          AND (
                              replace(t.caption,' ','') LIKE '%재무상태표%'
                           OR replace(t.caption,' ','') LIKE '%손익계산서%'
                           OR replace(t.caption,' ','') LIKE '%현금흐름표%'
                           OR replace(t.caption,' ','') LIKE '%자본변동표%'
                          )
                        GROUP BY c.text_raw
                        ORDER BY count(*) DESC,c.text_raw"""
                )
            ]
    return result


def _json_rows(path: Path) -> Iterable[object]:
    if path.suffix.casefold() == ".jsonl":
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: invalid JSON on line {line_no}") from exc
        return
    yield json.loads(path.read_text(encoding="utf-8"))


def _walk(value: object) -> Iterable[tuple[str | None, object]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def inventory_artifact(path: Path) -> dict[str, object]:
    """Inventory explicit account fields and resolvable question/search concepts."""

    account_ids: Counter[str] = Counter()
    raw_labels: Counter[str] = Counter()
    mentions: Counter[tuple[str, str, str]] = Counter()
    question_keys = {"question", "search_concept", "search_concepts"}
    for root in _json_rows(path):
        for key, value in _walk(root):
            if key == "account_id" and isinstance(value, str) and value:
                account_ids[value] += 1
            elif key == "account_name_raw" and isinstance(value, str) and value:
                raw_labels[value] += 1
            elif key in question_keys:
                strings = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                for text in strings:
                    if not isinstance(text, str) or not text:
                        continue
                    resolution = resolve_financial_account(text)
                    if resolution.status == "resolved" and resolution.canonical_id:
                        mentions[(resolution.canonical_id, str(resolution.matched_text or ""), resolution.match_type or "")] += 1
    return {
        "path": str(path),
        "account_ids": [
            {"account_id": key, "count": count}
            for key, count in sorted(account_ids.items())
        ],
        "account_name_raw": [
            {"account_name_raw": key, "count": count}
            for key, count in sorted(raw_labels.items())
        ],
        "catalog_mentions": [
            {"canonical_id": key[0], "matched_text": key[1], "match_type": key[2], "count": count}
            for key, count in sorted(mentions.items())
        ],
    }


def build_inventory(databases: Iterable[Path], artifacts: Iterable[Path]) -> dict[str, object]:
    database_rows = [inventory_database(path) for path in databases]
    artifact_rows = [inventory_artifact(path) for path in artifacts]
    return {
        "schema_version": "financial-account-inventory-v1",
        "databases": database_rows,
        "artifacts": artifact_rows,
        "source_counts": {"databases": len(database_rows), "artifacts": len(artifact_rows)},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract a read-only financial-account inventory from SQLite and JSON/JSONL inputs"
    )
    parser.add_argument("--database", type=Path, action="append", default=[])
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.database and not args.artifact:
        parser.error("at least one --database or --artifact is required")
    missing = [path for path in [*args.database, *args.artifact] if not path.is_file()]
    if missing:
        parser.error(f"input does not exist: {missing[0]}")
    if args.output is not None:
        inputs = {path.resolve() for path in [*args.database, *args.artifact]}
        if args.output.resolve() in inputs:
            parser.error("output must not overwrite an input")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    payload = build_inventory(args.database, args.artifact)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
