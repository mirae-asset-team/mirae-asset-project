"""Evaluate exact free-form retrieval targets through the real analysis service."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from disclosure_db.analysis_planner import plan_analysis
from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.evidence_service import EvidenceService
from disclosure_db.freeform_evaluation import (
    aggregate_freeform_scores,
    decide_embedding_pilot,
    evaluation_exclusion_reason,
    score_freeform_case,
    semantic_summary_sha256,
)


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


def _filing_issuers(database: Path, filing_ids: set[str]) -> dict[str, str]:
    if not filing_ids:
        return {}
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        marks = ",".join("?" for _ in filing_ids)
        return {
            str(filing_id): str(corp_code)
            for filing_id, corp_code in connection.execute(
                f"SELECT filing_id, issuer_corp_code FROM filing WHERE filing_id IN ({marks})",
                sorted(filing_ids),
            )
        }
    finally:
        connection.close()


def _indexed_evidence_ids(search_index: Path, evidence_ids: set[str]) -> set[str]:
    if not evidence_ids:
        return set()
    connection = sqlite3.connect(f"{search_index.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        marks = ",".join("?" for _ in evidence_ids)
        return {
            str(row[0]) for row in connection.execute(
                f"SELECT evidence_id FROM search_document WHERE evidence_id IN ({marks})",
                sorted(evidence_ids),
            )
        }
    finally:
        connection.close()


def _structured_evidence_ids(overlay: Path, evidence_ids: set[str]) -> set[str]:
    if not evidence_ids:
        return set()
    connection = sqlite3.connect(f"{overlay.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        marks = ",".join("?" for _ in evidence_ids)
        return {
            str(row[0])
            for table in ("financial_fact_evidence", "event_fact_evidence")
            for row in connection.execute(
                f"SELECT evidence_id FROM {table} WHERE evidence_id IN ({marks})",
                sorted(evidence_ids),
            )
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--embedding-decision", type=Path, required=True)
    args = parser.parse_args(argv)

    inputs = {path.resolve() for path in (args.database, args.overlay, args.search_index, args.attestation, args.gold)}
    outputs = {args.summary.resolve(), args.embedding_decision.resolve()}
    if len(outputs) != 2 or inputs.intersection(outputs):
        raise SystemExit("input_output_path_collision")
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    if not verify_fast_identity(args.database, attestation):
        raise SystemExit("database_attestation_mismatch")
    rows = [
        json.loads(line)
        for line in args.gold.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    service = EvidenceService(
        args.database,
        args.overlay,
        attestation=attestation,
        search_database=args.search_index,
    )
    scores: list[dict[str, object]] = []
    latency_samples: list[float] = []
    all_targets = {str(item) for row in rows for item in row["target_evidence_ids"]}
    indexed_targets = _indexed_evidence_ids(args.search_index, all_targets)
    serving_targets = indexed_targets | _structured_evidence_ids(args.overlay, all_targets)
    excluded_cases: list[dict[str, str]] = []
    for case in rows:
        plan = plan_analysis(
            str(case["question"]),
            company_candidates=[str(case["issuer_name"])],
            as_of=str(case["as_of"]) if case.get("as_of") else None,
        )
        started = time.perf_counter()
        result = service.search_analysis(plan, limit=20)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latency_samples.append(elapsed_ms)
        filing_ids = {str(ref.filing_id) for ref in result.evidence}
        issuer_by_filing = _filing_issuers(args.database, filing_ids)
        selected = [{
            "evidence_id": ref.evidence_id,
            "filing_id": ref.filing_id,
            "issuer_corp_code": issuer_by_filing.get(str(ref.filing_id)),
            "is_current": ref.is_current,
        } for ref in result.evidence]
        slot_diagnostics = result.retrieval_diagnostics.get("slots", {})
        diagnostics = list(slot_diagnostics.values()) if isinstance(slot_diagnostics, dict) else []
        query_count = sum(len(item.get("variant_ids", ())) for item in diagnostics if isinstance(item, dict))
        candidate_count = sum(int(item.get("candidate_count", 0)) for item in diagnostics if isinstance(item, dict))
        exclusion = evaluation_exclusion_reason(
            case, plan, query_count=query_count, serving_evidence_ids=serving_targets,
        )
        if exclusion is not None:
            excluded_cases.append({"case_id": str(case["case_id"]), "reason": exclusion})
            continue
        score = score_freeform_case(
            case,
            selected,
            slot_complete=result.complete,
            query_count=query_count,
            candidate_count=candidate_count,
            latency_ms=elapsed_ms,
        )
        if score["missing_target_evidence_ids"]:
            target_domains = case.get("target_domains", {})
            missing_domains = {
                str(target_domains.get(item, ""))
                for item in score["missing_target_evidence_ids"]
                if isinstance(target_domains, dict)
            }
            residual_route = next(iter(missing_domains)) if len(missing_domains) == 1 else "mixed"
            score["route"] = residual_route
            score["exclusion_boundary"] = (
                "expanded_sparse_not_retrieved_at_20" if residual_route == "text"
                else "structured_route_target_not_retrieved"
            )
            if residual_route != "text":
                score["hypothesis"] = "structured_target_not_returned_by_audited_route"
            elif any(item not in indexed_targets for item in score["missing_target_evidence_ids"]):
                score["hypothesis"] = "target_absent_from_search_index"
            else:
                score["hypothesis"] = "target_outside_expanded_top20"
        scores.append(score)

    metrics = aggregate_freeform_scores(scores)
    summary: dict[str, object] = {
        "schema_version": "1.0.0",
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_sha256": attestation.sha256,
        "overlay_sha256": _sha256(args.overlay),
        "search_index_sha256": _sha256(args.search_index),
        "gold_sha256": _sha256(args.gold),
        "metrics": metrics,
        "case_scores": sorted(scores, key=lambda row: str(row["case_id"])),
        "excluded_cases": sorted(excluded_cases, key=lambda row: row["case_id"]),
        "excluded_case_count": len(excluded_cases),
        "latency_samples_ms": latency_samples,
    }
    summary["semantic_sha256"] = semantic_summary_sha256(summary)
    decision = decide_embedding_pilot(metrics)
    decision["summary_semantic_sha256"] = summary["semantic_sha256"]
    _write_json(args.summary, summary)
    _write_json(args.embedding_decision, decision)
    print(json.dumps({
        "status": summary["status"],
        "case_count": metrics["case_count"],
        "target_recall_at_20": metrics["target_recall_at_20"],
        "semantic_sha256": summary["semantic_sha256"],
        "embedding_status": decision["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
