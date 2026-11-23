"""Tests for watching lots over time. No network, no key, no real sleeping.

Run with: python3 -m unittest discover -s tests
"""

import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import model_read, read_fixture
from src.agent import BiddingAgent
from src.cost_ledger import CostLedger
from src.ember_client import EmberClient
from src.lot_fetcher import FetchError, html_to_text, parse_lot_text
from src.policy import PolicyConfig
from src.watcher import MIN_INTERVAL_S, watch

URL = "https://example.invalid/lot"
OTHER = "https://example.invalid/other"
LIVE = html_to_text(read_fixture("lot_page.html"))          # live at $800.00
HIGHER = LIVE.replace("US $800.00", "US $900.00")            # still inside a $1,500 max
TOO_HIGH = LIVE.replace("US $800.00", "US $1,400.00")        # past a $1,500 max
ENDED = HIGHER.replace("Ends Nov 30, 2026", "Ended Nov 30, 2026")


class Pages:
    """A fake fetch: each URL serves its texts in order, repeating the last."""

    def __init__(self, **by_url):
        self.queues = {url: list(texts) for url, texts in by_url.items()}
        self.fetches = []

    def __call__(self, url):
        self.fetches.append(url)
        queue = self.queues[url]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return parse_lot_text(item), item


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ledger = CostLedger(os.path.join(tmp.name, "ledger.jsonl"))
        self.agent = BiddingAgent(
            ledger=self.ledger, tax_rate=0.10, pickup_cost_usd=0.0,
            policy_config=PolicyConfig(),
            ember=EmberClient(ledger=self.ledger, api_key=""))
        self.sleeps = []
        self.lines = []

    def watch(self, pages, lots=None, **kwargs):
        return watch(self.agent, lots or [{"url": URL, "all_in_max": 1_500.00}],
                     fetch=pages, sleep=self.sleeps.append, emit=self.lines.append,
                     now=lambda: datetime.datetime(2026, 11, 23, 12, 0, 0), **kwargs)


class TestWatch(WatcherTestCase):
    def test_unchanged_lot_is_evaluated_once_however_many_rounds_run(self):
        pages = Pages(**{URL: [LIVE]})
        events = self.watch(pages, max_rounds=4, interval_s=300)
        self.assertEqual(len(pages.fetches), 4)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.ledger.summary()["calls"], 1)
        self.assertEqual(self.sleeps, [300, 300, 300])

    def test_price_move_is_re_evaluated_and_a_flipped_verdict_is_called_out(self):
        pages = Pages(**{URL: [LIVE, HIGHER, TOO_HIGH]})
        events = self.watch(pages, max_rounds=3)
        self.assertEqual([e["current_price"] for e in events], [800.00, 900.00, 1400.00])
        self.assertEqual([e["verdict"] for e in events], ["BID", "BID", "NO_BID"])
        self.assertEqual([e["verdict_changed"] for e in events], [False, False, True])
        self.assertEqual([e["round"] for e in events], [1, 2, 3])
        self.assertEqual(events[0]["bid_amount"], 1166.00)
        self.assertTrue(events[0]["bid_is_proxy"])
        self.assertIsNone(events[2]["bid_amount"])
        self.assertEqual(self.ledger.summary()["calls"], 3)
        self.assertIn("VERDICT: BID", self.lines[0])                 # first look: full card
        self.assertIn("now $900.00, still BID (DRY_RUN)", self.lines[1])
        self.assertIn("VERDICT CHANGED", self.lines[2])
        self.assertIn("VERDICT: NO_BID", self.lines[2])

    def test_watching_stops_by_itself_once_every_lot_has_ended(self):
        pages = Pages(**{URL: [LIVE, ENDED]})
        events = self.watch(pages)   # no max_rounds: must end on its own
        self.assertEqual([e["status"] for e in events], ["live", "ended"])
        self.assertEqual(len(pages.fetches), 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_an_ended_lot_is_dropped_while_the_others_keep_being_watched(self):
        pages = Pages(**{URL: [ENDED], OTHER: [LIVE, HIGHER]})
        events = self.watch(pages, max_rounds=3,
                            lots=[{"url": URL, "all_in_max": 1_500.00},
                                  {"url": OTHER, "all_in_max": 1_500.00}])
        self.assertEqual(pages.fetches, [URL, OTHER, OTHER, OTHER])
        self.assertEqual([(e["url"], e["status"]) for e in events],
                         [(URL, "ended"), (OTHER, "live"), (OTHER, "live")])

    def test_a_failed_fetch_is_reported_and_tried_again_next_round(self):
        pages = Pages(**{URL: [FetchError("PropertyRoom returned HTTP 503"), LIVE]})
        events = self.watch(pages, max_rounds=2)
        self.assertIn("HTTP 503", self.lines[0])
        self.assertEqual([e["round"] for e in events], [2])

    def test_rounds_are_never_closer_together_than_the_floor(self):
        self.watch(Pages(**{URL: [LIVE]}), max_rounds=2, interval_s=1)
        self.assertEqual(self.sleeps, [MIN_INTERVAL_S])

    def test_lot_without_a_max_is_watched_but_never_bid_on(self):
        events = self.watch(Pages(**{URL: [LIVE]}), max_rounds=1, lots=[{"url": URL}])
        self.assertEqual(events[0]["verdict"], "HOLD_FOR_HUMAN")
        self.assertIsNone(events[0]["bid_amount"])

    def test_watching_stops_when_the_model_budget_would_be_crossed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = CostLedger(os.path.join(tmp.name, "tiny.jsonl"), budget_usd=0.0001)
        calls = []

        def transport(url, body, headers, timeout_s):
            calls.append(url)
            return {"choices": [{"message": {"content": json.dumps(model_read(parse_lot_text(LIVE)))}}],
                    "usage": {"prompt_tokens": 4000, "completion_tokens": 500}}

        self.agent = BiddingAgent(
            ledger=ledger, tax_rate=0.10, pickup_cost_usd=0.0,
            ember=EmberClient(ledger=ledger, api_key="k", transport=transport))
        events = self.watch(Pages(**{URL: [LIVE]}), max_rounds=5)
        self.assertEqual(events, [])
        self.assertEqual(calls, [])
        self.assertEqual(self.sleeps, [])
        self.assertIn("Stopping", self.lines[0])


if __name__ == "__main__":
    unittest.main()
