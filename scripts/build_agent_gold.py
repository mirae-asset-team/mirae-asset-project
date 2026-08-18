"""Build deterministic, evidence-backed agent-audited Gold candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_predicate_config(path: Path) -> dict[str, Any]:
    """Load and validate the explicit predicate allowlist."""
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != "0.1.0":
        raise ValueError("agent Gold predicate config must declare version 0.1.0")
    predicates = config.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        raise ValueError("agent Gold predicate config requires predicates")
    seen_ids: set[str] = set()
    seen_values: set[str] = set()
    for item in predicates:
        if not isinstance(item, dict):
            raise ValueError("predicate entries must be objects")
        required = {"id", "question_type", "answer_kind", "allowed_fact_types", "predicate_values", "required_evidence_fields"}
        if required - set(item):
            raise ValueError(f"predicate missing fields: {sorted(required - set(item))}")
        predicate_id = str(item["id"])
        values = item["predicate_values"]
        if not predicate_id or predicate_id in seen_ids or not isinstance(values, list) or not values:
            raise ValueError("predicate ids must be unique and predicate_values must be non-empty")
        if any(not isinstance(value, str) or not value or value == "*" for value in values):
            raise ValueError("predicate_values must contain explicit non-empty strings")
        if not isinstance(item["allowed_fact_types"], list) or not item["allowed_fact_types"]:
            raise ValueError(f"predicate {predicate_id} requires allowed_fact_types")
        if not isinstance(item["required_evidence_fields"], list):
            raise ValueError(f"predicate {predicate_id} requires required_evidence_fields")
        seen_ids.add(predicate_id)
        seen_values.update(values)
    templates = config.get("templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("agent Gold predicate config requires templates")
    if any(not isinstance(item, dict) or not item.get("id") for item in templates):
        raise ValueError("templates must have ids")
    return config


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predicate-config", type=Path, default=Path("config/agent_gold_predicates.json"))
    args = parser.parse_args()
    loaded = load_predicate_config(args.predicate_config)
    print(json.dumps({"version": loaded["version"], "predicate_count": len(loaded["predicates"])}, ensure_ascii=False))
