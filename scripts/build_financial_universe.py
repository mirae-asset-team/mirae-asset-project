from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Sequence

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.financial_universe import build_financial_universe, canonical_manifest_bytes


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the attested financial-company annual-report universe")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output in {args.database.resolve(), args.attestation.resolve()}:
        parser.error("output must not overwrite an input")
    return args


def _write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    if not verify_fast_identity(args.database, attestation):
        raise ValueError("database attestation mismatch")
    manifest = build_financial_universe(
        args.database,
        source_database_sha256=attestation.sha256,
    )
    content = canonical_manifest_bytes(manifest)
    _write_atomically(args.output, content)
    print(
        json.dumps(
            {
                "status": "ok",
                "company_count": manifest["company_count"],
                "searchable_alias_count": manifest["searchable_alias_count"],
                "rejected_company_count": manifest["rejected_company_count"],
                "manifest_sha256": hashlib.sha256(content).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
