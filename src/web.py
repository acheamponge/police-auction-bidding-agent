"""The demo web UI for the agent. Standard library only.

It is a small multi-page site:

    /          Overview      what the agent does and where it stands
    /demo      Demo          a live auction and the four demo lots: the listing,
                             the command-line run and the UI run, side by side
    /lots      Lots          evaluate and watch your own lots
    /policy    Policy        the spending leash and your assumptions
    /ledger    Ledger        every model call against the $10
    /method    Method        how every verdict is made
    /cli       Command line  using the agent without the UI (from the README)

Every page is the same shell (web/shell.html) around one fragment from
web/pages/. The stylesheet, the script and the recording of the real site
live in public/.

It runs two ways.

Local: `python3 ui.py` on http://127.0.0.1:8765. One person, one process.
The lots are your watchlist.json (the file watch.py reads) and the server
keeps each lot's verdict in memory. It listens on 127.0.0.1 only and refuses
requests addressed to any other host name, so a web page elsewhere cannot
reach it by pointing a domain at your machine.

Hosted: on Vercel (api/index.py), or `python3 ui.py --hosted` to try that
mode at home. There a request can land on any server instance and the disk is
read-only, so the server keeps nothing between requests. Each visitor's
lots, verdicts and assumptions live in their own browser and travel with
every request as a "session". That session is untrusted input: it is checked
field by field, and nothing in it can do more than change what that same
visitor is shown.

Either way:
  - changes are accepted only as JSON posted from the site's own pages;
  - the only pages it will fetch are PropertyRoom lot pages;
  - it is dry-run only. Nothing here can place a bid;
  - the API key stays in the server process. The pages are only told whether
    a live model is in use.

Money is only spent on model calls. Ember's read of a page is kept for as
long as the page's price, status and fees stay the same, so changing a max,
the tax rate or the pickup cost, or re-running the demo, re-works verdicts
without another call. Locally the ledger's budget wall covers every call.
Hosted, the ledger lives in an instance's temporary space and resets when the
instance does, so the wall is per instance; the firm limit there is the
credit on the key itself.
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import json
import os
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import mini_markdown
from .agent import AgentResult, BiddingAgent
from .cost_ledger import BudgetExceeded, CostLedger
from .demo import (DEMO_PICKUP_COST_USD, DEMO_TAX_RATE, cli_command, demo_agent,
                   evaluate_demo_lot, find_live_lot, transcript_footer, transcript_header,
                   transcript_lot)
from .demo_lots import DEMO_LOTS, demo_config_version
from .ember_client import EmberClient, EmberError
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
# Served from public/. On Vercel the platform serves that folder itself.
ASSETS = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    # Theme before first paint: an external file, because the CSP blocks
    # inline scripts (see public/theme.js).
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    # A recording of a real PropertyRoom lot page, so people can see the site.
    "/propertyroom.gif": ("propertyroom.gif", "image/gif"),
}
# The README sections shown on the Command line page.
README_SECTIONS = ("Run it from the command line", "Files")
MAX_BODY_BYTES = 10_000
# Hosted requests carry the visitor's session, so they are larger.
HOSTED_MAX_BODY_BYTES = 800_000
HOSTED_MAX_LOTS = 12
# What a hosted visitor starts with. Tax is still only an assumption, shown
# as one on every verdict, and they can change it on the Policy page.
HOSTED_DEFAULT_TAX_RATE = DEMO_TAX_RATE
SESSION_VERSION = 1
# Scripts and data come only from this server. The two exceptions are the
# web fonts, and data: images for the small arrow drawn inside select boxes.
# The lot's own photos are loaded straight from content.propertyroom.com —
# they are image files, not pages, so nothing here is framed or copied.
CONTENT_SECURITY_POLICY = ("default-src 'self'; "
                           "style-src 'self' https://fonts.googleapis.com; "
                           "font-src https://fonts.gstatic.com; "
                           "img-src 'self' data: https://content.propertyroom.com")
# What the Demo page shows of a listing: the lot page's own words, as read
# when the demo ran.
LISTING_FIELDS = ("title", "status", "ends_text", "current_price", "bid_increment",
                  "premium_pct", "premium_absent", "reserve_not_met", "condition",
                  "shipping_options", "photo_urls")
# What is kept about a lot and about a demo entry between hosted requests.
LOT_SESSION_FIELDS = ("snap", "ember_result", "view", "error", "checked_at", "used_model")
DEMO_SESSION_FIELDS = ("snap", "reads", "listed", "as_if_live", "error", "listing",
                       "fetched_at", "cli_output")


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


def _result_view(result: AgentResult) -> dict:
    """What the pages are shown of one evaluation."""
    return {"evaluation": dataclasses.asdict(result.evaluation),
            "policy_decision": result.policy_decision,
            "policy_note": result.policy_note}


def _view_status(view: dict | None) -> str | None:
    """The auction status in a stored view, however it got there."""
    evaluation = view.get("evaluation") if isinstance(view, dict) else None
    return evaluation.get("status") if isinstance(evaluation, dict) else None


def snap_key(parsed: dict) -> str:
    """The parts of a page that can change a verdict, as one string that
    survives a trip through the visitor's browser."""
    return json.dumps(snapshot(parsed))


