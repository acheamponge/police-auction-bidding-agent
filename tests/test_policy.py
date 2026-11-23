"""Tests for the spending policy gate — run with: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.policy import PolicyConfig, PolicyState


class TestPolicy(unittest.TestCase):
    def test_no_bid_never_spends(self):
        state = PolicyState(PolicyConfig(dry_run=False, live_bidding_configured=True))
        decision = state.evaluate("NO_BID", 100.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "DENIED")

    def test_hold_never_spends(self):
        state = PolicyState(PolicyConfig(dry_run=False, live_bidding_configured=True))
        decision = state.evaluate("HOLD_FOR_HUMAN", 100.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "DENIED")

    def test_over_tx_cap_denied_outright(self):
        # $5,000+ all-in needs a wire transfer; the agent never wires money.
        state = PolicyState(PolicyConfig(dry_run=False, live_bidding_configured=True))
        decision = state.evaluate("BID", 5_100.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "DENIED")
        self.assertIn("wire", decision.note.lower())

    def test_session_budget_denied(self):
        state = PolicyState(PolicyConfig(session_budget_usd=1_000.00, dry_run=True))
        state.commit("https://example.invalid/lot-a", 900.00)
        decision = state.evaluate("BID", 200.00, "https://example.invalid/lot-b")
        self.assertEqual(decision.decision, "DENIED")

    def test_dry_run_is_default(self):
        state = PolicyState(PolicyConfig())
        self.assertTrue(state.config.dry_run)
        decision = state.evaluate("BID", 1_000.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "DRY_RUN")

    def test_live_needs_config(self):
        state = PolicyState(PolicyConfig(dry_run=False, live_bidding_configured=False))
        decision = state.evaluate("BID", 1_000.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "HUMAN_APPROVAL")

    def test_live_allowed_only_when_configured(self):
        state = PolicyState(PolicyConfig(dry_run=False, live_bidding_configured=True))
        decision = state.evaluate("BID", 1_000.00, "https://example.invalid/lot")
        self.assertEqual(decision.decision, "LIVE")


class TestCostLedger(unittest.TestCase):
    def test_budget_wall(self):
        import tempfile
        from src.cost_ledger import BudgetExceeded, CostLedger
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.jsonl")
            ledger = CostLedger(path, budget_usd=0.01)
            ledger.record(lot_url="https://example.invalid/a", model="muse-ember-1.0",
                          input_tokens=100, cached_input_tokens=0,
                          output_tokens=100, cost_usd=0.005, simulated=False)
            with self.assertRaises(BudgetExceeded):
                ledger.record(lot_url="https://example.invalid/b", model="muse-ember-1.0",
                              input_tokens=100, cached_input_tokens=0,
                              output_tokens=100, cost_usd=0.01, simulated=False)

    def test_ember_pricing_math(self):
        from src.ember_client import price_call
        # 3,000 input tokens, 600 output tokens at standard tier
        cost = price_call(3_000, 0, 600)
        self.assertAlmostEqual(cost, 0.0003 + 0.0003, places=6)
        # Long prompt bills the whole request at the higher tier
        self.assertGreater(price_call(250_000, 0, 600), price_call(199_000, 0, 600))


if __name__ == "__main__":
    unittest.main()
