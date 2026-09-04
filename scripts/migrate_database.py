from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from disclosure_db.migration import apply_semantic_migration, copy_database, sha256_file


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and validate a migrated copy of the structural SQLite SSOT")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--migration-sql", type=Path, default=ROOT / "sql" / "sqlite_semantic_layer_v1.sql"
    )
    parser.add_argument("--skip-lineage", action="store_true")
    parser.add_argument("--skip-fts", action="store_true")
    parser.add_argument("--skip-source-sha256", action="store_true")
    parser.add_argument("--skip-source-quick-check", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    started = time.perf_counter()
    source_sha = None if args.skip_source_sha256 else sha256_file(source)
    last_percent = -1

    def progress(done: int, total: int) -> None:
        nonlocal last_percent
        percent = int(done * 100 / total) if total else 100
        if percent >= last_percent + 10 or percent == 100:
            print(json.dumps({"stage": "backup", "percent": percent}, ensure_ascii=False), flush=True)
            last_percent = percent

    source_fingerprint = copy_database(
        source, output, progress=progress, verify_source=not args.skip_source_quick_check
    )
    print(json.dumps({"stage": "semantic_migration", "status": "started"}, ensure_ascii=False), flush=True)
    result = apply_semantic_migration(
        output,
        source_database=source,
        migration_sql=args.migration_sql.resolve(),
        rebuild_lineage=not args.skip_lineage,
        rebuild_fts=not args.skip_fts,
    )
    print(json.dumps({"stage": "semantic_migration", "status": "completed"}, ensure_ascii=False), flush=True)
    result["source_file_sha256"] = source_sha
    result["source_fingerprint"] = source_fingerprint
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["quick_check"] != "ok" or result["foreign_key_violations"] != 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
