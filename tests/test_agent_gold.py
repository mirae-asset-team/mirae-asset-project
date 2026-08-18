from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_agent_gold import load_predicate_config


class AgentGoldTests(unittest.TestCase):
    def test_predicate_config_has_only_explicit_safe_predicates(self) -> None:
        config = load_predicate_config(Path("config/agent_gold_predicates.json"))
        self.assertEqual(config["version"], "0.1.0")
        self.assertGreaterEqual(
            {item["id"] for item in config["predicates"]},
            {"contract_amount", "counterparty", "issued_shares"},
        )
        self.assertTrue(all(item["answer_kind"] in {"numeric", "text"} for item in config["predicates"]))


if __name__ == "__main__":
    unittest.main()
