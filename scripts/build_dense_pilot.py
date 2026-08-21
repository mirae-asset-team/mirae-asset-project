"""Materialize the bounded dense-pilot decision without fabricating vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from disclosure_db.freeform_evaluation import canonical_sha256


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--retrieval-summary", type=Path, required=True)
    parser.add_argument("--embedding-decision", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)

    retrieval = json.loads(args.retrieval_summary.read_text(encoding="utf-8"))
    decision = json.loads(args.embedding_decision.read_text(encoding="utf-8"))
    if decision.get("status") != "ELIGIBLE_PILOT":
        raise SystemExit("dense_pilot_not_eligible")
    residuals = [
        miss for miss in retrieval["metrics"]["residual_misses"]
        if miss.get("route") == "text"
    ]
    residual_identity = [{
        "case_id": miss["case_id"],
        "target_evidence_ids": miss["target_evidence_ids"],
    } for miss in residuals]
    provider_available = bool(os.getenv("CLOVASTUDIO_API_KEY"))
    if provider_available:
        raise SystemExit("provider_execution_requires_separate_approved_cost_run")

    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "status": "BLOCKED_EXTERNAL",
        "reason": "provider_access_unavailable",
        "provider": "naver_clova_studio",
        "model": "bge-m3",
        "dimension": 1024,
        "fragment_cap": 20000,
        "fragment_count": 0,
        "cost": {"currency": "KRW", "estimated": None, "actual": None},
        "gold_sha256": _sha256(args.gold),
        "retrieval_summary_sha256": _sha256(args.retrieval_summary),
        "residual_input_sha256": canonical_sha256(residual_identity),
        "vector_records_sha256": None,
    }
    pilot_summary: dict[str, object] = {
        "schema_version": "1.0.0",
        "status": "BLOCKED_EXTERNAL",
        "reason": "provider_access_unavailable",
        "residual_text_case_count": len(residuals),
        "residual_text_target_count": sum(len(item["target_evidence_ids"]) for item in residuals),
        "sparse_recall_at_20": retrieval["metrics"]["target_recall_at_20"],
        "dense_recall_at_20": None,
        "measured_gain": None,
        "wrong_issuer_count": None,
        "wrong_version_count": None,
        "p95_ms": None,
        "adopted": False,
        "manifest_semantic_sha256": canonical_sha256(manifest),
    }
    decision.update({
        "status": "BLOCKED_EXTERNAL",
        "reason": "provider_access_unavailable",
        "pilot_model": "bge-m3",
        "pilot_dimension": 1024,
        "pilot_fragment_cap": 20000,
        "pilot_manifest_semantic_sha256": canonical_sha256(manifest),
    })
    _write_json(args.manifest, manifest)
    _write_json(args.summary, pilot_summary)
    _write_json(args.embedding_decision, decision)
    print(json.dumps({
        "status": "BLOCKED_EXTERNAL",
        "residual_text_case_count": len(residuals),
        "model": "bge-m3",
        "dimension": 1024,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
