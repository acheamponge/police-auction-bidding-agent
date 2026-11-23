"""Tests for how the command-line scripts turn .env, config.json and flags
into an agent. Run with: python3 -m unittest discover -s tests
"""

import argparse
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli import add_setting_args, build_agent

CONFIG = {
    "all_in_max_default_usd": 900.0,
    "tax_rate": 0.0825,
    "shipping_preference": "standard",
    "pickup_cost_usd": 120.0,
    "policy": {"tx_cap_usd": 2500.0, "session_budget_usd": 3000.0, "allow_reserve_lots": False},
    "live_bidding": {"configured": True},
}


class TestBuildAgent(unittest.TestCase):
    def build(self, argv, config=CONFIG, env_file=None, dry_run=True):
        parser = argparse.ArgumentParser()
        add_setting_args(parser)
        with tempfile.TemporaryDirectory() as project:
            if config is not None:
                with open(os.path.join(project, "config.json"), "w", encoding="utf-8") as fh:
                    json.dump(config, fh)
            if env_file is not None:
                with open(os.path.join(project, ".env"), "w", encoding="utf-8") as fh:
                    fh.write(env_file)
            with mock.patch.dict(os.environ, {}, clear=True):
                agent, settings = build_agent(parser.parse_args(argv), project, dry_run=dry_run)
            return agent, settings, project

    def test_config_json_reaches_the_agent(self):
        agent, settings, project = self.build([])
        self.assertEqual(agent.tax_rate, 0.0825)
        self.assertEqual(agent.shipping_preference, "standard")
        self.assertEqual(agent.pickup_cost_usd, 120.0)
        self.assertEqual(agent.policy_state.config.tx_cap_usd, 2500.0)
        self.assertEqual(agent.policy_state.config.session_budget_usd, 3000.0)
        self.assertFalse(agent.policy_state.config.allow_reserve_lots)
        self.assertTrue(agent.policy_state.config.live_bidding_configured)
        self.assertTrue(agent.policy_state.config.dry_run)
        self.assertEqual(settings.all_in_max_default_usd, 900.0)
        self.assertEqual(agent.ledger.path, os.path.join(project, "cost_ledger.jsonl"))

    def test_flags_win_over_config_json(self):
        agent, _, _ = self.build(["--tax-rate", "0.10", "--shipping", "expedited",
                                  "--pickup-cost", "0", "--allow-reserve",
                                  "--ledger", "/dev/null"], dry_run=False)
        self.assertEqual(agent.tax_rate, 0.10)
        self.assertEqual(agent.shipping_preference, "expedited")
        self.assertEqual(agent.pickup_cost_usd, 0.0)
        self.assertTrue(agent.policy_state.config.allow_reserve_lots)
        self.assertFalse(agent.policy_state.config.dry_run)
        self.assertEqual(agent.ledger.path, "/dev/null")

    def test_no_config_means_the_careful_defaults(self):
        agent, settings, _ = self.build([], config=None)
        self.assertIsNone(agent.tax_rate)
        self.assertIsNone(agent.pickup_cost_usd)
        self.assertEqual(agent.shipping_preference, "cheapest")
        self.assertIsNone(settings.all_in_max_default_usd)

    def test_key_in_dot_env_switches_the_agent_to_the_live_model(self):
        without, _, _ = self.build([])
        with_key, _, _ = self.build([], env_file="MODEL_API_KEY=abc123\n")
        self.assertFalse(without.ember.live)
        self.assertTrue(with_key.ember.live)


if __name__ == "__main__":
    unittest.main()
