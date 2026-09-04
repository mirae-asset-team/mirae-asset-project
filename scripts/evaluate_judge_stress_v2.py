from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_db.judge_stress_v2 import build_summary, render_summary_html


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Summarize Judge Stress V2 evaluator result metadata."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--json-summary", type=Path, required=True)
    parser.add_argument("--html-summary", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    summary = build_summary(manifest, _read_jsonl(args.results))
    args.json_summary.parent.mkdir(parents=True, exist_ok=True)
    args.json_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.html_summary.parent.mkdir(parents=True, exist_ok=True)
    args.html_summary.write_text(render_summary_html(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
