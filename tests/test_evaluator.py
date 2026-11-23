"""Tests for the all-in math and verdicts — run with: python3 -m unittest discover -s tests"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import model_read, read_fixture
from src.evaluator import all_in_total, choose_shipping, evaluate, max_hammer_for
from src.lot_fetcher import parse_lot_text

URL = "https://example.invalid/lot"


def live_lot_with_premium():
    """A live, non-reserve lot with a 15% premium. Next bid $4,640.00;
    pickup $0.00, standard $12.95, expedited $24.95."""
    parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
    parsed["premium_pct"] = 15.00
    parsed["premium_stated"] = True
    return parsed


class TestMath(unittest.TestCase):
    def test_all_in_total(self):
        # hammer 850, 15% premium, 10% tax on (hammer+premium), $0 shipping
        self.assertEqual(all_in_total(850, 0.15, 0.10, 0), 1075.25)

    def test_max_hammer(self):
        # $1,500 max, 15% premium, 10% tax, $0 shipping -> 1185.77
        self.assertEqual(max_hammer_for(1500, 0.15, 0.10, 0), 1185.77)

    def test_max_hammer_is_the_largest_bid_that_stays_inside_the_max(self):
        rng = random.Random(7)
        for _ in range(20_000):
            all_in_max = round(rng.uniform(50, 15_000), 2)
            premium = rng.choice([0.10, 0.12, 0.15])
            tax = rng.choice([0.0, 0.0625, 0.0825, 0.10])
            shipping = rng.choice([0.0, 12.95, 23.95])
            hammer = max_hammer_for(all_in_max, premium, tax, shipping)
            case = (all_in_max, premium, tax, shipping, hammer)
            self.assertLessEqual(all_in_total(hammer, premium, tax, shipping), all_in_max, case)
            self.assertGreater(
                all_in_total(round(hammer + 0.01, 2), premium, tax, shipping), all_in_max, case)

    def test_max_hammer_is_zero_when_shipping_alone_exceeds_the_max(self):
        self.assertEqual(max_hammer_for(20.00, 0.15, 0.10, 23.95), 0.0)


class TestShipping(unittest.TestCase):
    def test_pickup_is_never_chosen_without_a_pickup_cost(self):
        parsed = live_lot_with_premium()
        self.assertEqual(choose_shipping(parsed), ("Standard Shipping", 12.95))

    def test_pickup_is_chosen_when_the_buyer_says_what_it_costs_them(self):
        parsed = live_lot_with_premium()
        self.assertEqual(choose_shipping(parsed, pickup_cost_usd=0.0), ("Customer Pick-up", 0.0))

    def test_pickup_cost_is_added_to_the_page_fee_and_can_lose_to_shipping(self):
        parsed = {"shipping_options": [
            {"method": "Customer Pick-up", "price_usd": 23.95, "available": True},
            {"method": "Standard Shipping", "price_usd": 300.00, "available": True},
        ]}
        self.assertEqual(choose_shipping(parsed, pickup_cost_usd=150.0), ("Customer Pick-up", 173.95))
        self.assertEqual(choose_shipping(parsed, pickup_cost_usd=500.0), ("Standard Shipping", 300.00))

    def test_named_preference_matches_despite_the_hyphen(self):
        parsed = live_lot_with_premium()
        self.assertEqual(choose_shipping(parsed, "pickup", pickup_cost_usd=40.0),
                         ("Customer Pick-up", 40.0))
        self.assertEqual(choose_shipping(parsed, "expedited"), ("Expedited Shipping", 24.95))

    def test_named_preference_that_is_not_offered_gives_no_method(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        self.assertEqual(choose_shipping(parsed, "standard", pickup_cost_usd=0.0), (None, None))

    def test_pickup_only_lot_without_a_pickup_cost_holds(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00,
                      tax_rate=0.10, allow_reserve_lots=True)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("pickup cost", ev.reason)
        self.assertIsNone(ev.all_in_at_next_bid)


class TestVerdicts(unittest.TestCase):
    def test_ended_lot_never_bids_even_when_math_clears(self):
        parsed = parse_lot_text(read_fixture("lot_vehicle.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=13_500.00,
                      tax_rate=0.10, pickup_cost_usd=0.0)
        # next bid 10,900 -> all-in 13,428.80, inside the max — but ended.
        self.assertEqual(ev.all_in_at_next_bid, 13428.80)
        self.assertEqual(ev.verdict, "NO_BID")
        self.assertIn("ended", ev.reason.lower())

    def test_unknown_status_holds_even_when_everything_else_clears(self):
        parsed = live_lot_with_premium()
        parsed["status"] = "unknown"
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("still running", ev.reason)

    def test_reserve_lot_holds_for_human(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00,
                      tax_rate=0.10, pickup_cost_usd=0.0)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("Reserve not met", ev.reason)
        self.assertTrue(any("reserve" in flag.lower() for flag in ev.risk_flags))

    def test_reserve_lot_bids_only_when_allowed(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00,
                      tax_rate=0.10, allow_reserve_lots=True, pickup_cost_usd=0.0)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual(ev.next_bid, 4500.00)
        self.assertEqual(ev.all_in_at_next_bid, 5692.50)

    def test_no_premium_means_hold_not_bid(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("premium is not stated", ev.reason)
        self.assertIsNone(ev.all_in_at_next_bid)

    def test_math_does_not_clear(self):
        parsed = live_lot_with_premium()
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=1_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "NO_BID")
        self.assertIn("does not clear", ev.reason)

    def test_math_clears_is_bid(self):
        parsed = live_lot_with_premium()
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00,
                      tax_rate=0.10, pickup_cost_usd=0.0)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual(ev.next_bid, 4640.00)
        self.assertEqual(ev.shipping_method, "Customer Pick-up")
        self.assertEqual(ev.all_in_at_next_bid, 5869.60)

    def test_shipped_lot_includes_the_shipping_price(self):
        parsed = live_lot_with_premium()
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.shipping_method, "Standard Shipping")
        self.assertEqual(ev.all_in_at_next_bid, 5882.55)

    def test_one_cent_over_the_max_is_no_bid_and_exactly_the_max_is_bid(self):
        parsed = live_lot_with_premium()
        over = evaluate(parsed, model_read(parsed), URL, all_in_max=5_869.59,
                        tax_rate=0.10, pickup_cost_usd=0.0)
        exact = evaluate(parsed, model_read(parsed), URL, all_in_max=5_869.60,
                         tax_rate=0.10, pickup_cost_usd=0.0)
        self.assertEqual(over.verdict, "NO_BID")
        self.assertEqual(exact.verdict, "BID")

    def test_no_tax_rate_means_hold(self):
        parsed = live_lot_with_premium()
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=None)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("tax rate", ev.reason.lower())


class TestModelCanOnlyMakeTheAgentMoreCareful(unittest.TestCase):
    def evaluate(self, parsed, read, all_in_max=6_000.00):
        return evaluate(parsed, read, URL, all_in_max=all_in_max, tax_rate=0.10,
                        pickup_cost_usd=0.0)

    def test_hold_stance_turns_a_clearing_bid_into_a_hold(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(parsed, stance="hold",
                                              reasoning="Listing says it does not start."))
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("does not start", ev.reason)

    def test_no_bid_stance_turns_a_clearing_bid_into_a_hold(self):
        parsed = live_lot_with_premium()
        self.assertEqual(self.evaluate(parsed, model_read(parsed, stance="no_bid")).verdict,
                         "HOLD_FOR_HUMAN")

    def test_missing_stance_is_not_a_go_ahead(self):
        parsed = live_lot_with_premium()
        read = model_read(parsed)
        read["judgment"] = {}
        ev = self.evaluate(parsed, read)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("missing", ev.reason)

    def test_bid_stance_cannot_rescue_math_that_does_not_clear(self):
        parsed = live_lot_with_premium()
        self.assertEqual(self.evaluate(parsed, model_read(parsed, stance="bid"),
                                       all_in_max=1_000.00).verdict, "NO_BID")

    def test_disagreement_on_the_premium_holds(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(parsed, premium_pct=10.0))
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("buyer's premium % (parser: 15.0, Ember: 10.0)", ev.reason)

    def test_disagreement_on_the_price_holds(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(parsed, current_price=462.00))
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("current price", ev.reason)

    def test_disagreement_on_status_or_reserve_holds(self):
        parsed = live_lot_with_premium()
        self.assertIn("auction status",
                      self.evaluate(parsed, model_read(parsed, status="ended")).reason)
        self.assertIn("reserve not met",
                      self.evaluate(parsed, model_read(parsed, reserve_not_met=True)).reason)

    def test_model_that_reads_no_shipping_table_holds(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(parsed, shipping_options=[]))
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("Customer Pick-up price", ev.reason)

    def test_same_numbers_in_another_notation_are_not_a_disagreement(self):
        parsed = live_lot_with_premium()
        read = model_read(parsed, current_price="$4,620.00", premium_pct="15.00%",
                          bid_increment=20,
                          shipping_options=[
                              {"method": "Customer Pickup", "price_usd": 0, "available": True},
                              {"method": "Standard Shipping (5-7 business days)",
                               "price_usd": "12.95", "available": True},
                              {"method": "Expedited Shipping", "price_usd": 24.95, "available": True},
                          ])
        self.assertEqual(self.evaluate(parsed, read).verdict, "BID")

    def test_all_in_above_the_models_top_value_estimate_holds(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(
            parsed, estimated_value_usd={"low": 3_000, "high": 5_000}))
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("top value estimate of $5,000.00", ev.reason)
        self.assertEqual((ev.value_estimate_low, ev.value_estimate_high), (3_000.0, 5_000.0))

    def test_all_in_inside_the_models_value_estimate_bids(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, model_read(
            parsed, estimated_value_usd={"low": 5_500, "high": 7_000}))
        self.assertEqual(ev.verdict, "BID")


if __name__ == "__main__":
    unittest.main()
