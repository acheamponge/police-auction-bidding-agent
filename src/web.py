"""The demo web UI for the agent. Standard library only.

`python3 app.py` serves a small multi-page site on http://127.0.0.1:8765:

    /          Overview      what the agent does and where it stands
    /demo      Demo          a live auction and the four demo lots: the listing,
                             the command-line run and the UI run, side by side
    /lots      Lots          evaluate and watch your own lots
    /policy    Policy        the spending leash and your assumptions
    /ledger    Ledger        every model call against the $10
    /method    Method        how every verdict is made
    /cli       Command line  using the agent without the UI (from the README)

Every page is the same shell (web/shell.html) around one fragment from
web/pages/. The list of lots is your watchlist.json, the same file watch.py
reads.

It is a local demo, not a public site, and it is built that way:
  - it listens on 127.0.0.1 only, and refuses requests addressed to any
    other host name (so a web page elsewhere cannot reach it by pointing a
    domain at your machine);
  - changes are accepted only as JSON posted from its own pages;
  - the only pages it will fetch are PropertyRoom lot pages;
  - it is dry-run only. Nothing here can place a bid;
  - your API key stays in this process. The pages are only told whether a
    live model is in use.

Money is only spent on model calls, and the ledger's budget wall applies to
every one of them. Ember's read of a page is kept for as long as the page's
price, status and fees stay the same, so changing your max, the tax rate or
the pickup cost, or re-running the demo, re-works verdicts without another
call.
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import mini_markdown
from .agent import AgentResult, BiddingAgent
from .cost_ledger import BudgetExceeded
from .demo import (DEMO_PICKUP_COST_USD, DEMO_TAX_RATE, cli_command, demo_agent,
                   evaluate_demo_lot, find_live_lot, transcript_footer, transcript_header,
                   transcript_lot)
from .demo_lots import DEMO_LOTS
from .ember_client import EmberError
from .lot_fetcher import FetchError, fetch_and_parse, fetch_lot_page
from .watcher import snapshot

LOT_HOSTS = ("www.propertyroom.com", "propertyroom.com")
SHIPPING_PREFERENCES = ("cheapest", "pickup", "standard", "expedited")
PAGES = {
    "/": ("overview", "Overview"),
    "/demo": ("demo", "Demo"),
    "/lots": ("lots", "Lots"),
    "/policy": ("policy", "Policy"),
    "/ledger": ("ledger", "Ledger"),
    "/method": ("method", "Method"),
    "/cli": ("cli", "Command line"),
}
ASSETS = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
# The README sections shown on the Command line page.
README_SECTIONS = ("Run it from the command line", "Files")
MAX_BODY_BYTES = 10_000
# Scripts and data come only from this server. The two exceptions are the
# web fonts, and data: images for the small arrow drawn inside select boxes.
CONTENT_SECURITY_POLICY = ("default-src 'self'; "
                           "style-src 'self' https://fonts.googleapis.com; "
                           "font-src https://fonts.gstatic.com; "
                           "img-src 'self' data:")


class BadRequest(ValueError):
    """Something the person can fix: shown to them as-is."""


def check_lot_url(url) -> str:
    """Return the lot link in a standard form, or refuse it. Only PropertyRoom
    lot pages pass, so this server cannot be made to fetch anything else."""
    if not isinstance(url, str):
        raise BadRequest("Paste a PropertyRoom lot link.")
    parts = urllib.parse.urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        port = -1
    is_lot = parts.path.startswith("/l/") or parts.path.lower() == "/listing.aspx"
    if (parts.scheme != "https" or parts.hostname not in LOT_HOSTS
            or port is not None or parts.username is not None or not is_lot):
        raise BadRequest("That is not a PropertyRoom lot link. It should look like "
                         "https://www.propertyroom.com/l/item-name/12345678")
    query = parts.query if parts.path.lower() == "/listing.aspx" else ""
    return urllib.parse.urlunsplit(("https", "www.propertyroom.com", parts.path, query, ""))


def _amount(value, name: str, allow_none: bool = True, maximum: float = 1_000_000) -> float | None:
    """A dollar amount (or rate) from the page's JSON."""
    if value is None or value == "":
        if allow_none:
            return None
        raise BadRequest(f"{name} is required.")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadRequest(f"{name} must be a number.")
    if not 0 <= value <= maximum:
        raise BadRequest(f"{name} must be between 0 and {maximum:,.0f}.")
    return float(value)


