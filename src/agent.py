"""Orchestration: fetch -> Muse Ember -> deterministic math -> policy gate.

One `evaluate_lot()` call is one full agent run for one lot:
  1. Fetch the lot's own PropertyRoom page and parse its fee table.
  2. Send the page text to Muse Ember (one call; cost logged to the ledger).
     Ember reads the same fields independently and judges the listing. The
     deterministic parse is the authority on money numbers; Ember's read can
     only turn a BID into a HOLD_FOR_HUMAN.
  3. Compute the all-in math and the verdict (BID / NO_BID / HOLD_FOR_HUMAN).
  4. Run the spending policy gate (SimCity pattern). In dry-run mode the
     agent prints the exact bid it would place and stops — no money moves.

`evaluate_page()` is steps 2-4 for a page that is already fetched and parsed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cost_ledger import CostLedger
from .ember_client import EmberClient
from .evaluator import Evaluation, evaluate, render
from .live_bid import LiveBiddingNotConfigured, build_bid_request, place_bid
from .lot_fetcher import fetch_and_parse
from .policy import PolicyConfig, PolicyState


@dataclass
class AgentResult:
    evaluation: Evaluation
    policy_decision: str
    policy_note: str
    bid_request_text: str | None = None
    live_outcome: str | None = None


class BiddingAgent:
    def __init__(self, *, ledger: CostLedger,
                 tax_rate: float | None = None,
                 shipping_preference: str = "cheapest",
                 pickup_cost_usd: float | None = None,
                 policy_config: PolicyConfig | None = None,
                 ember: EmberClient | None = None):
        self.ledger = ledger
        self.tax_rate = tax_rate
        self.shipping_preference = shipping_preference
        self.pickup_cost_usd = pickup_cost_usd
        self.policy_state = PolicyState(config=policy_config or PolicyConfig())
        self.ember = ember or EmberClient(ledger=ledger)

    def evaluate_lot(self, lot_url: str, all_in_max: float,
                     live: bool = False, confirm: bool = False) -> AgentResult:
        parsed, lot_text = fetch_and_parse(lot_url)
        return self.evaluate_page(parsed, lot_text, lot_url, all_in_max,
                                  live=live, confirm=confirm)

    def evaluate_page(self, parsed: dict, lot_text: str, lot_url: str,
                      all_in_max: float, live: bool = False,
                      confirm: bool = False) -> AgentResult:
        ember_result = self.ember.evaluate_lot(lot_text, lot_url)
        evaluation = evaluate(
            parsed, ember_result, lot_url, all_in_max,
            tax_rate=self.tax_rate,
            shipping_preference=self.shipping_preference,
            allow_reserve_lots=self.policy_state.config.allow_reserve_lots,
            pickup_cost_usd=self.pickup_cost_usd,
        )

        decision = self.policy_state.evaluate(
            evaluation.verdict, evaluation.all_in_at_next_bid, lot_url)

        bid_text = None
        live_outcome = None
        if decision.decision == "DRY_RUN" and evaluation.verdict == "BID":
            bid = build_bid_request(evaluation, lot_url,
                                    listing_id=parsed.get("listing_id"))
            bid_text = bid.describe()
        elif decision.decision == "LIVE" and evaluation.verdict == "BID":
            bid = build_bid_request(evaluation, lot_url,
                                    listing_id=parsed.get("listing_id"))
            bid_text = bid.describe()
            if live and confirm:
                try:
                    live_outcome = place_bid(bid, self.policy_state,
                                             confirmed=True)
                    self.policy_state.commit(lot_url,
                                             evaluation.all_in_at_next_bid)
                except LiveBiddingNotConfigured as exc:
                    live_outcome = f"REFUSED: {exc}"
            else:
                live_outcome = ("Live decision reached, but --live --confirm "
                                "was not passed — no bid placed.")
        elif decision.decision == "LIVE":
            live_outcome = "Policy allowed live bidding, but the verdict is not BID."

        return AgentResult(
            evaluation=evaluation,
            policy_decision=decision.decision,
            policy_note=decision.note,
            bid_request_text=bid_text,
            live_outcome=live_outcome,
        )


def render_result(result: AgentResult) -> str:
    parts = [render(result.evaluation), "",
             f"Policy: {result.policy_decision}",
             f"  {result.policy_note}"]
    if result.bid_request_text:
        parts += ["", result.bid_request_text]
    if result.live_outcome:
        parts += ["", result.live_outcome]
    return "\n".join(parts)
