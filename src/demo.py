"""The demo run, shared by run_demo.py and the web UI's Demo page.

Each demo lot is evaluated as listed. A lot that has ended is then evaluated
again "as-if-live": the same page text with its "Ended <date>" line turned
into an "Ends <date>" line, so the paths past "auction ended" are exercised on
real fee data.

The fixed demo lots are real but have all ended. So that a demo also shows an
auction that is running at that moment, `find_live_lot()` picks one off
PropertyRoom's vehicles listing.

What run_demo.py prints is built here (the `transcript_*` functions), so the
web UI can show, for the same run, exactly what the command line prints.

Two things a lot page cannot tell the agent are fixed here as explicit,
labelled demo assumptions. Replace both with your own before bidding for real.
  Tax is not printed on lot pages (Lynnwood, WA combined rate ~10%).
  Collecting a pickup lot costs the buyer something; the demo assumes a local
  buyer, so $0 on top of the pickup fee the page prints.
"""

from __future__ import annotations

import datetime
import json
import math
import re

from .agent import AgentResult, BiddingAgent, render_result
from .cost_ledger import CostLedger
from .ember_client import EmberClient
from .evaluator import evaluate
from .lot_fetcher import FetchError, parse_lot_text
from .policy import PolicyConfig

DEMO_TAX_RATE = 0.10
DEMO_PICKUP_COST_USD = 0.00

# Where a currently running auction is looked for, and how many lot pages
# may be read while looking.
LIVE_LISTING_URL = "https://www.propertyroom.com/c/vehicles"
LIVE_SEARCH_LIMIT = 5
# A live lot found at run time has no max of its own. The demo gives it one a
# quarter above what the next bid costs all-in, so the whole pipeline runs.
LIVE_MAX_HEADROOM = 1.25

_ENDED_LINE = r"\bEnded\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+at\s+[\d:]+\s+[AP]M\s*\(Eastern\)"
RULE = "=" * 72
THIN_RULE = "-" * 72


def as_if_live(lot_text: str, now: datetime.datetime | None = None) -> str:
    """The same page text with its "Ended <date>" line turned into an
    "Ends <date>" line two days out. Fee data is untouched."""
    ends = (now or datetime.datetime.now()) + datetime.timedelta(days=2)
    return re.sub(_ENDED_LINE,
                  ends.strftime("Ends %b %d, %Y at %I:%M %p (Eastern)"), lot_text)


def demo_agent(ledger: CostLedger, ember: EmberClient) -> BiddingAgent:
    """An agent with the demo's assumptions and the default spending policy."""
    return BiddingAgent(
        ledger=ledger,
        tax_rate=DEMO_TAX_RATE,
        shipping_preference="cheapest",
        pickup_cost_usd=DEMO_PICKUP_COST_USD,
        policy_config=PolicyConfig(
            tx_cap_usd=4_999.99,      # PropertyRoom card/PayPal ceiling
            session_budget_usd=2_000.00,
            dry_run=True,             # the demo never bids
            allow_reserve_lots=False,
            live_bidding_configured=False,
        ),
        ember=ember,
    )


def evaluate_demo_lot(agent: BiddingAgent, lot: dict, lot_text: str,
                      earlier_reads: dict | None = None,
                      now: datetime.datetime | None = None) -> tuple[AgentResult, AgentResult | None]:
    """Evaluate one demo lot as listed and, if it has ended, as-if-live.

    `earlier_reads` may hold Ember's reads from a previous run of the same
    page text ({"listed": ..., "as_if_live": ...}); those are reused, so
    re-running an unchanged demo costs nothing.
    """
    earlier_reads = earlier_reads or {}
    listed = agent.evaluate_page(parse_lot_text(lot_text), lot_text, lot["url"],
                                 lot["all_in_max"], ember_result=earlier_reads.get("listed"))
    if listed.evaluation.status != "ended":
        return listed, None
    live_text = as_if_live(lot_text, now)
    return listed, agent.evaluate_page(parse_lot_text(live_text), live_text, lot["url"],
                                       lot["all_in_max"],
                                       ember_result=earlier_reads.get("as_if_live"))


