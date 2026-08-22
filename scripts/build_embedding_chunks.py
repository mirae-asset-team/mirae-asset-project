"""Build deterministic embedding chunk-v1 artifacts from an immutable corpus DB."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from disclosure_db.embedding_chunks import (
    DEFAULT_CONFIG_PATH,
    build_embedding_chunks,
    load_chunk_config,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--target-tokens", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--overlap-tokens", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--checkpoint-every-groups", type=int)
    parser.add_argument("--max-groups", type=int, help="Stop cleanly after N groups; continue with --resume")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.resume and args.overwrite:
        raise SystemExit("--resume and --overwrite are mutually exclusive")
    config = load_chunk_config(args.config)
    overrides = {
        "target_tokens": args.target_tokens,
        "max_tokens": args.max_tokens,
        "overlap_tokens": args.overlap_tokens,
        "batch_size": args.batch_size,
        "checkpoint_every_groups": args.checkpoint_every_groups,
    }
    config = replace(config, **{key: value for key, value in overrides.items() if value is not None})
    config.validate()
    result = build_embedding_chunks(
        args.database,
        args.output_dir,
        config=config,
        resume=args.resume,
        overwrite=args.overwrite,
        max_groups=args.max_groups,
    )
    print(json.dumps({
        "status": result.status,
        "output_directory": result.output_directory,
        "manifest_path": result.manifest_path,
        "checkpoint_path": result.checkpoint_path,
        "summary": dict(result.summary),
        "config": asdict(config),
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
