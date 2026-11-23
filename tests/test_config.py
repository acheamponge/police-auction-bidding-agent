"""Tests for .env and config.json loading — run with: python3 -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_env, load_settings


class TestLoadEnv(unittest.TestCase):
    def load(self, content, existing=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            with mock.patch.dict(os.environ, existing or {}, clear=True):
                load_env(path)
                return dict(os.environ)

    def test_reads_plain_quoted_and_exported_values(self):
        env = self.load("# the key\nMODEL_API_KEY=abc123\n\n"
                        "MODEL_API_BASE = \"https://proxy.invalid/v1\"\n"
                        "export OTHER='x=y'\nnot a pair\n")
        self.assertEqual(env, {"MODEL_API_KEY": "abc123",
                               "MODEL_API_BASE": "https://proxy.invalid/v1",
                               "OTHER": "x=y"})

    def test_a_variable_already_set_wins_over_the_file(self):
        env = self.load("MODEL_API_KEY=from-file\n", existing={"MODEL_API_KEY": "from-shell"})
        self.assertEqual(env["MODEL_API_KEY"], "from-shell")

    def test_an_empty_placeholder_does_not_set_the_variable(self):
        self.assertNotIn("MODEL_API_KEY", self.load("MODEL_API_KEY=\n"))

    def test_a_missing_file_is_fine(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            load_env(os.path.join(tempfile.gettempdir(), "no-such-dir", ".env"))
            self.assertEqual(dict(os.environ), {})


class TestLoadSettings(unittest.TestCase):
    def test_missing_file_gives_defaults_that_make_the_agent_hold(self):
        settings = load_settings(os.path.join(tempfile.gettempdir(), "no-such-dir", "config.json"))
        self.assertIsNone(settings.tax_rate)
        self.assertIsNone(settings.pickup_cost_usd)
        self.assertEqual(settings.shipping_preference, "cheapest")
        self.assertTrue(settings.policy.dry_run)
        self.assertFalse(settings.policy.live_bidding_configured)

    def test_every_documented_setting_is_read(self):
        raw = {
            "all_in_max_default_usd": 900.0,
            "tax_rate": 0.0825,
            "shipping_preference": "standard",
            "pickup_cost_usd": 120.0,
            "policy": {"tx_cap_usd": 2500.0, "session_budget_usd": 3000.0,
                       "allow_reserve_lots": True},
            "live_bidding": {"configured": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            settings = load_settings(path)
        self.assertEqual(settings.all_in_max_default_usd, 900.0)
        self.assertEqual(settings.tax_rate, 0.0825)
        self.assertEqual(settings.shipping_preference, "standard")
        self.assertEqual(settings.pickup_cost_usd, 120.0)
        self.assertEqual(settings.policy.tx_cap_usd, 2500.0)
        self.assertEqual(settings.policy.session_budget_usd, 3000.0)
        self.assertTrue(settings.policy.allow_reserve_lots)
        self.assertTrue(settings.policy.live_bidding_configured)

    def test_the_example_config_loads_and_stays_safe(self):
        example = os.path.join(os.path.dirname(__file__), "..", "config.example.json")
        settings = load_settings(example)
        self.assertEqual(settings.tax_rate, 0.1)
        self.assertIsNone(settings.pickup_cost_usd)
        self.assertEqual(settings.policy.tx_cap_usd, 4999.99)
        self.assertFalse(settings.policy.live_bidding_configured)
        self.assertFalse(settings.policy.allow_reserve_lots)


if __name__ == "__main__":
    unittest.main()