# -- a lot that is live right now -------------------------------------------

def lot_links(listing_html: str) -> list[str]:
    """The lot pages a PropertyRoom listing page links to, in page order."""
    paths = re.findall(r'href="(/l/[^"#?]+/\d+)(?:[?#][^"]*)?"', listing_html)
    return ["https://www.propertyroom.com" + path for path in dict.fromkeys(paths)]


def demo_max_for(parsed: dict) -> float | None:
    """An all-in max for a lot found at run time: a quarter above what the
    next bid costs all-in under the demo's assumptions, rounded up to $10."""
    at_next_bid = evaluate(parsed, {}, "", None, tax_rate=DEMO_TAX_RATE,
                           pickup_cost_usd=DEMO_PICKUP_COST_USD).all_in_at_next_bid
    if at_next_bid is None:
        return None
    return float(math.ceil(at_next_bid * LIVE_MAX_HEADROOM / 10) * 10)


def find_live_lot(fetch_html, fetch_lot, skip: tuple = ()) -> dict | None:
    """Pick an auction that is running right now off PropertyRoom's vehicles
    listing. Of the first few lots listed, a live one that prints a buyer's
    premium is preferred (those are the agency vehicles, and they exercise the
    premium read); failing that, the first live one. Returns a demo lot
    ({"url", "label", "all_in_max"}) or None.

    fetch_html(url) returns a page's HTML; fetch_lot(url) returns
    (parsed, text) for a lot page.
    """
    try:
        candidates = [url for url in lot_links(fetch_html(LIVE_LISTING_URL)) if url not in skip]
    except FetchError:
        return None
    first_live = None
    for url in candidates[:LIVE_SEARCH_LIMIT]:
        try:
            parsed, _ = fetch_lot(url)
        except FetchError:
            continue
        if parsed.get("status") != "live":
            continue
        lot = {"url": url,
               "label": (parsed.get("title") or url) + " — live on PropertyRoom right now",
               "all_in_max": demo_max_for(parsed)}
        if parsed.get("premium_pct") is not None:
            return lot
        first_live = first_live or lot
    return first_live


# -- what the command line prints ---------------------------------------------

def cli_command(lot: dict, live_model: bool) -> str:
    """The command that runs this one lot through the demo."""
    command = f"python3 run_demo.py --lot {lot['url']}"
    if lot.get("all_in_max") is not None:
        command += f" --max {lot['all_in_max']:g}"
    return command + (" --live-ember" if live_model else "")


def transcript_header(live_model: bool, model: str) -> list[str]:
    mode = (f"LIVE Muse Ember ({model})" if live_model else
            "FIXTURE MODE — no model called, $0.00 (pass --live-ember for the real model)")
    return [RULE,
            "PropertyRoom bidding agent — demo (evaluation only, no bids)",
            f"Mode: {mode}",
            f"Tax assumption: {DEMO_TAX_RATE:.0%} (not printed on lot pages)",
            f"Pickup cost assumption: ${DEMO_PICKUP_COST_USD:,.2f} on top of the "
            "page's pickup fee (a local buyer)",
            RULE]


def transcript_lot(label: str, all_in_max: float | None, listed: AgentResult | None,
                   live: AgentResult | None, error: str | None = None) -> list[str]:
    lines = ["", THIN_RULE, f"LOT: {label}",
             "All-in max: " + (f"${all_in_max:,.2f}" if all_in_max is not None else "not given"),
             THIN_RULE]
    if error is not None or listed is None:
        return lines + [f"FAILED to evaluate: {error}"]
    lines.append(render_result(listed))
    if live is not None:
        lines += ["",
                  ">>> AS-IF-LIVE: same lot, evaluated as if the auction were "
                  "still running (fee data is real; only the end line is changed).",
                  render_result(live)]
    return lines


def transcript_footer(ledger_summary: dict, ledger_path: str) -> list[str]:
    return ["", RULE,
            "COST LEDGER — every Muse Ember call, proving the $10 budget:",
            json.dumps(ledger_summary, indent=2),
            f"Ledger file: {ledger_path}",
            "No bids were placed. Dry-run mode: the agent only prints the "
            "exact bid it would place."]
