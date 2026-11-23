"""Tests for the lot-page parser — run with: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.lot_fetcher import parse_lot_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class TestLotParser(unittest.TestCase):
    def test_vehicle_lot_reads_premium(self):
        parsed = parse_lot_text(read_fixture("lot_vehicle.txt"))
        self.assertEqual(parsed["status"], "ended")
        self.assertEqual(parsed["current_price"], 10850.00)
        self.assertEqual(parsed["premium_pct"], 12.00)
        self.assertTrue(parsed["premium_stated"])
        self.assertEqual(parsed["bid_increment"], 50.00)
        self.assertFalse(parsed["reserve_not_met"])
        self.assertEqual(parsed["condition"], "Untested - Sold as-is")
        methods = {o["method"]: o for o in parsed["shipping_options"]}
        self.assertEqual(methods["Customer Pick-up"]["price_usd"], 0.00)
        self.assertTrue(methods["Customer Pick-up"]["available"])
        self.assertFalse(methods["Standard Shipping"]["available"])

    def test_reserve_lot_flagged(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        self.assertTrue(parsed["reserve_not_met"])
        self.assertEqual(parsed["premium_pct"], 15.00)
        self.assertEqual(parsed["current_price"], 4450.00)

    def test_no_premium_line_is_unknown_not_zero(self):
        # The core rule: a missing premium line means UNKNOWN, never 0%
        # and never the 16.5% third-party claim. The agent must hold.
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        self.assertEqual(parsed["status"], "live")
        self.assertIsNone(parsed["premium_pct"])
        self.assertFalse(parsed["premium_stated"])
        self.assertEqual(parsed["current_price"], 4620.00)
        methods = {o["method"]: o for o in parsed["shipping_options"]}
        self.assertEqual(methods["Standard Shipping"]["price_usd"], 12.95)
        self.assertEqual(methods["Expedited Shipping"]["price_usd"], 24.95)


if __name__ == "__main__":
    unittest.main()
