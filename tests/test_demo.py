"""Tests for the pieces of the demo that the command line and the UI share.

Run with: python3 -m unittest discover -s tests
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import read_fixture
from src.demo import (as_if_live, cli_command, demo_max_for, find_live_lot, lot_links,
                      transcript_footer, transcript_header, transcript_lot)
from src.lot_fetcher import FetchError, html_to_text, parse_lot_text

LIVE = html_to_text(read_fixture("lot_page.html"))
ENDED = LIVE.replace("Ends Nov 30, 2026", "Ended Nov 30, 2026")


class TestAsIfLive(unittest.TestCase):
    def test_only_the_ended_line_changes(self):
        rewritten = as_if_live(ENDED, datetime.datetime(2026, 11, 23, 15, 30))
        self.assertIn("Ends Nov 25, 2026 at 03:30 PM (Eastern)", rewritten)
        self.assertNotIn("Ended Nov 30", rewritten)
        self.assertEqual(parse_lot_text(rewritten)["status"], "live")
        before, after = parse_lot_text(ENDED), parse_lot_text(rewritten)
        for field in ("current_price", "premium_pct", "bid_increment", "shipping_options"):
            self.assertEqual(before[field], after[field])

    def test_a_live_page_is_left_alone(self):
        self.assertEqual(as_if_live(LIVE), LIVE)


class TestFindLiveLot(unittest.TestCase):
    LISTING = ('<a href="/l/first-lot/1">a</a> <a href="/l/first-lot/1#photos">again</a> '
               '<a href="/c/vehicles?page=2">next</a> <a href="/l/second-lot/22?x=1">b</a> '
               '<a href="https://example.com/l/elsewhere/9">c</a> <a href="/l/third-lot/333">d</a>')

    def test_lot_links_are_the_listing_s_lots_once_each_in_order(self):
        self.assertEqual(lot_links(self.LISTING), [
            "https://www.propertyroom.com/l/first-lot/1",
            "https://www.propertyroom.com/l/second-lot/22",
            "https://www.propertyroom.com/l/third-lot/333"])

    def test_first_live_lot_wins_and_unreadable_ones_are_passed_over(self):
        pages = {"https://www.propertyroom.com/l/first-lot/1": FetchError("HTTP 503"),
                 "https://www.propertyroom.com/l/second-lot/22": ENDED,
                 "https://www.propertyroom.com/l/third-lot/333": LIVE}

        def fetch_lot(url):
            if isinstance(pages[url], Exception):
                raise pages[url]
            return parse_lot_text(pages[url]), pages[url]

        lot = find_live_lot(lambda url: self.LISTING, fetch_lot)
        self.assertEqual(lot["url"], "https://www.propertyroom.com/l/third-lot/333")
        self.assertEqual(lot["label"],
                         "2007 Toyota Prius (Jamaica, NY 11430) — live on PropertyRoom right now")
        self.assertEqual(lot["all_in_max"], 1380.0)
        skipped = find_live_lot(lambda url: self.LISTING, fetch_lot,
                                skip=("https://www.propertyroom.com/l/third-lot/333",))
        self.assertIsNone(skipped)

    def test_a_live_lot_with_a_buyers_premium_is_preferred_over_an_earlier_one_without(self):
        no_premium = html_to_text(read_fixture("lot_page.html").replace(
            "<div><span>Buyer&#39;s Premium</span> <span>Buyer&#39;s Premium is non-refundable.</span><div>15.00%</div></div>", ""))
        self.assertIsNone(parse_lot_text(no_premium)["premium_pct"])
        pages = {"https://www.propertyroom.com/l/first-lot/1": no_premium,
                 "https://www.propertyroom.com/l/second-lot/22": LIVE,
                 "https://www.propertyroom.com/l/third-lot/333": LIVE}
        fetched = []

        def fetch_lot(url):
            fetched.append(url)
            return parse_lot_text(pages[url]), pages[url]

        lot = find_live_lot(lambda url: self.LISTING, fetch_lot)
        self.assertEqual(lot["url"], "https://www.propertyroom.com/l/second-lot/22")
        self.assertEqual(len(fetched), 2)               # stops at the first one with a premium
        # With no premium lot among them, the first live one is used.
        pages["https://www.propertyroom.com/l/second-lot/22"] = no_premium
        pages["https://www.propertyroom.com/l/third-lot/333"] = no_premium
        self.assertEqual(find_live_lot(lambda url: self.LISTING, fetch_lot)["url"],
                         "https://www.propertyroom.com/l/first-lot/1")

    def test_demo_max_is_none_when_the_price_cannot_be_read(self):
        parsed = parse_lot_text(LIVE)
        self.assertEqual(demo_max_for(parsed), 1380.0)
        parsed["current_price"] = None
        self.assertIsNone(demo_max_for(parsed))


class TestTranscript(unittest.TestCase):
    def test_command_for_one_lot(self):
        lot = {"url": "https://www.propertyroom.com/l/a/1", "all_in_max": 1500.0}
        self.assertEqual(cli_command(lot, False),
                         "python3 run_demo.py --lot https://www.propertyroom.com/l/a/1 --max 1500")
        self.assertEqual(cli_command({**lot, "all_in_max": 399.5}, True),
                         "python3 run_demo.py --lot https://www.propertyroom.com/l/a/1 --max 399.5 --live-ember")
        self.assertEqual(cli_command({**lot, "all_in_max": None}, False),
                         "python3 run_demo.py --lot https://www.propertyroom.com/l/a/1")

    def test_header_says_which_mode_and_the_assumptions(self):
        fixture = "\n".join(transcript_header(False, "muse-ember-1.0"))
        live = "\n".join(transcript_header(True, "muse-ember-1.0"))
        self.assertIn("Mode: FIXTURE MODE", fixture)
        self.assertIn("Mode: LIVE Muse Ember (muse-ember-1.0)", live)
        self.assertIn("Tax assumption: 10%", live)
        self.assertIn("Pickup cost assumption: $0.00", live)

    def test_lot_block_without_a_result_reports_the_failure(self):
        block = "\n".join(transcript_lot("a lot", None, None, None, "HTTP 503"))
        self.assertIn("LOT: a lot", block)
        self.assertIn("All-in max: not given", block)
        self.assertIn("FAILED to evaluate: HTTP 503", block)

    def test_footer_prints_the_ledger(self):
        footer = "\n".join(transcript_footer({"calls": 3, "total_spent_usd": 0.002}, "cost_ledger.jsonl"))
        self.assertIn('"calls": 3', footer)
        self.assertIn("Ledger file: cost_ledger.jsonl", footer)
        self.assertIn("No bids were placed.", footer)


if __name__ == "__main__":
    unittest.main()
