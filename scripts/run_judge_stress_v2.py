from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from disclosure_db.judge_stress_execution import (
    FaultInjectingJudgeRuntime,
    JudgeRunOptions,
    run_suite,
    sanitize_result,
)
from disclosure_db.judge_stress_v2 import (
    build_development_cases,
    build_summary,
    load_audited_sources,
    load_contract,
    load_private_holdout,
    render_summary_html,
)


_RUN_METADATA_FIELDS = (
    "execution_mode",
    "probe_version",
    "runtime_release_eligible",
    "provider_eligible_root_count",
    "provider_probe_observation_count",
    "provider_call_count",
    "forbidden_provider_call_count",
    "deterministic_provider_call_count",
    "evaluator_error_count",
    "security_failure_count",
    "concurrency_error_count",
    "concurrency_p95_ms",
    "provider_p95_ms",
    "answerability_agreement",
    "numeric_exactness",
    "claim_citation_coverage",
    "metamorphic_consistency",
)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _jsonl_bytes(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
        for row in rows
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run provider-free local checks for Judge Stress V2."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--failures", type=Path, required=True)
    parser.add_argument("--json-summary", type=Path, required=True)
    parser.add_argument("--html-summary", type=Path, required=True)
    parser.add_argument("--private-holdout", type=Path)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, object]:
    args = _parse_args(argv)
    root = args.repository_root.resolve()
    contract = load_contract(root / "config" / "judge_stress_v2_contract.json")
    sources, _ = load_audited_sources(root, contract)
    cases = build_development_cases(sources, contract)
    private_available = args.private_holdout is not None
    if args.private_holdout is not None:
        cases.extend(
            load_private_holdout(
                args.private_holdout,
                repository_root=root,
                contract=contract,
            )
        )

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    execution = run_suite(
        cases,
        FaultInjectingJudgeRuntime,
        JudgeRunOptions(
            private_holdout_available=private_available,
            provider_available=False,
            concurrency=20,
        ),
        manifest=manifest,
    )
    sanitized = [sanitize_result(result) for result in execution.results]
    metadata = {
        field: execution.summary_metadata.get(field) for field in _RUN_METADATA_FIELDS
    }
    blockers = tuple(str(value) for value in execution.summary_metadata["blocked_reasons"])
    summary = build_summary(
        manifest,
        sanitized,
        blockers=blockers,
        run_metadata=metadata,
    )
    failures = [row for row in sanitized if row.get("passed") is not True]

    _write_atomic(args.results, _jsonl_bytes(sanitized))
    _write_atomic(args.failures, _jsonl_bytes(failures))
    _write_atomic(args.json_summary, _json_bytes(summary))
    _write_atomic(args.html_summary, render_summary_html(summary).encode("utf-8"))
    return summary


def main(argv: list[str] | None = None) -> int:
    try:
        summary = run(argv)
    except Exception:
        print("EVALUATOR_ERROR", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
