"""Run the deterministic/HCX agent against the approved Gold JSONL contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.agent_contracts import to_jsonable
from disclosure_db.agent_evaluation import evaluate_agent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    agent = DisclosureAgent(AgentSettings(args.database, args.overlay))
    output = evaluate_agent(agent, args.gold, limit=args.limit)
    output.update({
        "database": str(args.database),
        "overlay": str(args.overlay) if args.overlay else None,
        "gold": str(args.gold),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(to_jsonable(output), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in output.items() if key != "evaluations"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
