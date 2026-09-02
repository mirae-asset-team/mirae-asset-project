"""Build the chunk-v1 BGE-M3 CPU FAISS pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_db.bge_m3_faiss import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_LENGTH,
    DEFAULT_MODEL,
    build_bge_m3_faiss,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed chunk-v1 JSONL with BGE-M3 and build a CPU FAISS IndexFlatIP.",
    )
    parser.add_argument("--chunks", type=Path, required=True, help="Path to chunk-v1 chunks.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Process at most this many input chunk records")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-revision")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_bge_m3_faiss(
        args.chunks,
        args.output_dir,
        limit=args.limit,
        batch_size=args.batch_size,
        resume=args.resume,
        max_length=args.max_length,
        model_name=args.model,
        model_revision=args.model_revision,
    )
    print(json.dumps({
        "status": manifest["status"],
        "completion_reason": manifest["completion_reason"],
        "processed_count": manifest["counts"]["processed_count"],
        "vector_count": manifest["index"]["vector_count"],
        "failure_count": manifest["counts"]["failure_count"],
        "truncated_count": manifest["counts"]["truncated_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
