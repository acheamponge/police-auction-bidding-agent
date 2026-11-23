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


class TestHeadroom(unittest.TestCase):
    def test_headroom_is_the_smaller_of_the_cap_and_what_the_budget_has_left(self):
        state = PolicyState(PolicyConfig(tx_cap_usd=4_999.99, session_budget_usd=2_000.00))
        self.assertEqual(state.headroom_usd, 2_000.00)
        state.commit("https://example.invalid/lot-a", 1_500.00)
        self.assertEqual(state.headroom_usd, 500.00)
        state.commit("https://example.invalid/lot-b", 900.00)
        self.assertEqual(state.headroom_usd, 0.0)

    def test_headroom_never_exceeds_the_transaction_cap(self):
        state = PolicyState(PolicyConfig(tx_cap_usd=4_999.99, session_budget_usd=20_000.00))
        self.assertEqual(state.headroom_usd, 4_999.99)


class TestCostLedger(unittest.TestCase):
    def record(self, ledger, cost_usd):
        return ledger.record(lot_url="https://example.invalid/a", model="muse-ember-1.0",
                             input_tokens=100, cached_input_tokens=0,
                             output_tokens=100, cost_usd=cost_usd, simulated=False)

    def test_budget_wall_refuses_a_call_before_it_is_made(self):
        import tempfile
        from src.cost_ledger import BudgetExceeded, CostLedger
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CostLedger(os.path.join(tmp, "ledger.jsonl"), budget_usd=0.01)
            self.record(ledger, 0.005)
            ledger.check_budget(0.005)  # lands exactly on the budget: allowed
            with self.assertRaises(BudgetExceeded):
                ledger.check_budget(0.0051)
            self.assertEqual(ledger.summary()["calls"], 1)

    def test_spend_is_always_recorded_even_past_the_budget(self):
        # Once a call has been made the money is gone; hiding it from the
        # ledger would make the running total a lie.
        import json
        import tempfile
        from src.cost_ledger import CostLedger
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.jsonl")
            ledger = CostLedger(path, budget_usd=0.01)
            self.record(ledger, 0.02)
            self.assertEqual(ledger.summary()["total_spent_usd"], 0.02)
            self.assertEqual(ledger.summary()["remaining_usd"], 0.0)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.loads(fh.read())["cost_usd"], 0.02)
            # A fresh ledger on the same file picks the overspend back up.
            self.assertEqual(CostLedger(path, budget_usd=0.01).total_usd, 0.02)

    def test_ember_pricing_math(self):
        from src.ember_client import price_call
        # 3,000 input tokens, 600 output tokens at standard tier
        cost = price_call(3_000, 0, 600)
        self.assertAlmostEqual(cost, 0.0003 + 0.0003, places=6)
        # Long prompt bills the whole request at the higher tier
        self.assertGreater(price_call(250_000, 0, 600), price_call(199_000, 0, 600))


if __name__ == "__main__":
    unittest.main()
