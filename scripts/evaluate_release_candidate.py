"""Recompute the unified release hard gate from content-free reports."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping

from disclosure_db.release_gate import (
    evaluate_release_gate,
    load_release_contract,
    write_release_report,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a release candidate.")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--financial", type=Path)
    parser.add_argument("--judge", type=Path)
    parser.add_argument("--retrieval", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument(
        "--expected-identity",
        type=Path,
        help="External trusted commit/image/data identity JSON for this deployment",
    )
    parser.add_argument("--json-summary", type=Path)
    parser.add_argument("--html-summary", type=Path)
    parser.add_argument("--now")
    return parser.parse_args(argv)


def _read_mapping(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, Mapping) else {}


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now must include a timezone")
    return parsed.astimezone(UTC)


def _actual_head(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value if value else None


def run(argv: list[str] | None = None):
    args = _parse_args(argv)
    root = args.repository_root.resolve()
    contract_path = args.contract or root / "config/release_gate_contract.json"
    financial_path = args.financial or root / "data/derived/financial_release_evaluation.json"
    judge_path = args.judge or root / "data/derived/judge_stress_v2_summary.json"
    retrieval_path = args.retrieval or root / "data/derived/freeform_retrieval_summary.json"
    deployment_path = args.deployment or root / "data/derived/staging_evaluation.json"
    json_path = args.json_summary or root / "data/derived/release_gate_summary.json"
    html_path = args.html_summary or root / "data/derived/release_gate_summary.html"
    result = evaluate_release_gate(
        _read_mapping(financial_path),
        _read_mapping(judge_path),
        _read_mapping(retrieval_path),
        _read_mapping(deployment_path),
        contract=load_release_contract(contract_path),
        expected_identity=(
            _read_mapping(args.expected_identity)
            if args.expected_identity is not None
            else None
        ),
        actual_commit=_actual_head(root),
        now=_timestamp(args.now),
    )
    write_release_report(result, json_path, html_path)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
    except Exception:
        print("RELEASE_EVALUATOR_ERROR", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "release_state": result.release_state,
                "hard_gate_passed": result.hard_gate_passed,
                "hard_gate_reasons": list(result.hard_gate_reasons),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.hard_gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
