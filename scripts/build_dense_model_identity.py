from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from disclosure_db.dense_runtime import (
    EXPECTED_MODEL,
    EXPECTED_MODEL_REVISION,
    MODEL_IDENTITY_FILENAME,
    build_model_identity,
)
from huggingface_hub import snapshot_download


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a non-secret, file-hash-bound identity for a staged Dense model directory."
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    model_path = args.model_path.resolve(strict=True)
    output = args.output.resolve()
    if output.parent != model_path or output.name != MODEL_IDENTITY_FILENAME:
        raise SystemExit(f"--output must be --model-path/{MODEL_IDENTITY_FILENAME}")

    reference_model_path = Path(snapshot_download(
        repo_id=EXPECTED_MODEL,
        revision=EXPECTED_MODEL_REVISION,
        local_files_only=True,
    ))
    identity = build_model_identity(
        model_path,
        reference_model_path=reference_model_path,
    )
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