def _result_view(result: AgentResult | None) -> dict | None:
    if result is None:
        return None
    return {"evaluation": dataclasses.asdict(result.evaluation),
            "policy_decision": result.policy_decision,
            "policy_note": result.policy_note}


# What the Demo page shows of a listing: the lot page's own words, as read
# when the demo ran.
LISTING_FIELDS = ("title", "status", "ends_text", "current_price", "bid_increment",
                  "premium_pct", "premium_absent", "reserve_not_met", "condition",
                  "shipping_options")


def _demo_entry(lot: dict, live_now: bool = False) -> dict:
    return {"lot": lot, "live_now": live_now, "snapshot": None, "reads": {}, "listed": None,
            "as_if_live": None, "error": None, "listing": None, "fetched_at": None,
            "cli_output": None}


def render_page(static_dir: str, name: str, title: str) -> bytes:
    """One page: the shared shell around its fragment."""
    with open(os.path.join(static_dir, "shell.html"), encoding="utf-8") as fh:
        shell = fh.read()
    with open(os.path.join(static_dir, "pages", name + ".html"), encoding="utf-8") as fh:
        content = fh.read()
    if "{{readme}}" in content:
        with open(os.path.join(static_dir, os.pardir, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        shown = "\n".join(f"<h2>{html.escape(heading)}</h2>\n"
                          + mini_markdown.render(mini_markdown.section(readme, heading))
                          for heading in README_SECTIONS)
        content = content.replace("{{readme}}", shown)
    page = (shell.replace("{{title}}", html.escape(title))
            .replace("{{page}}", name).replace("{{content}}", content))
    return page.encode("utf-8")


class WebApp:
    """The UI's state and what it can do, independent of HTTP."""

    def __init__(self, agent: BiddingAgent, watchlist_path: str,
                 fetch=fetch_and_parse, now=datetime.datetime.now,
                 fetch_html=fetch_lot_page):
        self.agent = agent
        self.watchlist_path = watchlist_path
        self.fetch = fetch            # a lot page -> (parsed, text)
        self.fetch_html = fetch_html  # any PropertyRoom page -> HTML
        self.now = now
        self.lock = threading.Lock()
        self.lots = []
        if os.path.exists(watchlist_path):
            with open(watchlist_path, encoding="utf-8") as fh:
                for entry in json.load(fh):
                    self.lots.append(self._new_lot(check_lot_url(entry["url"]),
                                                   entry.get("all_in_max")))
        # The demo has its own fixed assumptions, and shares the ledger and
        # the model client.
        self.demo_agent = demo_agent(agent.ledger, agent.ember)
        self.demo = [_demo_entry(lot) for lot in DEMO_LOTS]
        self.demo_live = None         # an auction found running when the demo ran
        self.demo_ran_at = None

    # -- state ------------------------------------------------------------
    @staticmethod
    def _new_lot(url: str, all_in_max: float | None) -> dict:
        return {"url": url, "all_in_max": all_in_max, "page": None, "ember_result": None,
                "result": None, "error": None, "checked_at": None, "used_model": False}

    def _find(self, url) -> dict:
        url = check_lot_url(url)
        for lot in self.lots:
            if lot["url"] == url:
                return lot
        raise BadRequest("That lot is not in the list any more. Reload the page.")

    def _save_watchlist(self) -> None:
        entries = [{"url": lot["url"], **({"all_in_max": lot["all_in_max"]}
                                           if lot["all_in_max"] is not None else {})}
                   for lot in self.lots]
        with open(self.watchlist_path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
            fh.write("\n")

    def state(self) -> dict:
        policy = self.agent.policy_state
        return {
            "mode": "live" if self.agent.ember.live else "fixture",
            "model": self.agent.ember.model,
            "settings": {
                "tax_rate": self.agent.tax_rate,
                "pickup_cost_usd": self.agent.pickup_cost_usd,
                "shipping_preference": self.agent.shipping_preference,
            },
            "policy": {
                "tx_cap_usd": policy.config.tx_cap_usd,
                "session_budget_usd": policy.config.session_budget_usd,
                "allow_reserve_lots": policy.config.allow_reserve_lots,
                "live_bidding_configured": policy.config.live_bidding_configured,
                "exposure_usd": policy.exposure_usd,
                "headroom_usd": policy.headroom_usd,
            },
            "ledger": self.agent.ledger.summary(),
            "ledger_entries": self.agent.ledger.entries(100),
            "lots": [self._public(lot) for lot in self.lots],
            "demo": {
                "tax_rate": DEMO_TAX_RATE,
                "pickup_cost_usd": DEMO_PICKUP_COST_USD,
                "ran_at": self.demo_ran_at,
                "lots": [{"label": entry["lot"]["label"], "url": entry["lot"]["url"],
                          "all_in_max": entry["lot"]["all_in_max"], "error": entry["error"],
                          "live_now": entry["live_now"],
                          "listing": entry["listing"], "fetched_at": entry["fetched_at"],
                          "cli": {"command": cli_command(entry["lot"], self.agent.ember.live),
                                  "output": entry["cli_output"]},
                          "listed": _result_view(entry["listed"]),
                          "as_if_live": _result_view(entry["as_if_live"])}
                         for entry in self._demo_entries()],
            },
        }

    @staticmethod
    def _public(lot: dict) -> dict:
        return {"url": lot["url"], "all_in_max": lot["all_in_max"],
                "checked_at": lot["checked_at"], "used_model": lot["used_model"],
                "error": lot["error"],
                **(_result_view(lot["result"]) or
                   {"evaluation": None, "policy_decision": None, "policy_note": None})}

    # -- working out a verdict ---------------------------------------------
    def _evaluate(self, lot: dict, refetch: bool) -> None:
        """Bring one lot's verdict up to date. With refetch the page is read
        again; Ember is only called when the page changed since its last read."""
        lot["error"] = None
        lot["used_model"] = False
        try:
            if refetch or lot["page"] is None:
                page = self.fetch(lot["url"])
                if lot["page"] is None or snapshot(page[0]) != snapshot(lot["page"][0]):
                    lot["ember_result"] = None
                lot["page"] = page
                lot["checked_at"] = self.now().isoformat(timespec="seconds")
            parsed, lot_text = lot["page"]
            lot["used_model"] = lot["ember_result"] is None and self.agent.ember.live
            lot["result"] = self.agent.evaluate_page(
                parsed, lot_text, lot["url"], lot["all_in_max"],
                ember_result=lot["ember_result"])
            lot["ember_result"] = lot["result"].ember_result
        except (FetchError, EmberError, BudgetExceeded) as exc:
            # Drop the earlier verdict too: a stale BID next to an error
            # would be worse than no verdict.
            lot["used_model"] = False
            lot["result"] = None
            lot["error"] = str(exc)

    # -- what the pages can ask for -------------------------------------------
    def add_lot(self, url, all_in_max) -> None:
        url = check_lot_url(url)
        all_in_max = _amount(all_in_max, "The all-in max")
        if any(lot["url"] == url for lot in self.lots):
            raise BadRequest("That lot is already in your list.")
        lot = self._new_lot(url, all_in_max)
        self.lots.insert(0, lot)
        self._save_watchlist()
        self._evaluate(lot, refetch=True)

    def remove_lot(self, url) -> None:
        self.lots.remove(self._find(url))
        self._save_watchlist()

    def set_max(self, url, all_in_max) -> None:
        lot = self._find(url)
        lot["all_in_max"] = _amount(all_in_max, "The all-in max")
        self._save_watchlist()
        self._evaluate(lot, refetch=False)

    def check(self, url) -> None:
        self._evaluate(self._find(url), refetch=True)

    def check_all(self) -> None:
        for lot in self.lots:
            ended = lot["result"] is not None and lot["result"].evaluation.status == "ended"
            if not ended:
                self._evaluate(lot, refetch=True)

    def update_settings(self, tax_rate, pickup_cost_usd, shipping_preference) -> None:
        tax_rate = _amount(tax_rate, "The tax rate", maximum=0.25)
        pickup_cost_usd = _amount(pickup_cost_usd, "The pickup cost")
        if shipping_preference not in SHIPPING_PREFERENCES:
            raise BadRequest("Pick a delivery preference from the list.")
        self.agent.tax_rate = tax_rate
        self.agent.pickup_cost_usd = pickup_cost_usd
        self.agent.shipping_preference = shipping_preference
        for lot in self.lots:
            if lot["page"] is not None:
                self._evaluate(lot, refetch=False)

    def _demo_entries(self) -> list:
        return ([self.demo_live] if self.demo_live else []) + self.demo

    def run_demo(self) -> None:
        """Run the demo: an auction that is live right now, then the fixed
        demo lots, each as listed and (once ended) as-if-live. For each lot
        this keeps what its page says, what the command line would print,
        and the verdict the pages show."""
        # Keep the same live auction between runs until it ends.
        still_live = (self.demo_live is not None and self.demo_live["listed"] is not None
                      and self.demo_live["listed"].evaluation.status == "live")
        if not still_live:
            found = find_live_lot(self.fetch_html, self.fetch,
                                  skip=tuple(lot["url"] for lot in DEMO_LOTS))
            self.demo_live = _demo_entry(found, live_now=True) if found else None

        live_model = self.agent.ember.live
        for entry in self._demo_entries():
            lot = entry["lot"]
            entry["error"] = None
            try:
                parsed, lot_text = self.fetch(lot["url"])
                if snapshot(parsed) != entry["snapshot"]:
                    entry["reads"] = {}
                listed, live = evaluate_demo_lot(self.demo_agent, lot, lot_text,
                                                 entry["reads"], self.now())
                entry.update(snapshot=snapshot(parsed), listed=listed, as_if_live=live,
                             listing={field: parsed.get(field) for field in LISTING_FIELDS},
                             fetched_at=self.now().isoformat(timespec="seconds"),
                             reads={"listed": listed.ember_result,
                                    "as_if_live": live.ember_result if live else None})
            except (FetchError, EmberError, BudgetExceeded) as exc:
                entry.update(error=str(exc), listed=None, as_if_live=None)
            # Exactly what the command shown beside it prints for this run.
            entry["cli_output"] = "\n".join(
                transcript_header(live_model, self.agent.ember.model)
                + transcript_lot(lot["url"], lot["all_in_max"], entry["listed"],
                                 entry["as_if_live"], entry["error"])
                + transcript_footer(self.agent.ledger.summary(), "cost_ledger.jsonl"))
        self.demo_ran_at = self.now().isoformat(timespec="seconds")

    # -- HTTP -----------------------------------------------------------------
    def dispatch(self, method: str, path: str, headers: dict, body: bytes,
                 allowed_hosts: tuple, static_dir: str) -> tuple[int, str, bytes]:
        """Answer one request: (status, content type, body). `headers` has
        lower-case names."""
        host = headers.get("host", "")
        if host not in allowed_hosts:
            return self._json(403, {"error": "This server only answers on its own local address."})
        path = urllib.parse.urlsplit(path).path

        if method == "GET":
            if path in PAGES:
                name, title = PAGES[path]
                return 200, "text/html; charset=utf-8", render_page(static_dir, name, title)
            if path in ASSETS:
                name, content_type = ASSETS[path]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return 200, content_type, fh.read()
            if path == "/api/state":
                with self.lock:
                    return self._json(200, self.state())
            return self._json(404, {"error": "Not found."})

        if method != "POST":
            return self._json(405, {"error": "Method not allowed."})
        origin = headers.get("origin")
        if origin is not None and origin != f"http://{host}":
            return self._json(403, {"error": "Requests must come from this page."})
        if headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "Send JSON."})
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "The request was not valid JSON."})

        actions = {
            "/api/lots": lambda: self.add_lot(data.get("url"), data.get("all_in_max")),
            "/api/lots/remove": lambda: self.remove_lot(data.get("url")),
            "/api/lots/max": lambda: self.set_max(data.get("url"), data.get("all_in_max")),
            "/api/lots/check": lambda: self.check(data.get("url")),
            "/api/lots/check-all": self.check_all,
            "/api/settings": lambda: self.update_settings(
                data.get("tax_rate"), data.get("pickup_cost_usd"),
                data.get("shipping_preference")),
            "/api/demo/run": self.run_demo,
        }
        if path not in actions:
            return self._json(404, {"error": "Not found."})
        with self.lock:
            try:
                actions[path]()
            except BadRequest as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, self.state())

    @staticmethod
    def _json(status: int, payload: dict) -> tuple[int, str, bytes]:
        return status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")


def make_handler(app: WebApp, allowed_hosts: tuple, static_dir: str):
    class Handler(BaseHTTPRequestHandler):
        def _answer(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                status, content_type, payload = WebApp._json(413, {"error": "Request too large."})
            else:
                body = self.rfile.read(length) if length else b""
                headers = {name.lower(): value for name, value in self.headers.items()}
                status, content_type, payload = app.dispatch(
                    method, self.path, headers, body, allowed_hosts, static_dir)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 — http.server's naming
            self._answer("GET")

        def do_POST(self):  # noqa: N802
            self._answer("POST")

        def log_message(self, format, *args):  # noqa: A002 — quiet by default
            pass

    return Handler


def serve(app: WebApp, port: int, static_dir: str) -> ThreadingHTTPServer:
    """Build the server, bound to this machine only. The caller runs it."""
    allowed_hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, allowed_hosts, static_dir))
