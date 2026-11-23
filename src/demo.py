"""The demo run, shared by run_demo.py and the web UI's Demo page.

Each demo lot is evaluated as listed. A lot that has ended is then evaluated
again "as-if-live": the same page text with its "Ended <date>" line turned
into an "Ends <date>" line, so the paths past "auction ended" are exercised on
real fee data.

Two things a lot page cannot tell the agent are fixed here as explicit,
labelled demo assumptions. Replace both with your own before bidding for real.
  Tax is not printed on lot pages (Lynnwood, WA combined rate ~10%).
  Collecting a pickup lot costs the buyer something; the demo assumes a local
  buyer, so $0 on top of the pickup fee the page prints.
"""

from __future__ import annotations

import datetime
import re

from .agent import AgentResult, BiddingAgent
from .cost_ledger import CostLedger
from .ember_client import EmberClient
from .lot_fetcher import parse_lot_text
from .policy import PolicyConfig

DEMO_TAX_RATE = 0.10
DEMO_PICKUP_COST_USD = 0.00

_ENDED_LINE = r"\bEnded\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+at\s+[\d:]+\s+[AP]M\s*\(Eastern\)"


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
