"""Run the deterministic/HCX agent against the approved Gold JSONL contract."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.agent_contracts import to_jsonable
from disclosure_db.agent_evaluation import evaluate_agent

STAGE_METRIC_KEYS = ("planner_p95_ms", "fact_lookup_p95_ms", "local_retrieval_p95_ms", "reranked_retrieval_p95_ms")
STAGE_METADATA_KEYS = ("reranker_provider_configured",)


def load_stage_metrics(path: Path | None) -> tuple[dict[str, float | bool], list[str]]:
    """Load finite stage p95 metrics without allowing malformed artifacts to pass."""
    if path is None:
        return {}, ["stage_metrics_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"stage_metrics_invalid_json:{exc}"]
    if not isinstance(payload, dict):
        return {}, ["stage_metrics_not_object"]
    unknown = sorted(set(payload) - (set(STAGE_METRIC_KEYS) | set(STAGE_METADATA_KEYS)))
    if unknown:
        return {}, ["stage_metrics_unknown_fields:" + ",".join(unknown)]
    values: dict[str, float | bool] = {}
    errors: list[str] = []
    provider_configured = payload.get("reranker_provider_configured", True)
    if not isinstance(provider_configured, bool):
        errors.append("reranker_provider_configured:must_be_boolean")
    else:
        values["reranker_provider_configured"] = provider_configured
    for key in STAGE_METRIC_KEYS:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{key}:missing_or_non_numeric")
            continue
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            errors.append(f"{key}:non_finite_or_non_numeric")
            continue
        if not math.isfinite(numeric) or numeric < 0:
            errors.append(f"{key}:non_finite_or_negative")
            continue
        values[key] = numeric
    return (values, errors) if not errors else ({}, errors)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--search-index", type=Path)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--contract", type=Path, default=Path("config/evaluation_contract.json"))
    parser.add_argument("--stage-metrics", type=Path)
    args = parser.parse_args()
    settings = AgentSettings(
        base_database=args.database,
        overlay_database=args.overlay,
        attestation_path=args.attestation,
        search_index=args.search_index,
        use_hcx=True,
    )
    agent = DisclosureAgent(settings=settings)
    stage_metrics, stage_metric_errors = load_stage_metrics(args.stage_metrics)
    output = evaluate_agent(
        agent,
        args.gold,
        limit=args.limit,
        contract_path=args.contract,
        stage_metrics=stage_metrics,
        stage_metrics_errors=stage_metric_errors,
    )
    output.update({
        "database": str(args.database),
        "overlay": str(args.overlay) if args.overlay else None,
        "gold": str(args.gold),
        "search_index": str(args.search_index) if args.search_index else None,
        "attestation": str(args.attestation) if args.attestation else None,
        "stage_metrics": str(args.stage_metrics) if args.stage_metrics else None,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(to_jsonable(output), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in output.items() if key != "evaluations"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
