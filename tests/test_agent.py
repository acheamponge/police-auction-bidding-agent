"""Tests for the Muse Ember client and the agent orchestration. No network
and no API key: the HTTP transport is replaced with a fake.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import model_read, read_fixture
from src.agent import BiddingAgent, render_result
from src.cost_ledger import BudgetExceeded, CostLedger
from src.ember_client import EmberClient, EmberError, price_call
from src.lot_fetcher import html_to_text, parse_lot_text
from src.policy import PolicyConfig

URL = "https://example.invalid/lot"
USAGE = {"prompt_tokens": 4000, "completion_tokens": 500,
         "prompt_tokens_details": {"cached_tokens": 1000}}


class FakeTransport:
    """Stands in for the HTTP POST. Records each request and replies with
    the given message content."""

    def __init__(self, content):
        self.content = content
        self.requests = []

    def __call__(self, url, body, headers, timeout_s):
        self.requests.append({"url": url, "body": body, "headers": headers})
        return {"choices": [{"message": {"content": self.content}}], "usage": USAGE}


def no_transport(*args, **kwargs):
    raise AssertionError("fixture mode must never make a request")


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = os.path.join(self.tmp.name, "ledger.jsonl")
        self.text = html_to_text(read_fixture("lot_page.html"))
        self.parsed = parse_lot_text(self.text)

    def ledger(self, budget_usd=10.0):
        return CostLedger(self.ledger_path, budget_usd=budget_usd)

    def live_client(self, ledger, content):
        transport = FakeTransport(content)
        client = EmberClient(ledger=ledger, api_key="test-key",
                             base_url="https://model.invalid/v1", transport=transport)
        return client, transport

    def agent(self, ledger, ember, **policy):
        return BiddingAgent(ledger=ledger, tax_rate=0.10, pickup_cost_usd=0.0,
                            policy_config=PolicyConfig(**policy), ember=ember)


class TestEmberClient(AgentTestCase):
    def test_live_call_sends_the_lot_body_and_logs_the_real_cost(self):
        ledger = self.ledger()
        client, transport = self.live_client(ledger, json.dumps(model_read(self.parsed)))
        result = client.evaluate_lot(self.text, URL)

        request = transport.requests[0]
        self.assertEqual(request["url"], "https://model.invalid/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(request["body"]["model"], "muse-ember-1.0")
        self.assertGreater(request["body"]["max_tokens"], 0)
        self.assertEqual(request["body"]["reasoning_effort"], "low")
        prompt = request["body"]["messages"][1]["content"]
        self.assertIn("Listing ID: 18690397", prompt)
        self.assertIn("New York 11430\n$23.95", prompt)
        self.assertNotIn("Sign Out", prompt)

        expected_cost = price_call(4000, 1000, 500)
        self.assertAlmostEqual(expected_cost, 0.000554, places=9)
        self.assertFalse(result["_meta"]["simulated"])
        self.assertAlmostEqual(result["_meta"]["cost_usd"], expected_cost, places=9)
        summary = ledger.summary()
        self.assertEqual((summary["calls"], summary["live_calls"]), (1, 1))
        self.assertAlmostEqual(summary["total_spent_usd"], expected_cost, places=6)

    def test_call_that_could_cross_the_budget_is_never_sent(self):
        ledger = self.ledger(budget_usd=0.0001)
        client, transport = self.live_client(ledger, json.dumps(model_read(self.parsed)))
        with self.assertRaises(BudgetExceeded):
            client.evaluate_lot(self.text, URL)
        self.assertEqual(transport.requests, [])
        self.assertEqual(ledger.summary()["calls"], 0)

    def test_unusable_reply_is_an_error_but_its_cost_is_still_logged(self):
        # None is what the API sends when the model's thinking used up the
        # whole output cap before it wrote an answer.
        for number, content in enumerate(("Sure! Here is the JSON you asked for.", "[1, 2, 3]", None)):
            with self.subTest(content=content):
                ledger = CostLedger(os.path.join(self.tmp.name, f"{number}.jsonl"))
                client, _ = self.live_client(ledger, content)
                with self.assertRaises(EmberError):
                    client.evaluate_lot(self.text, URL)
                self.assertEqual(ledger.summary()["live_calls"], 1)

    def test_fixture_mode_makes_no_request_and_costs_nothing(self):
        ledger = self.ledger()
        client = EmberClient(ledger=ledger, api_key="", transport=no_transport)
        result = client.evaluate_lot(self.text, URL)
        self.assertTrue(result["_meta"]["simulated"])
        self.assertEqual(result["judgment"]["stance"], "bid")
        self.assertEqual(result["premium_pct"], 15.00)
        summary = ledger.summary()
        self.assertEqual((summary["calls"], summary["simulated_calls"]), (1, 1))
        self.assertEqual(summary["total_spent_usd"], 0.0)

    def test_base_url_comes_from_the_environment_at_construction(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"MODEL_API_BASE": "https://proxy.invalid/v9/"}):
            client = EmberClient(api_key="k")
        self.assertEqual(client.url, "https://proxy.invalid/v9/chat/completions")


class TestBiddingAgent(AgentTestCase):
    def test_clearing_lot_in_dry_run_prints_the_exact_bid_and_spends_nothing(self):
        ledger = self.ledger()
        agent = self.agent(ledger, EmberClient(ledger=ledger, api_key="", transport=no_transport))
        result = agent.evaluate_page(self.parsed, self.text, URL, all_in_max=1_500.00)

        self.assertEqual(result.evaluation.verdict, "BID")
        self.assertEqual(result.policy_decision, "DRY_RUN")
        # hammer 850 + 15% premium + 10% tax + $23.95 pickup fee
        self.assertEqual(result.evaluation.all_in_at_next_bid, 1099.20)
        self.assertIn("Bid request (NOT SENT)", result.bid_request_text)
        self.assertIn("listing id: 18690397", result.bid_request_text)
        # Proxy maximum: the highest whole dollar whose all-in fits the
        # $1,500 max. 1,166 * 1.265 + 23.95 = 1,498.94.
        self.assertIn("proxy bid, maximum: $1,166.00", result.bid_request_text)
        self.assertIn("bids now: $850.00", result.bid_request_text)
        self.assertIn("all-in at most: $1,498.94", result.bid_request_text)
        self.assertIsNone(result.live_outcome)
        self.assertEqual(agent.policy_state.exposure_usd, 0.0)
        self.assertIn("SIMULATED", render_result(result))
        # Fixture mode must not claim a model looked at the lot.
        self.assertIn("No model checked this lot", result.evaluation.reason)
        self.assertNotIn("Muse Ember raised no objection", result.evaluation.reason)

    def test_proxy_maximum_is_kept_inside_what_the_policy_has_left(self):
        # $1,200 session budget: a proxy sized to the $1,500 max alone would
        # commit $1,498.94 and be denied. Sized to the headroom it goes through.
        ledger = self.ledger()
        agent = self.agent(ledger, EmberClient(ledger=ledger, api_key="", transport=no_transport),
                           session_budget_usd=1_200.00)
        result = agent.evaluate_page(self.parsed, self.text, URL, all_in_max=1_500.00)
        self.assertEqual(result.policy_decision, "DRY_RUN")
        # (1,200 - 23.95) / 1.265 = 929.68 -> $929, and $930 would go over
        self.assertEqual(result.evaluation.bid_amount, 929.00)
        self.assertLessEqual(result.evaluation.all_in_at_bid, 1_200.00)
        self.assertGreater(result.evaluation.all_in_at_bid, 1_198.00)
        self.assertIn("spending policy", result.evaluation.bid_note)

    def test_policy_judges_the_most_a_bid_can_cost_and_denies_past_the_budget(self):
        # Next bid all-in is $1,099.20; with $1,000 left even that is too much.
        ledger = self.ledger()
        agent = self.agent(ledger, EmberClient(ledger=ledger, api_key="", transport=no_transport),
                           session_budget_usd=1_000.00)
        result = agent.evaluate_page(self.parsed, self.text, URL, all_in_max=1_500.00)
        self.assertEqual(result.evaluation.verdict, "BID")
        self.assertEqual(result.policy_decision, "DENIED")
        self.assertIsNone(result.bid_request_text)

    def test_everyday_lot_with_no_premium_gets_a_bid_plan_in_fixture_mode(self):
        text = read_fixture("lot_no_premium.txt").replace("US $4,620.00", "US $46.00")
        ledger = self.ledger()
        agent = BiddingAgent(ledger=ledger, tax_rate=0.10,
                             ember=EmberClient(ledger=ledger, api_key="", transport=no_transport))
        result = agent.evaluate_page(parse_lot_text(text), text, URL, all_in_max=200.00)
        self.assertEqual(result.evaluation.verdict, "BID")
        self.assertTrue(result.evaluation.premium_absent)
        self.assertEqual(result.evaluation.shipping_method, "Standard Shipping")
        # next bid 66 + 10% tax + $12.95 shipping, no premium
        self.assertEqual(result.evaluation.all_in_at_next_bid, 85.55)
        self.assertEqual(result.policy_decision, "DRY_RUN")
        # (200 - 12.95) / 1.10 = 170.05 -> $170
        self.assertIn("proxy bid, maximum: $170.00", result.bid_request_text)
        self.assertIn("bids now: $66.00", result.bid_request_text)

    def test_no_max_holds_and_suggests_one_from_the_models_low_estimate(self):
        ledger = self.ledger()
        reply = model_read(self.parsed, estimated_value_usd={"low": 1_200, "high": 2_000})
        client, _ = self.live_client(ledger, json.dumps(reply))
        result = self.agent(ledger, client).evaluate_page(self.parsed, self.text, URL, None)
        self.assertEqual(result.evaluation.verdict, "HOLD_FOR_HUMAN")
        self.assertEqual(result.evaluation.suggested_all_in_max, 1_200.0)
        self.assertEqual(result.policy_decision, "DENIED")
        self.assertIsNone(result.bid_request_text)

    def test_live_bid_reason_says_the_model_agreed(self):
        ledger = self.ledger()
        client, _ = self.live_client(ledger, json.dumps(model_read(self.parsed)))
        result = self.agent(ledger, client).evaluate_page(self.parsed, self.text, URL, 1_500.00)
        self.assertEqual(result.evaluation.verdict, "BID")
        self.assertIn("Muse Ember raised no objection", result.evaluation.reason)
        self.assertNotIn("No model checked", result.evaluation.reason)

    def test_model_objection_stops_the_bid_end_to_end(self):
        ledger = self.ledger()
        reply = model_read(self.parsed, stance="hold",
                           reasoning="Damage is only described as 'see images'.")
        client, _ = self.live_client(ledger, json.dumps(reply))
        result = self.agent(ledger, client).evaluate_page(
            self.parsed, self.text, URL, all_in_max=1_500.00)

        self.assertEqual(result.evaluation.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("see images", result.evaluation.reason)
        self.assertEqual(result.policy_decision, "DENIED")
        self.assertIsNone(result.bid_request_text)
        self.assertIn("[live Muse Ember]", render_result(result))

    def test_model_misread_of_the_price_stops_the_bid_end_to_end(self):
        ledger = self.ledger()
        client, _ = self.live_client(
            ledger, json.dumps(model_read(self.parsed, current_price=80.00)))
        result = self.agent(ledger, client).evaluate_page(
            self.parsed, self.text, URL, all_in_max=1_500.00)
        self.assertEqual(result.evaluation.verdict, "HOLD_FOR_HUMAN")
        self.assertIn("current price (parser: 800.0, Ember: 80.0)", result.evaluation.reason)
        self.assertIsNone(result.bid_request_text)

    def test_pickup_only_lot_holds_until_a_pickup_cost_is_configured(self):
        ledger = self.ledger()
        agent = BiddingAgent(ledger=ledger, tax_rate=0.10,
                             ember=EmberClient(ledger=ledger, api_key="", transport=no_transport))
        result = agent.evaluate_page(self.parsed, self.text, URL, all_in_max=1_500.00)
        self.assertEqual(result.evaluation.verdict, "HOLD_FOR_HUMAN")
        self.assertEqual(result.policy_decision, "DENIED")

    def test_live_and_confirmed_bid_is_still_refused_without_a_transport(self):
        ledger = self.ledger()
        agent = self.agent(ledger, EmberClient(ledger=ledger, api_key="", transport=no_transport),
                           dry_run=False, live_bidding_configured=True)
        result = agent.evaluate_page(self.parsed, self.text, URL, all_in_max=1_500.00,
                                     live=True, confirm=True)
        self.assertEqual(result.policy_decision, "LIVE")
        self.assertTrue(result.live_outcome.startswith("REFUSED:"))
        self.assertEqual(agent.policy_state.exposure_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