def _model_read_or_none(value):
    """A stored model read is only reused if it still has the shape the
    evaluator expects. Anything else is dropped and the model is asked again."""
    if not isinstance(value, dict):
        return None
    for key, kind in (("judgment", dict), ("estimated_value_usd", dict),
                      ("shipping_options", list), ("_meta", dict)):
        if value.get(key) is not None and not isinstance(value[key], kind):
            return None
    return value


def _demo_entry(lot: dict, live_now: bool = False) -> dict:
    return {"lot": lot, "live_now": live_now, "snap": None, "reads": {}, "listed": None,
            "as_if_live": None, "error": None, "listing": None, "fetched_at": None,
            "cli_output": None}


def render_page(project_dir: str, name: str, title: str, hosted: bool = False) -> bytes:
    """One page: the shared shell around its fragment."""
    with open(os.path.join(project_dir, "web", "shell.html"), encoding="utf-8") as fh:
        shell = fh.read()
    with open(os.path.join(project_dir, "web", "pages", name + ".html"), encoding="utf-8") as fh:
        content = fh.read()
    if "{{readme}}" in content:
        with open(os.path.join(project_dir, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        shown = "\n".join(f"<h2>{html.escape(heading)}</h2>\n"
                          + mini_markdown.render(mini_markdown.section(readme, heading))
                          for heading in README_SECTIONS)
        content = content.replace("{{readme}}", shown)
    page = (shell.replace("{{title}}", html.escape(title)).replace("{{page}}", name)
            .replace("{{hosted}}", "true" if hosted else "false")
            .replace("{{content}}", content))
    return page.encode("utf-8")


class WebApp:
    """The UI's state and what it can do, independent of HTTP.

    Locally one WebApp lives as long as the server. Hosted, a new one is made
    for every request and filled from the visitor's session.
    """

    def __init__(self, agent: BiddingAgent, watchlist_path: str | None,
                 fetch=fetch_and_parse, now=datetime.datetime.now,
                 fetch_html=fetch_lot_page, hosted: bool = False):
        self.agent = agent
        self.watchlist_path = watchlist_path
        self.fetch = fetch            # a lot page -> (parsed, text)
        self.fetch_html = fetch_html  # any PropertyRoom page -> HTML
        self.now = now
        self.hosted = hosted
        self.lock = threading.Lock()
        self.lots = []
        if watchlist_path and os.path.exists(watchlist_path):
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
        return {"url": url, "all_in_max": all_in_max, "page": None, "snap": None,
                "ember_result": None, "view": None, "error": None, "checked_at": None,
                "used_model": False}

    def _find(self, url) -> dict:
        url = check_lot_url(url)
        for lot in self.lots:
            if lot["url"] == url:
                return lot
        raise BadRequest("That lot is not in the list any more. Reload the page.")

    def _save_watchlist(self) -> None:
        if not self.watchlist_path:
            return
        entries = [{"url": lot["url"], **({"all_in_max": lot["all_in_max"]}
                                           if lot["all_in_max"] is not None else {})}
                   for lot in self.lots]
        with open(self.watchlist_path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
            fh.write("\n")

    def _demo_entries(self) -> list:
        return ([self.demo_live] if self.demo_live else []) + self.demo

    def state(self) -> dict:
        policy = self.agent.policy_state
        return {
            "hosted": self.hosted,
            "mode": "live" if self.agent.ember.live else "fixture",
            "model": self.agent.ember.model,
            "settings": self._settings(),
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
                          "listed": entry["listed"], "as_if_live": entry["as_if_live"]}
                         for entry in self._demo_entries()],
            },
        }

    def _settings(self) -> dict:
        return {"tax_rate": self.agent.tax_rate,
                "pickup_cost_usd": self.agent.pickup_cost_usd,
                "shipping_preference": self.agent.shipping_preference}

    @staticmethod
    def _public(lot: dict) -> dict:
        return {"url": lot["url"], "all_in_max": lot["all_in_max"],
                "checked_at": lot["checked_at"], "used_model": lot["used_model"],
                "error": lot["error"],
                **(lot["view"] or {"evaluation": None, "policy_decision": None,
                                   "policy_note": None})}

    # -- the visitor's session (hosted) -------------------------------------
    def export_session(self) -> dict:
        """Everything a later request needs to pick up where this one left
        off. It goes to the visitor's browser and comes back with their next
        request."""
        def entry_out(entry):
            return {"lot": entry["lot"], "live_now": entry["live_now"],
                    **{field: entry[field] for field in DEMO_SESSION_FIELDS}}
        return {
            "version": SESSION_VERSION,
            "settings": self._settings(),
            "lots": [{"url": lot["url"], "all_in_max": lot["all_in_max"],
                      **{field: lot[field] for field in LOT_SESSION_FIELDS}}
                     for lot in self.lots],
            "demo": {"ran_at": self.demo_ran_at,
                     "config_version": demo_config_version(),
                     "live": entry_out(self.demo_live) if self.demo_live else None,
                     "entries": [entry_out(entry) for entry in self.demo]},
        }

    def load_session(self, session) -> None:
        """Take up a visitor's session. It comes from their browser, so every
        part is checked, and a part that does not check out is left behind
        rather than trusted."""
        if not isinstance(session, dict) or session.get("version") != SESSION_VERSION:
            return
        settings = session.get("settings")
        if isinstance(settings, dict):
            try:
                tax_rate = _amount(settings.get("tax_rate"), "The tax rate", maximum=0.25)
                pickup_cost = _amount(settings.get("pickup_cost_usd"), "The pickup cost")
                if settings.get("shipping_preference") in SHIPPING_PREFERENCES:
                    self.agent.tax_rate = tax_rate
                    self.agent.pickup_cost_usd = pickup_cost
                    self.agent.shipping_preference = settings["shipping_preference"]
            except BadRequest:
                pass

        self.lots = []
        raw_lots = session.get("lots")
        for raw in (raw_lots if isinstance(raw_lots, list) else [])[:HOSTED_MAX_LOTS]:
            if not isinstance(raw, dict):
                continue
            try:
                lot = self._new_lot(check_lot_url(raw.get("url")),
                                    _amount(raw.get("all_in_max"), "The all-in max"))
            except BadRequest:
                continue
            if any(known["url"] == lot["url"] for known in self.lots):
                continue
            self._restore(lot, raw)
            lot["ember_result"] = _model_read_or_none(raw.get("ember_result"))
            lot["used_model"] = raw.get("used_model") is True
            lot["checked_at"] = raw["checked_at"] if isinstance(raw.get("checked_at"), str) else None
            self.lots.append(lot)

        demo = session.get("demo")
        if not isinstance(demo, dict):
            return
        # A stored demo run belongs to the demo lots as they were. If the
        # lots have changed since (a lot added, a max changed), the stored
        # results are stale — leave them behind and the visitor runs the
        # demo fresh against the new list. Sessions stored before versioning
        # have no config_version, so they are stale by definition.
        if demo.get("config_version") != demo_config_version():
            return
        self.demo_ran_at = demo["ran_at"] if isinstance(demo.get("ran_at"), str) else None
        # The fixed demo lots are the server's own; only what was learned
        # about them comes back from the browser.
        raw_entries = demo.get("entries")
        by_url = {raw["lot"]["url"]: raw
                  for raw in (raw_entries if isinstance(raw_entries, list) else [])
                  if isinstance(raw, dict) and isinstance(raw.get("lot"), dict)
                  and isinstance(raw["lot"].get("url"), str)}
        for entry in self.demo:
            if entry["lot"]["url"] in by_url:
                self._restore_demo(entry, by_url[entry["lot"]["url"]])
        live = demo.get("live")
        if isinstance(live, dict) and isinstance(live.get("lot"), dict):
            try:
                lot = {"url": check_lot_url(live["lot"].get("url")),
                       "label": str(live["lot"].get("label") or "")[:200] or "A live auction",
                       "all_in_max": _amount(live["lot"].get("all_in_max"), "The all-in max")}
            except BadRequest:
                return
            self.demo_live = _demo_entry(lot, live_now=True)
            self._restore_demo(self.demo_live, live)

    @staticmethod
    def _restore(target: dict, raw: dict) -> None:
        """Copy the parts of a stored lot that are simply shown back to the
        same visitor: their type is checked, their content is not trusted."""
        target["snap"] = raw["snap"] if isinstance(raw.get("snap"), str) else None
        target["view"] = raw["view"] if isinstance(raw.get("view"), dict) else None
        target["error"] = raw["error"] if isinstance(raw.get("error"), str) else None

    @staticmethod
    def _restore_demo(entry: dict, raw: dict) -> None:
        entry["snap"] = raw["snap"] if isinstance(raw.get("snap"), str) else None
        reads = raw.get("reads") if isinstance(raw.get("reads"), dict) else {}
        entry["reads"] = {key: _model_read_or_none(reads.get(key))
                          for key in ("listed", "as_if_live")}
        for field in ("listed", "as_if_live", "listing"):
            entry[field] = raw[field] if isinstance(raw.get(field), dict) else None
        for field in ("error", "fetched_at", "cli_output"):
            entry[field] = raw[field] if isinstance(raw.get(field), str) else None

    # -- working out a verdict ---------------------------------------------
    def _evaluate(self, lot: dict, refetch: bool) -> None:
        """Bring one lot's verdict up to date. With refetch (or when the page
        is not in memory) the page is read again; Ember is only called when
        the page changed since its last read."""
        lot["error"] = None
        lot["used_model"] = False
        try:
            if refetch or lot["page"] is None:
                page = self.fetch(lot["url"])
                key = snap_key(page[0])
                if key != lot["snap"]:
                    lot["ember_result"] = None
                lot["page"], lot["snap"] = page, key
                lot["checked_at"] = self.now().isoformat(timespec="seconds")
            parsed, lot_text = lot["page"]
            lot["used_model"] = lot["ember_result"] is None and self.agent.ember.live
            result = self.agent.evaluate_page(parsed, lot_text, lot["url"], lot["all_in_max"],
                                              ember_result=lot["ember_result"])
            lot["ember_result"] = result.ember_result
            lot["view"] = _result_view(result)
        except (FetchError, EmberError, BudgetExceeded) as exc:
            # Drop the earlier verdict too: a stale BID next to an error
            # would be worse than no verdict.
            lot["used_model"] = False
            lot["view"] = None
            lot["error"] = str(exc)

    # -- what the pages can ask for -------------------------------------------
    def add_lot(self, url, all_in_max) -> None:
        url = check_lot_url(url)
        all_in_max = _amount(all_in_max, "The all-in max")
        if any(lot["url"] == url for lot in self.lots):
            raise BadRequest("That lot is already in your list.")
        if self.hosted and len(self.lots) >= HOSTED_MAX_LOTS:
            raise BadRequest(f"This demo keeps up to {HOSTED_MAX_LOTS} lots. Remove one first.")
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
            if _view_status(lot["view"]) != "ended":
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
            if lot["page"] is not None or lot["view"] is not None:
                self._evaluate(lot, refetch=False)

    def run_demo(self, step=None) -> None:
        """Run the demo: an auction that is live right now, then the fixed
        demo lots, each as listed and (once ended) as-if-live. For each lot
        this keeps what its page says, what the command line would print,
        and the verdict the pages show.

        `step` runs one part: "live" for the live auction, or the number of
        a fixed lot. The pages go step by step, so each request stays short
        and the results appear as they come.
        """
        is_fixed = (isinstance(step, int) and not isinstance(step, bool)
                    and 0 <= step < len(self.demo))
        if step not in (None, "live") and not is_fixed:
            raise BadRequest("That is not a step of the demo.")
        if not is_fixed:
            # Keep the same live auction between runs until it ends.
            still_live = (self.demo_live is not None
                          and _view_status(self.demo_live["listed"]) == "live")
            if not still_live:
                found = find_live_lot(self.fetch_html, self.fetch,
                                      skip=tuple(lot["url"] for lot in DEMO_LOTS))
                self.demo_live = _demo_entry(found, live_now=True) if found else None

        if step is None:
            entries = self._demo_entries()
        elif is_fixed:
            entries = [self.demo[step]]
        else:
            entries = [self.demo_live] if self.demo_live else []
        for entry in entries:
            self._run_demo_entry(entry)
        self.demo_ran_at = self.now().isoformat(timespec="seconds")

    def _run_demo_entry(self, entry: dict) -> None:
        lot = entry["lot"]
        listed = live = None
        entry["error"] = None
        try:
            parsed, lot_text = self.fetch(lot["url"])
            key = snap_key(parsed)
            if key != entry["snap"]:
                entry["reads"] = {}
            listed, live = evaluate_demo_lot(self.demo_agent, lot, lot_text,
                                             entry["reads"], self.now(),
                                             parsed=parsed)
            entry.update(snap=key, listed=_result_view(listed),
                         as_if_live=_result_view(live) if live else None,
                         listing={field: parsed.get(field) for field in LISTING_FIELDS},
                         fetched_at=self.now().isoformat(timespec="seconds"),
                         reads={"listed": listed.ember_result,
                                "as_if_live": live.ember_result if live else None})
        except (FetchError, EmberError, BudgetExceeded) as exc:
            listed = live = None
            entry.update(error=str(exc), listed=None, as_if_live=None)
        # Exactly what the command shown beside it prints for this run.
        entry["cli_output"] = "\n".join(
            transcript_header(self.agent.ember.live, self.agent.ember.model)
            + transcript_lot(lot["url"], lot["all_in_max"], listed, live, entry["error"])
            + transcript_footer(self.agent.ledger.summary(), "cost_ledger.jsonl"))

    # -- HTTP -----------------------------------------------------------------
    def dispatch(self, method: str, path: str, headers: dict, body: bytes,
                 allowed_hosts: tuple | None, project_dir: str) -> tuple[int, str, bytes]:
        """Answer one request: (status, content type, body). `headers` has
        lower-case names. `allowed_hosts` is the local server's own
        addresses; hosted, it is None and any host name is answered."""
        host = headers.get("host", "")
        if allowed_hosts is not None and host not in allowed_hosts:
            return self._json(403, {"error": "This server only answers on its own local address."})
        path = urllib.parse.urlsplit(path).path

        if method == "GET":
            if path in PAGES:
                name, title = PAGES[path]
                return (200, "text/html; charset=utf-8",
                        render_page(project_dir, name, title, self.hosted))
            if path in ASSETS:
                name, content_type = ASSETS[path]
                try:
                    with open(os.path.join(project_dir, "public", name), "rb") as fh:
                        return 200, content_type, fh.read()
                except FileNotFoundError:
                    # Hosted, Vercel serves public/ itself and may not hand
                    # the function a copy of it.
                    return self._json(404, {"error": "Not found."})
            if path == "/api/state":
                with self.lock:
                    return self._json(200, self.state())
            return self._json(404, {"error": "Not found."})

        if method != "POST":
            return self._json(405, {"error": "Method not allowed."})
        origin = headers.get("origin")
        if origin is not None and urllib.parse.urlsplit(origin).netloc != host:
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
            "/api/state": lambda: None,
            "/api/lots": lambda: self.add_lot(data.get("url"), data.get("all_in_max")),
            "/api/lots/remove": lambda: self.remove_lot(data.get("url")),
            "/api/lots/max": lambda: self.set_max(data.get("url"), data.get("all_in_max")),
            "/api/lots/check": lambda: self.check(data.get("url")),
            "/api/lots/check-all": self.check_all,
            "/api/settings": lambda: self.update_settings(
                data.get("tax_rate"), data.get("pickup_cost_usd"),
                data.get("shipping_preference")),
            "/api/demo/run": lambda: self.run_demo(data.get("step")),
        }
        if path not in actions:
            return self._json(404, {"error": "Not found."})
        with self.lock:
            if self.hosted:
                self.load_session(data.get("session"))
            try:
                actions[path]()
            except BadRequest as exc:
                return self._json(400, {"error": str(exc)})
            reply = self.state()
            if self.hosted:
                reply["session"] = self.export_session()
            return self._json(200, reply)

    @staticmethod
    def _json(status: int, payload: dict) -> tuple[int, str, bytes]:
        return status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")


