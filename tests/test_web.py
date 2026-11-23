"""Tests for the local web UI's server side. No network, no key, no sockets:
requests go straight to WebApp.dispatch.

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
from src import mini_markdown
from src.demo_lots import DEMO_LOTS
from src.web import PAGES, BadRequest, WebApp, check_lot_url

LOT = "https://www.propertyroom.com/l/2012-honda-civic-test-lot/11111111"   # not one of the demo lots
OTHER = "https://www.propertyroom.com/l/skullcandy-headphones/19039373"
LIVE = html_to_text(read_fixture("lot_page.html"))            # live at $800.00, 15% premium
HIGHER = LIVE.replace("US $800.00", "US $900.00")
ENDED = LIVE.replace("Ends Nov 30, 2026", "Ended Nov 30, 2026")
STATIC = os.path.join(os.path.dirname(__file__), "..", "web")
HOSTS = ("127.0.0.1:8765", "localhost:8765")


class WebTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.watchlist = os.path.join(self.dir, "watchlist.json")
        self.pages = {LOT: LIVE, OTHER: LIVE}
        for demo_lot in DEMO_LOTS:
            self.pages[demo_lot["url"]] = ENDED
        self.fetches = []
        self.model_calls = []
        self.listing_html = "<html>no lots listed</html>"   # PropertyRoom's vehicles listing
        self.html_fetches = []

    def fetch_html(self, url):
        self.html_fetches.append(url)
        if isinstance(self.listing_html, Exception):
            raise self.listing_html
        return self.listing_html

    def list_lots(self, *urls):
        self.listing_html = "".join(
            f'<a href="{url.replace("https://www.propertyroom.com", "")}">lot</a>' for url in urls)

    def fetch(self, url):
        self.fetches.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return parse_lot_text(page), page

    def transport(self, url, body, headers, timeout_s):
        self.model_calls.append(body)
        page = body["messages"][1]["content"]
        price = 900.00 if "US $900.00" in page else 800.00
        reply = model_read(parse_lot_text(LIVE), current_price=price,
                           estimated_value_usd={"low": 1_200, "high": 2_000, "basis": "Comparable cars."})
        return {"choices": [{"message": {"content": json.dumps(reply)}}],
                "usage": {"prompt_tokens": 4000, "completion_tokens": 500}}

    def app(self, live=True, **agent_kwargs):
        ledger = CostLedger(os.path.join(self.dir, "ledger.jsonl"))
        ember = EmberClient(ledger=ledger, api_key="test-key" if live else "",
                            transport=self.transport)
        agent = BiddingAgent(ledger=ledger, ember=ember,
                             **{"tax_rate": 0.10, "pickup_cost_usd": 0.0, **agent_kwargs})
        return WebApp(agent, self.watchlist, fetch=self.fetch, fetch_html=self.fetch_html,
                      now=lambda: datetime.datetime(2026, 11, 23, 12, 0, 0))

    def saved(self):
        with open(self.watchlist, encoding="utf-8") as fh:
            return json.load(fh)


class TestLotUrl(unittest.TestCase):
    def test_lot_links_are_accepted_and_put_in_one_form(self):
        self.assertEqual(check_lot_url("  " + LOT + "?utm=x#photos "), LOT)
        self.assertEqual(check_lot_url("https://propertyroom.com/l/a-thing/123"),
                         "https://www.propertyroom.com/l/a-thing/123")
        self.assertEqual(check_lot_url("https://www.propertyroom.com/listing.aspx?l=123"),
                         "https://www.propertyroom.com/listing.aspx?l=123")

    def test_anything_that_is_not_a_propertyroom_lot_page_is_refused(self):
        for bad in ("http://www.propertyroom.com/l/a-thing/123",          # not https
                    "https://example.com/l/a-thing/123",                    # another site
                    "https://www.propertyroom.com.evil.example/l/a/1",      # look-alike host
                    "https://www.propertyroom.com@127.0.0.1/l/a/1",         # host is really 127.0.0.1
                    "https://user@www.propertyroom.com/l/a/1",
                    "https://www.propertyroom.com:8443/l/a/1",
                    "https://www.propertyroom.com:notaport/l/a/1",
                    "https://www.propertyroom.com/c/vehicles",              # not a lot
                    "https://www.propertyroom.com/",
                    "http://127.0.0.1:8765/api/state",
                    "file:///etc/passwd", "", None, 42):
            with self.subTest(url=bad):
                with self.assertRaises(BadRequest):
                    check_lot_url(bad)


class TestWebApp(WebTestCase):
    def test_adding_a_lot_evaluates_it_and_saves_the_watchlist(self):
        app = self.app()
        app.add_lot(LOT + "#photos", 1_500)
        lot = app.state()["lots"][0]
        self.assertEqual(lot["url"], LOT)
        self.assertEqual(lot["evaluation"]["verdict"], "BID")
        self.assertEqual(lot["evaluation"]["bid_amount"], 1166.0)
        self.assertTrue(lot["evaluation"]["bid_is_proxy"])
        self.assertEqual(lot["policy_decision"], "DRY_RUN")
        self.assertTrue(lot["used_model"])
        self.assertEqual(lot["checked_at"], "2026-11-23T12:00:00")
        self.assertEqual(self.saved(), [{"url": LOT, "all_in_max": 1500.0}])
        self.assertEqual((len(self.fetches), len(self.model_calls)), (1, 1))

    def test_newest_lot_is_listed_first_and_a_lot_without_a_max_saves_none(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        app.add_lot(OTHER, None)
        self.assertEqual([lot["url"] for lot in app.state()["lots"]], [OTHER, LOT])
        self.assertEqual(self.saved(), [{"url": OTHER}, {"url": LOT, "all_in_max": 1500.0}])
        self.assertEqual(app.state()["lots"][0]["evaluation"]["verdict"], "HOLD_FOR_HUMAN")
        self.assertEqual(app.state()["lots"][0]["evaluation"]["suggested_all_in_max"], 1200.0)

    def test_bad_input_is_refused_before_anything_is_fetched_or_saved(self):
        app = self.app()
        for url, all_in_max in (("https://example.com/l/a/1", 100), (LOT, -5), (LOT, "100"),
                                (LOT, True), (LOT, 2_000_000)):
            with self.subTest(url=url, all_in_max=all_in_max):
                with self.assertRaises(BadRequest):
                    app.add_lot(url, all_in_max)
        app.add_lot(LOT, 100)
        with self.assertRaises(BadRequest):
            app.add_lot(LOT, 200)   # already listed
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(self.saved(), [{"url": LOT, "all_in_max": 100.0}])

    def test_changing_the_max_reworks_the_verdict_without_a_fetch_or_a_model_call(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        app.set_max(LOT, 1_000)     # next bid's all-in is $1,099.20
        lot = app.state()["lots"][0]
        self.assertEqual(lot["evaluation"]["verdict"], "NO_BID")
        self.assertFalse(lot["used_model"])
        self.assertEqual((len(self.fetches), len(self.model_calls)), (1, 1))
        self.assertEqual(self.saved(), [{"url": LOT, "all_in_max": 1000.0}])
        app.set_max(LOT, None)
        self.assertEqual(app.state()["lots"][0]["evaluation"]["verdict"], "HOLD_FOR_HUMAN")
        self.assertEqual(self.saved(), [{"url": LOT}])

    def test_changing_the_assumptions_reworks_every_lot_for_free(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        app.update_settings(0.0825, None, "cheapest")   # no pickup cost: pickup-only lot holds
        state = app.state()
        self.assertEqual(state["settings"], {"tax_rate": 0.0825, "pickup_cost_usd": None,
                                             "shipping_preference": "cheapest"})
        self.assertEqual(state["lots"][0]["evaluation"]["verdict"], "HOLD_FOR_HUMAN")
        self.assertIn("pickup cost", state["lots"][0]["evaluation"]["reason"])
        app.update_settings(0.0825, 100, "pickup")
        evaluation = app.state()["lots"][0]["evaluation"]
        self.assertEqual(evaluation["verdict"], "BID")
        self.assertEqual(evaluation["shipping_cost"], 123.95)
        self.assertEqual(evaluation["tax_rate"], 0.0825)
        self.assertEqual((len(self.fetches), len(self.model_calls)), (1, 1))

    def test_assumptions_are_validated(self):
        app = self.app()
        for args in ((0.9, None, "cheapest"), (-0.1, None, "cheapest"), ("0.1", None, "cheapest"),
                     (0.1, -1, "cheapest"), (0.1, None, "teleport"), (0.1, None, None)):
            with self.subTest(args=args):
                with self.assertRaises(BadRequest):
                    app.update_settings(*args)
        self.assertEqual(app.state()["settings"]["tax_rate"], 0.10)

    def test_checking_again_only_calls_the_model_when_the_page_changed(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        app.check(LOT)
        self.assertFalse(app.state()["lots"][0]["used_model"])
        self.assertEqual((len(self.fetches), len(self.model_calls)), (2, 1))
        self.pages[LOT] = HIGHER
        app.check(LOT)
        lot = app.state()["lots"][0]
        self.assertTrue(lot["used_model"])
        self.assertEqual(lot["evaluation"]["current_price"], 900.00)
        self.assertEqual((len(self.fetches), len(self.model_calls)), (3, 2))

    def test_check_all_leaves_ended_lots_alone(self):
        self.pages[OTHER] = ENDED
        app = self.app(live=False)
        app.add_lot(LOT, 1_500)
        app.add_lot(OTHER, 1_500)
        self.fetches.clear()
        app.check_all()
        self.assertEqual(self.fetches, [LOT])

    def test_failed_check_shows_the_error_and_drops_the_old_verdict(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        self.pages[LOT] = FetchError("PropertyRoom returned HTTP 503 for the lot")
        app.check(LOT)
        lot = app.state()["lots"][0]
        self.assertIn("HTTP 503", lot["error"])
        self.assertIsNone(lot["evaluation"])
        self.assertIsNone(lot["policy_decision"])
        self.pages[LOT] = LIVE
        app.check(LOT)
        lot = app.state()["lots"][0]
        self.assertIsNone(lot["error"])
        self.assertEqual(lot["evaluation"]["verdict"], "BID")

    def test_model_budget_refusal_is_shown_on_the_lot(self):
        app = self.app()
        app.agent.ledger.budget_usd = 0.0001
        app.add_lot(LOT, 1_500)
        lot = app.state()["lots"][0]
        self.assertIn("budget", lot["error"])
        self.assertIsNone(lot["evaluation"])
        self.assertEqual(self.model_calls, [])

    def test_removing_a_lot(self):
        app = self.app(live=False)
        app.add_lot(LOT, 1_500)
        app.add_lot(OTHER, None)
        app.remove_lot(LOT)
        self.assertEqual([lot["url"] for lot in app.state()["lots"]], [OTHER])
        self.assertEqual(self.saved(), [{"url": OTHER}])
        with self.assertRaises(BadRequest):
            app.remove_lot(LOT)

    def test_saved_watchlist_is_loaded_but_nothing_is_fetched_or_spent_at_startup(self):
        with open(self.watchlist, "w", encoding="utf-8") as fh:
            json.dump([{"url": LOT, "all_in_max": 1500}, {"url": OTHER}], fh)
        app = self.app()
        lots = app.state()["lots"]
        self.assertEqual([(lot["url"], lot["all_in_max"], lot["evaluation"]) for lot in lots],
                         [(LOT, 1500, None), (OTHER, None, None)])
        self.assertEqual((self.fetches, self.model_calls), ([], []))

    def test_state_reports_the_mode_and_never_contains_the_api_key(self):
        live = self.app()
        live.add_lot(LOT, 1_500)
        self.assertEqual(live.state()["mode"], "live")
        self.assertNotIn("test-key", json.dumps(live.state()))
        self.assertEqual(self.app(live=False).state()["mode"], "fixture")
        self.assertEqual(live.state()["ledger"]["live_calls"], 1)
        self.assertEqual(live.state()["policy"]["session_budget_usd"], 2000.0)


class TestDemoAndLedger(WebTestCase):
    def test_demo_runs_each_lot_as_listed_and_ended_lots_as_if_live(self):
        self.pages[DEMO_LOTS[3]["url"]] = LIVE       # one demo lot is still live
        app = self.app(live=False)
        before = app.state()["demo"]
        self.assertIsNone(before["ran_at"])
        self.assertEqual([lot["listed"] for lot in before["lots"]], [None] * 4)
        self.assertEqual((before["tax_rate"], before["pickup_cost_usd"]), (0.10, 0.0))

        app.run_demo()
        demo = app.state()["demo"]
        self.assertEqual(demo["ran_at"], "2026-11-23T12:00:00")
        self.assertEqual([lot["label"] for lot in demo["lots"]], [lot["label"] for lot in DEMO_LOTS])
        ended = demo["lots"][0]
        self.assertEqual(ended["listed"]["evaluation"]["verdict"], "NO_BID")
        self.assertEqual(ended["listed"]["evaluation"]["status"], "ended")
        self.assertEqual(ended["as_if_live"]["evaluation"]["status"], "live")
        self.assertEqual(ended["as_if_live"]["evaluation"]["verdict"], "BID")
        still_live = demo["lots"][3]
        self.assertEqual(still_live["listed"]["evaluation"]["status"], "live")
        self.assertIsNone(still_live["as_if_live"])
        self.assertEqual(len(self.fetches), 4)

    def test_demo_uses_its_own_assumptions_not_yours(self):
        app = self.app(live=False, tax_rate=None, pickup_cost_usd=None)   # yours would hold everything
        app.run_demo()
        evaluation = app.state()["demo"]["lots"][0]["as_if_live"]["evaluation"]
        self.assertEqual((evaluation["tax_rate"], evaluation["pickup_cost_usd"]), (0.10, 0.0))
        self.assertEqual(evaluation["verdict"], "BID")

    def test_running_the_demo_again_costs_nothing_unless_a_page_changed(self):
        app = self.app()
        app.run_demo()
        first = len(self.model_calls)
        self.assertEqual(first, 8)                    # four lots, as listed and as-if-live
        app.run_demo()
        self.assertEqual(len(self.model_calls), first)
        self.pages[DEMO_LOTS[0]["url"]] = ENDED.replace("US $800.00", "US $900.00")
        app.run_demo()
        self.assertEqual(len(self.model_calls), first + 2)

    def test_a_demo_lot_that_cannot_be_fetched_shows_its_error_and_the_rest_still_run(self):
        self.pages[DEMO_LOTS[1]["url"]] = FetchError("PropertyRoom returned HTTP 503 for the lot")
        app = self.app(live=False)
        app.run_demo()
        lots = app.state()["demo"]["lots"]
        self.assertIn("HTTP 503", lots[1]["error"])
        self.assertIsNone(lots[1]["listed"])
        self.assertEqual(lots[2]["as_if_live"]["evaluation"]["verdict"], "BID")

    def test_state_carries_the_recent_ledger_entries_and_what_the_policy_has_left(self):
        app = self.app()
        app.add_lot(LOT, 1_500)
        state = app.state()
        entry = state["ledger_entries"][-1]
        self.assertEqual((entry["lot_url"], entry["simulated"]), (LOT, False))
        self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4000, 500))
        self.assertEqual(state["policy"]["headroom_usd"], 2000.0)
        self.assertEqual(state["policy"]["exposure_usd"], 0.0)


class TestDemoThreeViews(WebTestCase):
    """For each demo lot the page shows the live listing, the command-line
    run and the UI run. The first lot is an auction found running right now."""

    def test_an_auction_that_is_live_right_now_is_found_and_run_first(self):
        self.pages[OTHER] = ENDED
        self.list_lots(OTHER, LOT, "https://www.propertyroom.com/l/never-reached/3")
        app = self.app(live=False)
        app.run_demo()
        lots = app.state()["demo"]["lots"]
        self.assertEqual(len(lots), 5)
        live = lots[0]
        self.assertTrue(live["live_now"])
        self.assertEqual([lot["live_now"] for lot in lots[1:]], [False] * 4)
        self.assertEqual(live["url"], LOT)             # OTHER came first but has ended
        self.assertTrue(live["label"].endswith("live on PropertyRoom right now"))
        # next bid $850 costs $1,099.20 all-in; a quarter above, rounded up to $10
        self.assertEqual(live["all_in_max"], 1380.0)
        self.assertEqual(live["listed"]["evaluation"]["verdict"], "BID")
        self.assertIsNone(live["as_if_live"])
        self.assertEqual(self.html_fetches, ["https://www.propertyroom.com/c/vehicles"])

    def test_listing_is_what_the_lot_page_said_when_the_demo_ran(self):
        self.list_lots(LOT)
        app = self.app(live=False)
        self.assertIsNone(app.state()["demo"]["lots"][0]["listing"])
        app.run_demo()
        lot = app.state()["demo"]["lots"][0]
        self.assertEqual(lot["fetched_at"], "2026-11-23T12:00:00")
        listing = lot["listing"]
        self.assertEqual(listing["title"], "2007 Toyota Prius (Jamaica, NY 11430)")
        self.assertEqual((listing["status"], listing["ends_text"]),
                         ("live", "Ends Nov 30, 2026 at 05:00 PM (Eastern)"))
        self.assertEqual((listing["current_price"], listing["bid_increment"], listing["premium_pct"]),
                         (800.0, 50.0, 15.0))
        self.assertEqual(listing["condition"], "Untested - Sold as-is")
        self.assertIn({"method": "Customer Pick-up", "price_usd": 23.95, "available": True},
                      listing["shipping_options"])

    def test_command_line_view_is_the_command_and_what_it_prints_for_this_run(self):
        self.list_lots(LOT)
        app = self.app(live=False)
        before = app.state()["demo"]["lots"][0]["cli"]
        self.assertIsNone(before["output"])
        app.run_demo()
        live, ended = app.state()["demo"]["lots"][0], app.state()["demo"]["lots"][1]
        self.assertEqual(live["cli"]["command"], f"python3 run_demo.py --lot {LOT} --max 1380")
        output = live["cli"]["output"]
        self.assertIn("FIXTURE MODE", output)
        self.assertIn(f"LOT: {LOT}", output)
        self.assertIn("All-in max: $1,380.00", output)
        self.assertIn("VERDICT: BID", output)
        self.assertIn("Bid request (NOT SENT):", output)
        self.assertIn("COST LEDGER", output)
        self.assertIn("Ledger file: cost_ledger.jsonl", output)
        self.assertNotIn("AS-IF-LIVE", output)
        # An ended lot's transcript carries both passes, as the command prints them.
        self.assertIn("VERDICT: NO_BID", ended["cli"]["output"])
        self.assertIn(">>> AS-IF-LIVE", ended["cli"]["output"])
        self.assertEqual(ended["cli"]["command"],
                         f"python3 run_demo.py --lot {DEMO_LOTS[0]['url']} --max 13500")

    def test_command_names_the_live_model_and_the_transcript_says_so(self):
        self.list_lots(LOT)
        app = self.app()
        app.run_demo()
        cli = app.state()["demo"]["lots"][0]["cli"]
        self.assertTrue(cli["command"].endswith(" --max 1380 --live-ember"))
        self.assertIn("Mode: LIVE Muse Ember (muse-ember-1.0)", cli["output"])
        self.assertNotIn("test-key", cli["output"])

    def test_failed_lot_shows_the_failure_in_its_transcript(self):
        self.pages[DEMO_LOTS[1]["url"]] = FetchError("PropertyRoom returned HTTP 503 for the lot")
        app = self.app(live=False)
        app.run_demo()
        self.assertIn("FAILED to evaluate: PropertyRoom returned HTTP 503",
                      app.state()["demo"]["lots"][1]["cli"]["output"])

    def test_only_the_first_few_listed_lots_are_read_and_demo_lots_are_not_reused(self):
        ended = [f"https://www.propertyroom.com/l/ended-lot/{n}" for n in range(1, 9)]
        for url in ended:
            self.pages[url] = ENDED
        self.list_lots(DEMO_LOTS[0]["url"], *ended)
        app = self.app(live=False)
        app.run_demo()
        self.assertEqual(self.fetches[:5], ended[:5])          # five tries, the demo lot skipped
        self.assertEqual(len(self.fetches), 5 + 4)
        self.assertEqual(len(app.state()["demo"]["lots"]), 4)   # no live lot found

    def test_the_live_lot_is_kept_between_runs_until_it_ends(self):
        self.list_lots(LOT, OTHER)
        app = self.app(live=False)
        app.run_demo()
        app.run_demo()
        self.assertEqual(len(self.html_fetches), 1)             # no second search
        self.assertEqual(app.state()["demo"]["lots"][0]["url"], LOT)

        self.pages[LOT] = ENDED
        app.run_demo()                                          # it ended: shown as ended this once
        ended = app.state()["demo"]["lots"][0]
        self.assertEqual((ended["url"], ended["listed"]["evaluation"]["status"]), (LOT, "ended"))
        app.run_demo()                                          # then replaced by the next live one
        self.assertEqual(len(self.html_fetches), 2)
        self.assertEqual(app.state()["demo"]["lots"][0]["url"], OTHER)

    def test_demo_still_runs_when_the_listing_cannot_be_read(self):
        self.listing_html = FetchError("PropertyRoom returned HTTP 503")
        app = self.app(live=False)
        app.run_demo()
        lots = app.state()["demo"]["lots"]
        self.assertEqual(len(lots), 4)
        self.assertEqual(lots[0]["as_if_live"]["evaluation"]["verdict"], "BID")


class TestMiniMarkdown(unittest.TestCase):
    def test_section_is_cut_at_the_next_second_level_heading(self):
        text = "# T\n\n## One\nalpha\n\n### Sub\nbeta\n\n## Two\ngamma\n"
        self.assertEqual(mini_markdown.section(text, "One").strip(), "alpha\n\n### Sub\nbeta")
        self.assertEqual(mini_markdown.section(text, "Two").strip(), "gamma")
        self.assertEqual(mini_markdown.section(text, "Missing"), "")

    def test_readme_constructs_are_rendered(self):
        html = mini_markdown.render(
            "Intro with `code`, **bold** and a [link](https://example.com/x).\n"
            "Second line of the same paragraph.\n\n"
            "### Settings\n\n"
            "```bash\n# a comment\npython3 run.py --max 5 <file>\n```\n\n"
            "- first item\n  continued\n- second\n\n"
            "| Path | What |\n|---|---|\n| `a.py` | The **a** file |\n")
        self.assertIn("<p>Intro with <code>code</code>, <strong>bold</strong> and a "
                      '<a href="https://example.com/x" target="_blank" rel="noopener noreferrer">link</a>. '
                      "Second line of the same paragraph.</p>", html)
        self.assertIn("<h3>Settings</h3>", html)
        self.assertIn("<pre><code># a comment\npython3 run.py --max 5 &lt;file&gt;</code></pre>", html)
        self.assertIn("<ul><li>first item continued</li><li>second</li></ul>", html)
        self.assertIn('<table class="table"><thead><tr><th>Path</th><th>What</th></tr></thead>'
                      "<tbody><tr><td><code>a.py</code></td><td>The <strong>a</strong> file</td></tr></tbody></table>", html)

    def test_markup_in_the_source_is_escaped_not_passed_through(self):
        html = mini_markdown.render("<script>alert(1)</script> and [x](javascript:alert(1))\n\n"
                                    "| <b>h</b> |\n|---|\n| <img src=x onerror=y> |\n")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn("&lt;script&gt;", html)


class TestDispatch(WebTestCase):
    def request(self, app, method, path, body=None, host="127.0.0.1:8765", **headers):
        headers = {"host": host, **headers}
        if body is not None and "content-type" not in headers:
            headers["content-type"] = "application/json"
        raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else (body or b"")
        status, content_type, payload = app.dispatch(method, path, headers, raw, HOSTS, STATIC)
        return status, content_type, payload

    def test_every_page_is_the_shell_around_its_own_fragment(self):
        app = self.app(live=False)
        self.assertEqual(sorted(PAGES), ["/", "/cli", "/demo", "/ledger", "/lots", "/method", "/policy"])
        for path, (name, title) in PAGES.items():
            with self.subTest(path=path):
                status, content_type, payload = self.request(app, "GET", path)
                page = payload.decode("utf-8")
                self.assertEqual((status, content_type), (200, "text/html; charset=utf-8"))
                self.assertIn(f'<body data-page="{name}">', page)
                self.assertIn(f"<title>{title} · Police Auction Bidding Agent</title>", page)
                self.assertIn('data-nav="cli"', page)        # the shared nav
                self.assertNotIn("{{", page)                   # nothing left unfilled
                # The CSP forbids inline script and inline style.
                self.assertNotIn("<script>", page)
                self.assertNotIn("style=", page)
        self.assertEqual(self.request(app, "GET", "/app.js")[:2], (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.request(app, "GET", "/style.css")[:2], (200, "text/css; charset=utf-8"))

    def test_command_line_page_shows_the_readme_usage(self):
        app = self.app(live=False)
        page = self.request(app, "GET", "/cli")[2].decode("utf-8")
        self.assertIn("<h2>Run it from the command line</h2>", page)
        self.assertNotIn("then open http://127.0.0.1:8765", page)   # the UI's own section stays out
        self.assertIn("python3 run_demo.py", page)
        self.assertIn("python3 watch.py --watchlist watchlist.json", page)
        self.assertIn("<h3>Settings</h3>", page)
        self.assertIn("<code>config.json</code>", page)
        self.assertIn("<h2>Files</h2>", page)
        self.assertIn("<td><code>src/evaluator.py</code></td>", page)
        self.assertNotIn("Not built yet", page)        # other README sections stay out

    def test_only_the_listed_files_can_be_read(self):
        app = self.app(live=False)
        for path in ("/../README.md", "/..%2f.env", "/.env", "/app.py", "/index.html", "/shell.html",
                     "/pages/lots.html", "/api/nope"):
            with self.subTest(path=path):
                self.assertEqual(self.request(app, "GET", path)[0], 404)

    def test_requests_for_another_host_name_are_refused(self):
        app = self.app(live=False)
        for host in ("evil.example", "evil.example:8765", "127.0.0.1:9999", ""):
            with self.subTest(host=host):
                self.assertEqual(self.request(app, "GET", "/api/state", host=host)[0], 403)
                self.assertEqual(self.request(app, "POST", "/api/lots", {"url": LOT}, host=host)[0], 403)
        self.assertEqual(self.request(app, "GET", "/api/state", host="localhost:8765")[0], 200)
        self.assertEqual(self.fetches, [])

    def test_changes_must_be_json_from_this_page(self):
        app = self.app(live=False)
        body = {"url": LOT, "all_in_max": 1500}
        form = self.request(app, "POST", "/api/lots", body, **{"content-type": "text/plain"})
        foreign = self.request(app, "POST", "/api/lots", body, origin="https://evil.example")
        self.assertEqual((form[0], foreign[0]), (415, 403))
        self.assertEqual(self.fetches, [])
        own = self.request(app, "POST", "/api/lots", body, origin="http://127.0.0.1:8765",
                           **{"content-type": "application/json; charset=utf-8"})
        self.assertEqual(own[0], 200)
        self.assertEqual(json.loads(own[2])["lots"][0]["evaluation"]["verdict"], "BID")

    def test_every_action_is_reachable_and_returns_the_new_state(self):
        app = self.app(live=False)
        steps = (("/api/lots", {"url": LOT, "all_in_max": 1500}, lambda s: s["lots"][0]["evaluation"]["verdict"] == "BID"),
                 ("/api/lots/max", {"url": LOT, "all_in_max": 1000}, lambda s: s["lots"][0]["evaluation"]["verdict"] == "NO_BID"),
                 ("/api/settings", {"tax_rate": 0.05, "pickup_cost_usd": 0, "shipping_preference": "cheapest"},
                  lambda s: s["settings"]["tax_rate"] == 0.05),
                 ("/api/lots/check", {"url": LOT}, lambda s: len(self.fetches) == 2),
                 ("/api/lots/check-all", {}, lambda s: len(self.fetches) == 3),
                 ("/api/lots/remove", {"url": LOT}, lambda s: s["lots"] == []),
                 ("/api/demo/run", {}, lambda s: s["demo"]["ran_at"] == "2026-11-23T12:00:00"))
        for path, body, expect in steps:
            with self.subTest(path=path):
                status, _, payload = self.request(app, "POST", path, body)
                self.assertEqual(status, 200)
                self.assertTrue(expect(json.loads(payload)))

    def test_mistakes_come_back_as_messages_not_crashes(self):
        app = self.app(live=False)
        status, _, payload = self.request(app, "POST", "/api/lots", {"url": "https://example.com/x"})
        self.assertEqual(status, 400)
        self.assertIn("not a PropertyRoom lot link", json.loads(payload)["error"])
        self.assertEqual(self.request(app, "POST", "/api/lots", b"{not json")[0], 400)
        self.assertEqual(self.request(app, "POST", "/api/lots", [1, 2])[0], 400)
        self.assertEqual(self.request(app, "POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.request(app, "PUT", "/api/lots", {})[0], 405)


if __name__ == "__main__":
    unittest.main()
