"""Evaluate exact free-form retrieval targets through the real analysis service."""

from __future__ import annotations

import argparse
from collections import Counter
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
from disclosure_db.judge_stress_v2 import is_git_ignored


ROOT = Path(__file__).resolve().parents[1]
_INDEPENDENT_HIDDEN_DIMENSIONS = {
    "profitability_financial_health",
    "contract_change",
    "financing_pressure",
    "correction_materiality",
    "governance_signal",
    "business_risk",
    "management_discussion",
}


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


def _evaluation_profile(
    rows: list[dict[str, object]],
    *,
    evaluation_scope: str = "development_public_agent_audited",
    gold_path: Path | None = None,
    repository_root: Path | None = None,
) -> dict[str, object]:
    gold_schema_versions = {str(row.get("schema_version", "1.0.0")) for row in rows}
    if len(gold_schema_versions) != 1:
        raise ValueError("mixed_gold_schema_versions")
    gold_schema_version = next(iter(gold_schema_versions))
    relevance_mode = gold_schema_version == "2.0.0"
    profile: dict[str, object] = {
        "schema_version": "freeform-retrieval-evaluation-v2" if relevance_mode else "1.0.0",
        "gold_schema_version": gold_schema_version,
        "evaluation_scope": "development_public_agent_audited",
        "release_eligible": False,
    }
    if evaluation_scope == "development_public_agent_audited":
        return profile
    if evaluation_scope != "independent_hidden":
        raise ValueError("unsupported_evaluation_scope")
    if not relevance_mode:
        raise ValueError("independent_hidden_schema_version")
    if gold_path is None or repository_root is None or not Path(gold_path).is_file():
        raise ValueError("independent_hidden_gold_missing")
    if not is_git_ignored(Path(repository_root), Path(gold_path)):
        raise ValueError("independent_hidden_gold_not_private")
    if len(rows) != 120:
        raise ValueError("independent_hidden_case_count")

    case_ids: set[str] = set()
    question_hashes: set[str] = set()
    issuers: set[str] = set()
    filings: set[str] = set()
    dimensions: Counter[str] = Counter()
    for index, row in enumerate(rows):
        label = f"independent_hidden[{index}]"
        case_id = row.get("case_id")
        question = row.get("question")
        question_sha256 = row.get("question_sha256")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError(f"{label}.case_id")
        if not isinstance(question, str) or not question.strip() or len(question) > 2_000:
            raise ValueError(f"{label}.question")
        actual_question_sha256 = hashlib.sha256(question.encode("utf-8")).hexdigest()
        if question_sha256 != actual_question_sha256 or question_sha256 in question_hashes:
            raise ValueError(f"{label}.question_sha256")
        case_ids.add(case_id)
        question_hashes.add(actual_question_sha256)

        issuer = row.get("issuer_corp_code")
        raw_filings = row.get("filing_ids")
        targets = row.get("target_evidence_ids")
        dimension = row.get("dimension_id")
        if not isinstance(issuer, str) or not issuer:
            raise ValueError(f"{label}.issuer_corp_code")
        if not isinstance(raw_filings, list) or not raw_filings or any(
            not isinstance(value, str) or not value for value in raw_filings
        ):
            raise ValueError(f"{label}.filing_ids")
        if not isinstance(targets, list) or not targets or any(
            not isinstance(value, str) or not value for value in targets
        ):
            raise ValueError(f"{label}.target_evidence_ids")
        if dimension not in _INDEPENDENT_HIDDEN_DIMENSIONS:
            raise ValueError(f"{label}.dimension_id")
        issuers.add(issuer)
        filings.update(raw_filings)
        dimensions[str(dimension)] += 1

        source_record_id = row.get("source_record_id")
        source_sha256 = row.get("source_sha256")
        review = row.get("review")
        if not isinstance(source_record_id, str) or not source_record_id:
            raise ValueError(f"{label}.source_record_id")
        if (
            not isinstance(source_sha256, str)
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
        ):
            raise ValueError(f"{label}.source_sha256")
        if not isinstance(review, dict) or review.get("status") != "agent_audited":
            raise ValueError(f"{label}.review")
        if review.get("provenance") != "independent_direct_corpus_annotation":
            raise ValueError(f"{label}.provenance")
        if review.get("target_selection") != "direct_evidence_ledger":
            raise ValueError(f"{label}.target_selection")
        if review.get("product_output_used") is not False:
            raise ValueError(f"{label}.product_output_used")
        if "selected_evidence_ids" in row or "retrieval_result" in row:
            raise ValueError(f"{label}.product_output_used")

    if set(dimensions) != _INDEPENDENT_HIDDEN_DIMENSIONS:
        raise ValueError("independent_hidden_dimension_coverage")
    if len(issuers) < 12:
        raise ValueError("independent_hidden_issuer_coverage")
    if len(filings) < 12:
        raise ValueError("independent_hidden_filing_coverage")
    return {
        **profile,
        "evaluation_scope": "independent_hidden",
        "release_eligible": True,
        "independence": {
            "case_count": len(rows),
            "issuer_count": len(issuers),
            "filing_count": len(filings),
            "dimension_counts": dict(sorted(dimensions.items())),
            "target_selection": "direct_evidence_ledger",
            "product_output_used": False,
            "visibility": "git_ignored_private_artifact",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--embedding-decision", type=Path, required=True)
    parser.add_argument(
        "--evaluation-scope",
        choices=("development_public_agent_audited", "independent_hidden"),
        default="development_public_agent_audited",
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
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
        missing_targets = list(score["missing_target_evidence_ids"])
        missing_relevance_sets = list(score.get("missing_relevance_set_ids", ()))
        if missing_targets or missing_relevance_sets:
            target_domains = case.get("target_domains", {})
            missing_domains = {
                str(target_domains.get(item, ""))
                for item in missing_targets
                if isinstance(target_domains, dict)
            }
            if missing_relevance_sets:
                relevance_domains = {
                    str(item.get("set_id")): str(item.get("domain", ""))
                    for item in case.get("relevance_sets", ())
                    if isinstance(item, dict)
                }
                missing_domains.update(
                    relevance_domains.get(str(set_id), "")
                    for set_id in missing_relevance_sets
                )
            missing_domains.discard("")
            residual_route = next(iter(missing_domains)) if len(missing_domains) == 1 else "mixed"
            score["route"] = residual_route
            score["exclusion_boundary"] = (
                "expanded_sparse_not_retrieved_at_20" if residual_route == "text"
                else "structured_route_target_not_retrieved"
            )
            if residual_route != "text":
                score["hypothesis"] = "structured_target_not_returned_by_audited_route"
            elif missing_targets and any(item not in indexed_targets for item in missing_targets):
                score["hypothesis"] = "target_absent_from_search_index"
            else:
                score["hypothesis"] = "target_outside_expanded_top20"
        scores.append(score)

    metrics = aggregate_freeform_scores(scores)
    profile = _evaluation_profile(
        rows,
        evaluation_scope=args.evaluation_scope,
        gold_path=args.gold,
        repository_root=args.repository_root,
    )
    summary: dict[str, object] = {
        **profile,
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
    decision = decide_embedding_pilot(
        metrics, release_eligible=bool(profile["release_eligible"])
    )
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
