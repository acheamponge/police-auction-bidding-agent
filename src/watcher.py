"""Watch lots as their prices move, until they end.

Each round fetches every lot's page (free) and compares the fields that
matter to the last time the lot was evaluated: status, price, increment,
premium, reserve flag and shipping prices. Only a lot that changed is sent
to Muse Ember again, so a quiet auction costs nothing to watch. Every model
call still goes through the ledger and its budget wall.

Watching never bids. Each evaluation ends in the same dry-run bid plan as
`evaluate.py`: the proxy maximum to set, or the reason not to.
"""

from __future__ import annotations

import datetime
import time

from .agent import BiddingAgent, render_result
from .cost_ledger import BudgetExceeded
from .ember_client import EmberError
from .lot_fetcher import FetchError, fetch_and_parse

# The floor on the time between rounds. Auctions move in minutes, and
# PropertyRoom extends one that gets a bid in its last minute, so polling
# faster buys nothing and only loads their site.
MIN_INTERVAL_S = 60


def snapshot(parsed: dict) -> tuple:
    """The parts of a parsed lot page that can change a verdict."""
    shipping = tuple((o["method"], o["price_usd"], o["available"])
                     for o in parsed.get("shipping_options", []))
    return (parsed.get("status"), parsed.get("current_price"),
            parsed.get("bid_increment"), parsed.get("premium_pct"),
            parsed.get("reserve_not_met"), shipping)


def watch(agent: BiddingAgent, lots: list, *, interval_s: float = 300,
          max_rounds: int | None = None, fetch=fetch_and_parse,
          sleep=time.sleep, now=datetime.datetime.now, emit=print) -> list:
    """Watch `lots` (dicts with "url" and optionally "all_in_max") until each
    has ended or `max_rounds` rounds have run. Returns one event per
    evaluation. `fetch`, `sleep`, `now` and `emit` are replaceable for tests.
    """
    events = []
    evaluated = {}      # url -> snapshot at its last evaluation
    last_verdict = {}   # url -> verdict at its last evaluation
    active = list(lots)
    rounds = 0
    while active and (max_rounds is None or rounds < max_rounds):
        if rounds:
            sleep(max(interval_s, MIN_INTERVAL_S))
        rounds += 1
        for lot in list(active):
            url = lot["url"]
            stamp = now().strftime("%H:%M:%S")
            try:
                parsed, lot_text = fetch(url)
            except FetchError as exc:
                emit(f"[{stamp}] {url}: {exc}. Will try again next round.")
                continue
            current = snapshot(parsed)
            if evaluated.get(url) == current:
                continue
            try:
                result = agent.evaluate_page(parsed, lot_text, url, lot.get("all_in_max"))
            except BudgetExceeded as exc:
                emit(f"[{stamp}] Stopping: {exc}")
                return events
            except EmberError as exc:
                emit(f"[{stamp}] {url}: {exc}. Will try again next round.")
                continue
            evaluated[url] = current
            evaluation = result.evaluation
            first_look = url not in last_verdict
            verdict_changed = not first_look and last_verdict[url] != evaluation.verdict
            last_verdict[url] = evaluation.verdict
            events.append({
                "round": rounds,
                "url": url,
                "status": evaluation.status,
                "current_price": evaluation.current_price,
                "verdict": evaluation.verdict,
                "policy_decision": result.policy_decision,
                "bid_amount": evaluation.bid_amount,
                "bid_is_proxy": evaluation.bid_is_proxy,
                "verdict_changed": verdict_changed,
            })
            if first_look or verdict_changed:
                emit(f"[{stamp}] " + ("VERDICT CHANGED\n" if verdict_changed else "")
                     + render_result(result) + "\n")
            else:
                price = (f"${evaluation.current_price:,.2f}"
                         if evaluation.current_price is not None else "price not read")
                emit(f"[{stamp}] {evaluation.title or url}: now {price}, "
                     f"still {evaluation.verdict} ({result.policy_decision})")
            if evaluation.status == "ended":
                active.remove(lot)
    return events