def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, payload: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    handler.end_headers()
    handler.wfile.write(payload)


def _read_body(handler: BaseHTTPRequestHandler, limit: int) -> bytes | None:
    """The request body, or None when it is larger than `limit`."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length > limit:
        return None
    return handler.rfile.read(length) if length else b""


def make_handler(app: WebApp, allowed_hosts: tuple, project_dir: str):
    """The local server's handler: one long-lived app."""
    class Handler(BaseHTTPRequestHandler):
        def _answer(self, method: str) -> None:
            body = _read_body(self, MAX_BODY_BYTES)
            if body is None:
                _send(self, *WebApp._json(413, {"error": "Request too large."}))
                return
            headers = {name.lower(): value for name, value in self.headers.items()}
            _send(self, *app.dispatch(method, self.path, headers, body, allowed_hosts, project_dir))

        def do_GET(self):  # noqa: N802 — http.server's naming
            self._answer("GET")

        def do_POST(self):  # noqa: N802
            self._answer("POST")

        def log_message(self, format, *args):  # noqa: A002 — quiet by default
            pass

    return Handler


def serve(app: WebApp, port: int, project_dir: str) -> ThreadingHTTPServer:
    """Build the local server, bound to this machine only. The caller runs it."""
    allowed_hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, allowed_hosts, project_dir))


