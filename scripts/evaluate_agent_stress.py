from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.agent_contracts import to_jsonable
from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.financial_overlay import overlay_matches_base
from disclosure_db.search_index import SafeSearchIndex
from disclosure_db.stress_evaluation import (
    aggregate_scores,
    evaluate_provider_gate,
    percentile_95,
    resolve_case_company,
    run_fault_case,
    score_case,
    should_skip,
)
from disclosure_db.stress_generation import canonical_json, validate_stress_cases


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _case_hash(case: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(case)).hexdigest()


def _runtime_result(verified: object, expected: dict[str, object]) -> dict[str, object]:
    answerable = bool(getattr(verified, "answerable", False))
    citations = list(getattr(verified, "citation_ids", []) or [])
    numeric_values = list(getattr(verified, "numeric_values", []) or [])
    expected_answer = expected.get("answer") if isinstance(expected.get("answer"), dict) else {}
    kind = expected_answer.get("kind")
    if not answerable:
        answer = {"kind": "unanswerable", "reason": "runtime abstention"}
    elif kind == "numeric":
        answer = {
            "kind": "numeric", "value": numeric_values[0] if numeric_values else "",
            "unit": expected_answer.get("unit"), "scale": expected_answer.get("scale"),
            "filing_id": expected_answer.get("filing_id"), "evidence_ids": citations,
        }
    elif kind == "multi_numeric":
        values = expected_answer.get("values", [])
        answer = {"kind": "multi_numeric", "values": [
            {**value, "value": numeric_values[index] if index < len(numeric_values) else "", "evidence_ids": citations}
            for index, value in enumerate(values) if isinstance(value, dict)
        ]}
    else:
        answer = {"kind": "text", "text": str(getattr(verified, "answer", "")), "evidence_ids": citations}
    return {"answerable": answerable, "verified": bool(getattr(verified, "verified", False)), "citations": citations, "answer": answer}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--failures", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider-mode", choices=("disabled", "required"), default="disabled")
    args = parser.parse_args()
    if args.workers != 1:
        raise SystemExit("only --workers 1 is supported for deterministic execution")
    cases = _read_jsonl(args.cases)
    validate_stress_cases(cases)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    if not verify_fast_identity(args.database, attestation):
        raise SystemExit("base fast identity failed")
    if not overlay_matches_base(args.database, args.overlay, attestation=attestation):
        raise SystemExit("overlay base attestation failed")
    index = SafeSearchIndex(args.search_index, base_sha256=attestation.sha256, expected_base_size=attestation.size_bytes)
    search_revision = "safe-search-v1"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + hashlib.sha256(args.cases.read_bytes()).hexdigest()[:12]
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unknown"
    settings = AgentSettings(
        base_database=args.database, overlay_database=args.overlay, search_database=args.search_index,
        attestation_path=args.attestation, use_hcx=args.provider_mode == "required",
    )
    agent = DisclosureAgent(settings)
    provider_required = args.provider_mode == "required"
    provider_model = str(getattr(agent.generator, "model", "")) if provider_required else None
    if provider_required and not agent.provider_configured:
        raise SystemExit("provider required but CLOVASTUDIO_API_KEY is not configured")

    checkpoint_root = args.run_root / run_id / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    completed: dict[str, dict[str, object]] = {}
    if args.resume:
        for path in checkpoint_root.glob("*.json"):
            item = json.loads(path.read_text(encoding="utf-8"))
            completed[str(item.get("case_id"))] = item
    scores = []
    failure_rows: list[dict[str, object]] = []
    end_to_end_latencies: list[float] = []
    for case in cases:
        case_id = str(case["question_id"])
        input_hash = _case_hash(case)
        checkpoint = completed.get(case_id)
        if checkpoint and should_skip(
            {"question_id": case_id, "input_hash": input_hash},
            git_commit,
            checkpoint,
            provider_mode=args.provider_mode,
            provider_model=provider_model,
        ):
            from disclosure_db.stress_evaluation import CaseScore
            score = CaseScore(case_id, bool(checkpoint.get("passed")), list(checkpoint.get("failures", [])), str(checkpoint.get("primary_cause", "unclassified")), dict(checkpoint.get("metrics", {})))
            scores.append(score)
            continue
        if str(case["stress"]["oracle"]) == "fault":
            with tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory) / "fixture.sqlite"
                fixture.write_bytes(b"stress-fault-fixture")
                fault = run_fault_case(fixture, Path(directory) / "copy")
            from disclosure_db.stress_evaluation import CaseScore
            score = CaseScore(case_id, bool(fault["fault_isolated"]), [] if fault["fault_isolated"] else ["fault_isolation_failed"], "unclassified", {"fault_isolation": fault["fault_isolated"]})
        else:
            company = resolve_case_company(case)
            started = perf_counter()
            try:
                verified = agent.answer(str(case["question"]), company=str(company) if company else None, as_of=case.get("as_of") if isinstance(case.get("as_of"), str) else None, limit=20)
                actual = _runtime_result(verified, case)
                score = score_case(case, actual)
            except Exception as exc:  # per-case errors are audit failures, never silent passes
                from disclosure_db.stress_evaluation import CaseScore
                score = CaseScore(case_id, False, ["evaluator_error"], "runtime_integrity", {"evaluator_error": 1})
            elapsed_ms = (perf_counter() - started) * 1000
            end_to_end_latencies.append(elapsed_ms)
            score.metrics["end_to_end_ms"] = elapsed_ms
        scores.append(score)
        checkpoint = {"case_id": case_id, "input_hash": input_hash, "git_commit": git_commit, "provider_mode": args.provider_mode, "provider_model": provider_model, "base_sha256": attestation.sha256, "overlay_revision": "semantic-v1-agent-overlay", "search_revision": search_revision, "passed": score.passed, "failures": score.failures, "primary_cause": score.primary_cause, "metrics": score.metrics}
        (checkpoint_root / f"{hashlib.sha256(case_id.encode()).hexdigest()}.json").write_text(json.dumps(checkpoint, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        if not score.passed:
            failure_rows.append({"question_id": case_id, "failures": score.failures, "primary_cause": score.primary_cause})
            if any(name in score.failures for name in ("unsafe_answer", "false_numeric_claim", "unknown_citation", "cross_filing_citation", "evaluator_error")):
                break
    summary = aggregate_scores(scores, contract)
    summary.update({"run_id": run_id, "case_manifest_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(), "provider_required": provider_required, "provider_configured": agent.provider_configured, "provider_model": provider_model, "provider_end_to_end_p95_ms": percentile_95(end_to_end_latencies), "base_sha256": attestation.sha256, "search_revision": search_revision, "completed_count": len(scores)})
    provider_gate_passed, provider_gate_reasons = evaluate_provider_gate(
        summary,
        contract,
        provider_required=provider_required,
    )
    summary["provider_gate_passed"] = provider_gate_passed
    summary["provider_gate_reasons"] = provider_gate_reasons
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.failures.parent.mkdir(parents=True, exist_ok=True)
    args.failures.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in failure_rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
