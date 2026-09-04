"""Evaluate every searchable financial target before release promotion."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.financial_release_evaluation import (
    build_release_cases,
    evaluate_release_answer,
    measure_concurrent_requests,
    release_hard_gate,
)


KST = timezone(timedelta(hours=9))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    attestation = json.loads(args.attestation.read_text(encoding="utf-8"))
    facts = _read_jsonl(args.facts)
    agent = DisclosureAgent(AgentSettings(
        base_database=args.database,
        overlay_database=args.overlay,
        search_index=args.search_index,
        attestation_path=args.attestation,
        use_hcx=False,
    ))
    search_targets = sorted(agent.evidence_service.company_candidates())
    cases = build_release_cases(search_targets=search_targets, manifest=manifest, facts=facts)

    results: list[dict[str, object]] = []
    latencies: list[float] = []
    for case in cases:
        began = perf_counter()
        answer = agent.answer(str(case["question"]), company=str(case["company"]), limit=20)
        elapsed_ms = round((perf_counter() - began) * 1_000, 3)
        result = evaluate_release_answer(case, answer)
        result.update({
            "elapsed_ms": elapsed_ms,
            "correction_case": bool(case.get("correction_case")),
            "finance_revenue_alias_case": bool(case.get("finance_revenue_alias_case")),
        })
        results.append(result)
        latencies.append(elapsed_ms)

    aggregate_results: list[dict[str, object]] = []
    for operation, question in (
        ("count_above", "영업이익 10억 넘는 기업은 몇 개야?"),
        ("list_above", "영업이익 10억 넘는 기업 목록"),
        ("rank", "영업이익 상위 5개 기업 순위"),
    ):
        aggregate_answer = agent.answer(question, limit=20)
        aggregate_result = evaluate_release_answer(
            {"case_id": f"aggregate:{operation}:incomplete_operating_income", "kind": "aggregate_refusal", "expected_facts": []},
            aggregate_answer,
        )
        if "corpus_wide_financial_coverage_incomplete" not in aggregate_answer.reason_codes:
            aggregate_result["passed"] = False
            aggregate_result["failures"] = [*aggregate_result["failures"], "coverage_refusal_reason_missing"]
        aggregate_results.append(aggregate_result)
        results.append(aggregate_result)

    # Warm the exact path, then start one twenty-request wave against SQLite.
    agent.answer("삼성전자의 최근 사업보고서 기준 매출액을 알려줘", company="삼성전자", limit=20)
    concurrency = measure_concurrent_requests(
        lambda: agent.answer(
            "삼성전자의 최근 사업보고서 기준 매출액을 알려줘",
            company="삼성전자",
            limit=20,
        )
    )
    hard_gate_passed, hard_gate_reasons = release_hard_gate(results, concurrency=concurrency)
    failures = [item for item in results if not bool(item["passed"])]
    kind_counts = Counter(str(item.get("kind", "unknown")) for item in results)
    kind_passes = Counter(str(item.get("kind", "unknown")) for item in results if bool(item["passed"]))
    ordered_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(ordered_latencies)) - 1)
    now = datetime.now(UTC)
    samsung = next((item for item in results if item["case_id"] == "latest:삼성전자:revenue"), None)

    report = {
        "schema_version": "financial-release-evaluation-v1",
        "finished_at_utc": now.isoformat(),
        "finished_at_kst": now.astimezone(KST).isoformat(),
        "inputs": {
            "base_sha256": str(
                (attestation.get("database") or {}).get("uncompressed_sha256", "")
                if isinstance(attestation.get("database"), dict)
                else ""
            ),
            "attestation_manifest_sha256": _sha256(args.attestation),
            "overlay_sha256": _sha256(args.overlay),
            "search_index_sha256": _sha256(args.search_index),
            "universe_manifest_sha256": _sha256(args.manifest),
            "financial_seed_sha256": _sha256(args.facts),
        },
        "population": {
            "search_target_count": len(search_targets),
            "legal_issuer_count": int(manifest.get("source_company_count", 0)),
            "selected_filing_count": int(manifest.get("company_count", 0)),
            "validated_fact_count": len(facts),
        },
        "cases": {
            "total": len(results),
            "passed": len(results) - len(failures),
            "failed": len(failures),
            "by_kind": {kind: {"total": count, "passed": kind_passes[kind]} for kind, count in sorted(kind_counts.items())},
            "structured_accuracy": (len(results) - len(failures)) / len(results),
            "false_numeric_claim_count": sum("false_numeric_claim" in item.get("failures", []) for item in results),
            "ungrounded_verified_answer_count": sum("citation_mismatch" in item.get("failures", []) for item in results),
            "p95_ms": ordered_latencies[p95_index] if ordered_latencies else None,
            "failures": failures,
        },
        "special_cases": {
            "samsung_latest_revenue_passed": bool(samsung and samsung["passed"]),
            "correction_case_count": sum(bool(item.get("correction_case")) for item in results),
            "finance_revenue_alias_case_count": sum(bool(item.get("finance_revenue_alias_case")) for item in results),
            "separate_fallback_observed": any(str(fact.get("scope")) == "separate" for fact in facts),
            "incomplete_aggregate_refusal_passed": all(bool(item["passed"]) for item in aggregate_results),
        },
        "concurrency": concurrency,
        "provider": {"required_for_structured_queries": False, "configured": agent.provider_configured},
        "hard_gate_passed": hard_gate_passed,
        "hard_gate_reasons": hard_gate_reasons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))
    if not hard_gate_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
