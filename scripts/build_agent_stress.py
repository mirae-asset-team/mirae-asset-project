from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from disclosure_db.stress_generation import (
    build_stress_cases,
    canonical_json,
    source_sha256,
    validate_stress_cases,
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_no} is not an object")
        rows.append(value)
    return rows


def _write_jsonl_atomic(path: Path, cases: list[dict[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(canonical_json(case) + b"\n" for case in cases)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if args.count != int(contract["case_count"]):
        raise SystemExit("--count must match contract case_count")
    if args.seed != int(contract["seed"]):
        raise SystemExit("--seed must match contract seed")
    records = _read_jsonl(args.gold)
    cases = build_stress_cases(records, contract, seed=args.seed)
    validate_stress_cases(cases)
    output_sha = _write_jsonl_atomic(args.output, cases)
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unknown"
    manifest = {
        "schema_version": "0.1.0",
        "generator": "deterministic_stress_v1",
        "seed": args.seed,
        "git_commit": git_commit,
        "input_sha256": hashlib.sha256(args.gold.read_bytes()).hexdigest(),
        "output_sha256": output_sha,
        "case_count": len(cases),
        "question_count": len({str(case["question_id"]) for case in cases}),
        "base_count": len({str(case["stress"]["base_id"]) for case in cases}),
        "group_count": len({str(case["stress"]["group_id"]) for case in cases}),
        "filing_count": len({str(item["filing_id"]) for case in cases for item in case.get("evidence", []) if isinstance(item, dict)}),
        "category_counts": dict(Counter(str(case["stress"]["category"]) for case in cases)),
        "trust_tier_counts": dict(Counter(str(case["stress"]["trust_tier"]) for case in cases)),
        "built_at": datetime.now(UTC).isoformat(),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
