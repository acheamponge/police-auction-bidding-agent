"""Tests for the all-in math and verdicts — run with: python3 -m unittest discover -s tests"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import model_read, read_fixture
from src.evaluator import all_in_total, choose_shipping, evaluate, max_hammer_for, render
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

    def test_everyday_lot_with_no_premium_bids_at_zero_premium(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "BID")
        self.assertTrue(ev.premium_absent)
        # next bid 4,640 + 10% tax + $12.95 standard shipping, no premium
        self.assertEqual(ev.all_in_at_next_bid, 5116.95)
        self.assertTrue(any("Bid Review" in flag for flag in ev.risk_flags))
        self.assertIn("Buyer's premium: none", render(ev))

    def test_premium_mentioned_without_a_rate_holds(self):
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        parsed["premium_absent"] = False
        ev = evaluate(parsed, model_read(parsed), URL, all_in_max=6_000.00, tax_rate=0.10)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("no rate could be read", ev.reason)
        self.assertIsNone(ev.all_in_at_next_bid)
        self.assertIsNone(ev.bid_amount)

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


class TestPhotoVeto(unittest.TestCase):
    """The photos can veto a bid exactly like any other Ember objection:
    the model said "bid" on the text, but what it saw in the photos stops it."""

    def evaluate(self, parsed, read, all_in_max=6_000.00):
        return evaluate(parsed, read, URL, all_in_max=all_in_max, tax_rate=0.10,
                        pickup_cost_usd=0.0)

    def photo_read(self, parsed, **photo_notes):
        return model_read(parsed, stance="bid",
                          estimated_value_usd={"low": 5_000, "high": 6_500,
                                               "basis": "Comparable cars."},
                          photo_notes=photo_notes)

    def test_photo_risks_hold_a_lot_the_text_would_bid_on(self):
        parsed = live_lot_with_premium()
        read = self.photo_read(
            parsed,
            observations=["A handwritten note is taped to the windshield."],
            risks=["The windshield note says the car needs a jump start and leaks coolant."],
            matches_listing=False,
            summary="The photos contradict the text: the car does not reliably start.")
        ev = self.evaluate(parsed, read)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("jump start", ev.reason)
        self.assertIn("Photo: The windshield note says the car needs a jump start "
                      "and leaks coolant.", ev.risk_flags)
        self.assertEqual(ev.photo_notes["summary"],
                         "The photos contradict the text: the car does not reliably start.")

    def test_photo_observations_without_risks_do_not_hold(self):
        parsed = live_lot_with_premium()
        read = self.photo_read(
            parsed, observations=["The car looks clean in the photos."],
            risks=[], matches_listing=True,
            summary="The photos match the listing.")
        self.assertEqual(self.evaluate(parsed, read).verdict, "BID")

    def test_the_render_quotes_what_the_photos_showed(self):
        parsed = live_lot_with_premium()
        read = self.photo_read(
            parsed,
            observations=["A handwritten note is taped to the windshield."],
            risks=["The windshield note says the car needs a jump start and leaks coolant."],
            matches_listing=False,
            summary="The photos contradict the text: the car does not reliably start.")
        text = render(self.evaluate(parsed, read))
        self.assertIn("Photos: The photos contradict the text: the car does not "
                      "reliably start. — do NOT match the listing", text)
        self.assertIn("photo risk: The windshield note says the car needs a jump start "
                      "and leaks coolant.", text)


class TestBidPlan(unittest.TestCase):
    """A BID comes with what to enter on the site. Rates here: 15% premium,
    10% tax, so all-in = hammer * 1.265 (+ shipping)."""

    def evaluate(self, parsed, all_in_max=6_000.00, **kwargs):
        read = kwargs.pop("read", None) or model_read(parsed)
        return evaluate(parsed, read, URL, all_in_max=all_in_max, tax_rate=0.10,
                        pickup_cost_usd=0.0, **kwargs)

    def test_proxy_maximum_is_the_highest_whole_dollar_inside_the_all_in_max(self):
        ev = self.evaluate(live_lot_with_premium())
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4743.00, True))
        self.assertEqual(ev.all_in_at_bid, all_in_total(4743.00, 0.15, 0.10, 0))
        self.assertLessEqual(ev.all_in_at_bid, 6_000.00)
        self.assertGreater(all_in_total(4744.00, 0.15, 0.10, 0), 6_000.00)
        self.assertIn("your all-in max", ev.bid_note)

    def test_models_top_value_estimate_lowers_the_proxy_maximum(self):
        parsed = live_lot_with_premium()
        ev = self.evaluate(parsed, read=model_read(
            parsed, estimated_value_usd={"low": 5_000, "high": 5_900}))
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4664.00, True))
        self.assertLessEqual(ev.all_in_at_bid, 5_900.00)
        self.assertIn("top value estimate", ev.bid_note)

    def test_spending_policy_headroom_lowers_the_proxy_maximum(self):
        ev = self.evaluate(live_lot_with_premium(), spend_limit_usd=5_880.00)
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4648.00, True))
        self.assertLessEqual(ev.all_in_at_bid, 5_880.00)
        self.assertIn("spending policy", ev.bid_note)

    def test_reserve_lot_gets_a_plain_bid_because_a_proxy_would_bid_its_full_maximum(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        ev = self.evaluate(parsed, allow_reserve_lots=True)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4500.00, False))
        self.assertEqual(ev.all_in_at_bid, ev.all_in_at_next_bid)
        self.assertIn("reserve is not met", ev.bid_note)

    def test_plain_bid_when_the_next_bid_is_already_at_the_ceiling(self):
        ev = self.evaluate(live_lot_with_premium(), all_in_max=5_869.60)
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4640.00, False))
        self.assertEqual(ev.all_in_at_bid, 5869.60)
        self.assertIn("already at the ceiling", ev.bid_note)

    def test_plan_says_so_when_even_the_next_bid_is_over_what_the_policy_has_left(self):
        ev = self.evaluate(live_lot_with_premium(), spend_limit_usd=2_000.00)
        self.assertEqual(ev.verdict, "BID")
        self.assertEqual((ev.bid_amount, ev.bid_is_proxy), (4640.00, False))
        self.assertEqual(ev.all_in_at_bid, 5869.60)
        self.assertIn("is over what the spending policy has left ($2,000.00)", ev.bid_note)
        self.assertNotIn("already at the ceiling", ev.bid_note)

    def test_no_bid_plan_unless_the_verdict_is_bid(self):
        ev = self.evaluate(live_lot_with_premium(), all_in_max=1_000.00)
        self.assertEqual(ev.verdict, "NO_BID")
        self.assertEqual((ev.bid_amount, ev.all_in_at_bid, ev.bid_note), (None, None, None))


class TestValueEstimate(unittest.TestCase):
    def evaluate(self, parsed, all_in_max, **overrides):
        return evaluate(parsed, model_read(parsed, **overrides), URL, all_in_max=all_in_max,
                        tax_rate=0.10, pickup_cost_usd=0.0)

    def test_without_a_max_the_agent_suggests_one_and_never_bids(self):
        # Even a wildly high estimate (say, planted in the listing text) cannot
        # make the agent bid: only a max the buyer gave can.
        ev = self.evaluate(live_lot_with_premium(), None,
                           estimated_value_usd={"low": 50_000, "high": 90_000})
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertEqual(ev.suggested_all_in_max, 50_000.0)
        self.assertIsNone(ev.all_in_max)
        self.assertIsNone(ev.bid_amount)
        self.assertIn("No all-in max was given", ev.reason)
        self.assertIn("A max of $50,000.00", ev.reason)
        # 50,000 / 1.265: the hammer that max would allow
        self.assertEqual(ev.max_hammer, 39525.69)
        self.assertIn("hammer bid up to $39,525.69", ev.reason)
        self.assertNotIn("already past it", ev.reason)
        self.assertIn("suggested: $50,000.00", render(ev))

    def test_suggestion_says_so_when_the_price_is_already_past_it(self):
        ev = self.evaluate(live_lot_with_premium(), None,
                           estimated_value_usd={"low": 3_000, "high": 9_000})
        self.assertEqual(ev.suggested_all_in_max, 3_000.0)
        self.assertIn("lands all-in at $5,869.60, which is already past it", ev.reason)

    def test_without_a_max_or_an_estimate_there_is_nothing_to_suggest(self):
        ev = self.evaluate(live_lot_with_premium(), None)
        self.assertEqual(ev.verdict, "HOLD_FOR_HUMAN")
        self.assertIsNone(ev.suggested_all_in_max)
        self.assertIn("no value estimate", ev.reason)
        self.assertIn("All-in max: not given", render(ev))

    def test_card_shows_the_estimate_its_basis_and_the_gap_to_cost(self):
        ev = self.evaluate(live_lot_with_premium(), 6_000.00, estimated_value_usd={
            "low": 5_500, "high": 7_000, "basis": "Similar 14k bracelets by gold weight."})
        card = render(ev)
        self.assertIn("Ember value estimate: $5,500.00 to $7,000.00 — "
                      "Similar 14k bracelets by gold weight.", card)
        # all-in at the next bid is $5,869.60
        self.assertIn("$369.60 above the low estimate and $1,130.40 below the high estimate", card)


if __name__ == "__main__":
    unittest.main()
