"""Build canonical agent-audited free-form retrieval Gold."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.freeform_evaluation import (
    build_freeform_gold,
    build_freeform_manifest,
    derive_freeform_source_records,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    inputs = {path.resolve() for path in (args.gold, args.contract, args.templates, args.database, args.attestation)}
    outputs = {args.output.resolve(), args.manifest.resolve()}
    if len(outputs) != 2 or inputs.intersection(outputs):
        raise SystemExit("input_output_path_collision")
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    if not verify_fast_identity(args.database, attestation):
        raise SystemExit("database_attestation_mismatch")

    audited_gold = [
        json.loads(line)
        for line in args.gold.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    templates = json.loads(args.templates.read_text(encoding="utf-8"))
    source_records = derive_freeform_source_records(audited_gold, args.database, contract)
    rows = build_freeform_gold(source_records, contract, templates)
    manifest = build_freeform_manifest(
        rows,
        source_gold_sha256=_sha256(args.gold),
        contract_sha256=_sha256(args.contract),
        templates_sha256=_sha256(args.templates),
        database_sha256=attestation.sha256,
    )
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    _write(args.output, payload)
    _write(args.manifest, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({
        "status": "ok",
        "case_count": manifest["case_count"],
        "content_sha256": manifest["content_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
