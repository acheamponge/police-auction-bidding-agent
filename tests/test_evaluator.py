"""Tests for the all-in math and verdicts — run with: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.evaluator import all_in_total, evaluate, max_hammer_for
from src.lot_fetcher import parse_lot_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def ember_stub(stance="bid"):
    return {"judgment": {"stance": stance, "risks": [], "reasoning": "stub"},
            "_meta": {"simulated": True}}


class TestMath(unittest.TestCase):
    def test_all_in_total(self):
        # hammer 850, 15% premium, 10% tax on (hammer+premium), $0 shipping
        self.assertEqual(all_in_total(850, 0.15, 0.10, 0), 1075.25)

    def test_max_hammer(self):
        # $1,500 max, 15% premium, 10% tax, $0 shipping -> 1185.77
        self.assertEqual(max_hammer_for(1500, 0.15, 0.10, 0), 1185.77)

    def test_max_hammer_never_exceeds(self):
        for max_usd in (500, 1500, 6000, 13500):
            hammer = max_hammer_for(max_usd, 0.15, 0.10, 0)
            self.assertLessEqual(all_in_total(hammer, 0.15, 0.10, 0), max_usd)


class TestVerdicts(unittest.TestCase):
    def test_ended_lot_never_bids_even_when_math_clears(self):
        parsed = parse_lot_text(read_fixture("lot_vehicle.txt"))
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=13_500.00, tax_rate=0.10)
        # next bid 10,900 -> all-in 13,428.80, inside the max — but ended.
        self.assertEqual(ev.all_in_at_next_bid, 13428.80)
        self.assertEqual(ev.verdict, "NO_BID")
        self.assertIn("ended", ev.reason.lower())

    def test_reserve_lot_holds_for_human(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertTrue(any("reserve" in flag.lower() for flag in ev.risk_flags))

    def test_reserve_lot_bids_only_when_allowed(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=6_000.00, tax_rate=0.10,
                      allow_reserve_lots=True)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual(ev.next_bid, 4500.00)
        self.assertEqual(ev.all_in_at_next_bid, 5692.50)

    def test_no_premium_means_hold_not_bid(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIsNone(ev.all_in_at_next_bid)

    def test_math_does_not_clear(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        parsed["premium_pct"] = 15.00
        parsed["premium_stated"] = True
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=1_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "NO_BID")
        self.assertIn("does not clear", ev.reason)

    def test_math_clears_is_bid(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        parsed["premium_pct"] = 15.00
        parsed["premium_stated"] = True
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual(ev.next_bid, 4640.00)
        self.assertEqual(ev.all_in_at_next_bid, 5869.60)

    def test_no_tax_rate_means_hold(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        parsed["premium_pct"] = 15.00
        parsed["premium_stated"] = True
        ev = evaluate(parsed, ember_stub(), "https://example.invalid/lot",
                      all_in_max=6_000.00, tax_rate=None)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")


if __name__ == "__main__":
    unittest.main()
