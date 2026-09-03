from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from disclosure_db.judge_stress_v2 import (
    assemble_judge_suite,
    build_development_cases,
    build_manifest,
    build_summary,
    load_audited_sources,
    load_contract,
    load_private_holdout,
    render_summary_html,
    write_raw_cases,
)


def _file_identity(
    path: Path, case_count: int, repository_root: Path
) -> dict[str, object]:
    return {
        "path": path.relative_to(repository_root).as_posix(),
        "case_count": case_count,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> None:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build 480 development cases and, with private input, validate the 600-case suite."
    )
    parser.add_argument("--repository-root", type=Path, default=default_root)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--private-holdout", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json-summary", type=Path)
    parser.add_argument("--html-summary", type=Path)
    args = parser.parse_args(argv)

    repository_root = args.repository_root.resolve()
    contract_path = args.contract or repository_root / "config" / "judge_stress_v2_contract.json"
    contract = load_contract(contract_path)
    artifact_root = args.artifact_root or repository_root / str(contract["raw_artifact_root"])
    manifest_path = args.manifest or repository_root / str(contract["tracked_manifest"])
    json_summary_path = args.json_summary or repository_root / str(contract["tracked_json_summary"])
    html_summary_path = args.html_summary or repository_root / str(contract["tracked_html_summary"])

    sources, source_metadata = load_audited_sources(repository_root, contract)
    development = build_development_cases(sources, contract)
    development_artifact = write_raw_cases(
        development,
        "development",
        artifact_root,
        repository_root=repository_root,
    )
    if args.private_holdout is None:
        print(
            "BLOCKED_PRIVATE_HOLDOUT: supply --private-holdout with a git-ignored evaluator artifact",
            file=sys.stderr,
        )
        raise SystemExit(2)

    holdout = load_private_holdout(
        args.private_holdout,
        repository_root=repository_root,
        contract=contract,
    )
    suite = assemble_judge_suite(development, holdout, contract)
    holdout_artifact = write_raw_cases(
        holdout,
        "holdout",
        artifact_root,
        repository_root=repository_root,
    )
    legacy_regressions = {
        "agent_stress_300": _file_identity(
            repository_root / "data" / "derived" / "agent_stress_300_manifest.json",
            300,
            repository_root,
        ),
        "financial_release_856": _file_identity(
            repository_root / "data" / "derived" / "financial_release_evaluation.json",
            856,
            repository_root,
        ),
    }
    manifest = build_manifest(
        suite,
        contract,
        source_metadata,
        legacy_regressions=legacy_regressions,
    )
    manifest["raw_artifacts"] = {
        "development": development_artifact,
        "holdout": holdout_artifact,
    }
    summary = build_summary(manifest, [])

    _write_json(manifest_path, manifest)
    _write_json(json_summary_path, summary)
    html_summary_path.parent.mkdir(parents=True, exist_ok=True)
    html_summary_path.write_text(render_summary_html(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