# -- hosted ---------------------------------------------------------------

def hosted_app(fixture: bool = False) -> WebApp:
    """A fresh app for one hosted request. The key comes from the server's
    environment; the ledger sits in the instance's temporary space."""
    budget = float(os.environ.get("MODEL_BUDGET_USD") or 10.0)
    ledger = CostLedger(os.path.join(tempfile.gettempdir(), "cost_ledger.jsonl"), budget_usd=budget)
    agent = BiddingAgent(ledger=ledger, tax_rate=HOSTED_DEFAULT_TAX_RATE,
                         ember=EmberClient(ledger=ledger, api_key="" if fixture else None))
    return WebApp(agent, None, hosted=True)


def requested_path(raw_path: str) -> str:
    """The path the visitor asked for. Vercel sends every path to one
    function, and passes the original along as ?p=... (see vercel.json); if
    the platform hands over the original path itself, that is used as is."""
    parts = urllib.parse.urlsplit(raw_path)
    if parts.path.rstrip("/") == "/api/index":
        asked = urllib.parse.parse_qs(parts.query).get("p", [""])[0]
        return "/" + asked.lstrip("/")
    return parts.path


def answer_hosted(request: BaseHTTPRequestHandler, method: str, project_dir: str,
                  build_app=hosted_app) -> None:
    """Answer one hosted request: a new app, filled from the visitor's
    session, so nothing is shared between visitors and nothing depends on
    which instance answers. Used by the Vercel entry point (api/index.py)
    and by `ui.py --hosted`."""
    body = _read_body(request, HOSTED_MAX_BODY_BYTES)
    if body is None:
        _send(request, *WebApp._json(413, {"error": "Request too large."}))
        return
    headers = {name.lower(): value for name, value in request.headers.items()}
    try:
        reply = build_app().dispatch(method, requested_path(request.path), headers,
                                     body, None, project_dir)
    except Exception:  # noqa: BLE001 — a visitor must never see a traceback
        reply = WebApp._json(500, {"error": "Something went wrong on the server. Try again."})
    _send(request, *reply)


def make_hosted_handler(project_dir: str, build_app=hosted_app):
    """A handler class for running the hosted mode locally (`ui.py --hosted`)."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's naming
            answer_hosted(self, "GET", project_dir, build_app)

        def do_POST(self):  # noqa: N802
            answer_hosted(self, "POST", project_dir, build_app)

        def log_message(self, format, *args):  # noqa: A002 — quiet by default
            pass

    return Handler
