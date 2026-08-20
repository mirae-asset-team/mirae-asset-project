from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.financial_extraction import extract_financial_candidate_corpus


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract strict annual financial fact candidates")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--rejects", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = {args.database.resolve(), args.attestation.resolve(), args.manifest.resolve()}
    outputs = {args.candidates.resolve(), args.rejects.resolve(), args.summary.resolve()}
    if len(outputs) != 3 or inputs & outputs:
        parser.error("outputs must be distinct and must not overwrite inputs")
    return args


def _jsonl_bytes(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
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
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if str(manifest.get("source_database_sha256") or "") != attestation.sha256:
        raise ValueError("manifest source does not match database attestation")
    result = extract_financial_candidate_corpus(args.database, manifest)
    candidates_bytes = _jsonl_bytes(result["candidates"])  # type: ignore[arg-type]
    rejects_bytes = _jsonl_bytes(result["rejects"])  # type: ignore[arg-type]
    summary = {
        "schema_version": result["schema_version"],
        "source_database_sha256": result["source_database_sha256"],
        **result["summary"],  # type: ignore[dict-item]
        "candidate_jsonl_sha256": hashlib.sha256(candidates_bytes).hexdigest(),
        "reject_jsonl_sha256": hashlib.sha256(rejects_bytes).hexdigest(),
    }
    summary_bytes = _json_bytes(summary)
    _write_atomically(args.candidates, candidates_bytes)
    _write_atomically(args.rejects, rejects_bytes)
    _write_atomically(args.summary, summary_bytes)
    print(
        json.dumps(
            {
                "status": "ok",
                "candidate_count": summary["candidate_count"],
                "missing_grain_count": summary["missing_grain_count"],
                "candidate_jsonl_sha256": summary["candidate_jsonl_sha256"],
                "reject_jsonl_sha256": summary["reject_jsonl_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
