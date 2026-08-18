"""Measure the four serving stages used by the Task 10 acceptance contract.

The benchmark deliberately uses the deterministic runtime path and a
provider-disabled reranker.  It writes exactly the four fields consumed by
``scripts/evaluate_agent.py --stage-metrics``; the stage boundaries and sample
counts are printed for the development log.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable

from disclosure_db.agent_contracts import EvidenceRef
from disclosure_db.attestation import load_distribution_attestation
from disclosure_db.evidence_service import EvidenceService
from disclosure_db.query_planner import plan_query
from disclosure_db.reranker import ClovaReranker
from disclosure_db.search_index import SafeSearchIndex


STAGES = ("planner_p95_ms", "fact_lookup_p95_ms", "local_retrieval_p95_ms", "reranked_retrieval_p95_ms")


def _p95(values: list[float]) -> float:
    if not values:
        raise ValueError("benchmark produced no samples")
    ordered = sorted(values)
    index = min(max(math.ceil(len(ordered) * 0.95), 1) - 1, len(ordered) - 1)
    return round(ordered[index], 2)


def _timed(samples: list[float], fn: Callable[[], object]) -> object:
    started = time.perf_counter()
    result = fn()
    samples.append((time.perf_counter() - started) * 1000)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    attestation = load_distribution_attestation(args.attestation, database=args.database)
    service = EvidenceService(
        args.database,
        args.overlay,
        attestation=attestation,
        search_database=args.search_index,
        reranker=ClovaReranker(api_key=None),
    )
    index = SafeSearchIndex(
        args.search_index,
        base_sha256=attestation.sha256,
        expected_base_size=attestation.size_bytes,
    )
    reranker = ClovaReranker(api_key=None)
    records = [json.loads(line) for line in args.gold.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = records[: max(1, args.limit)]
    samples: dict[str, list[float]] = {stage: [] for stage in STAGES}

    for record in records:
        company = record.get("company_resolution") or {}
        company_hint = company.get("query_name") or company.get("issuer_name")
        question = str(record["question"])
        plan = _timed(
            samples["planner_p95_ms"],
            lambda: plan_query(question, company_hint=str(company_hint) if company_hint else None),
        )
        # This measures the complete structured lookup/hydration boundary used
        # by the serving path, including its attested overlay checks.
        _timed(samples["fact_lookup_p95_ms"], lambda plan=plan: service.search(plan, limit=args.limit))
        rows = _timed(
            samples["local_retrieval_p95_ms"],
            lambda plan=plan: index.search(
                plan.question,
                company=plan.company,
                as_of=plan.as_of,
                limit=args.limit,
                correction_policy=plan.correction_policy,
            ),
        )
        refs = [
            EvidenceRef(
                evidence_id=str(row["evidence_id"]),
                filing_id=str(row["filing_id"]),
                source_id=str(row["source_id"]),
                text=str(row.get("text_normalized") or ""),
            )
            for row in rows
        ]
        _timed(samples["reranked_retrieval_p95_ms"], lambda: reranker.rerank(question, refs, limit=min(8, args.limit)))

    result = {stage: _p95(samples[stage]) for stage in STAGES}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"samples": {key: len(value) for key, value in samples.items()}, "metrics": result}, indent=2))


if __name__ == "__main__":
    main()
