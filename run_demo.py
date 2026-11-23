#!/usr/bin/env python3
"""Demo: evaluate 2-3 real PropertyRoom lots end-to-end. Evaluation only — no bids.

Usage:
    python3 run_demo.py                      # the three demo lots, fixture mode
    python3 run_demo.py --live-ember         # same, but with live Muse Ember
                                              # (needs MODEL_API_KEY set)
    python3 run_demo.py --lot URL --max N    # evaluate one lot of your own

With MODEL_API_KEY set (the $10 hackathon credits), every evaluation calls
Muse Ember for real and the cost ledger records the spend. Without a key the
agent runs in fixture mode: the deterministic local evaluator returns the
same schema Ember would, marked simulated, at $0.00.

Each lot is evaluated twice: once as listed (real status — these demo lots
have ended, so the agent correctly refuses to bid), and once "as-if-live"
(status forced to live) so the full BID path is exercised on real fee data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import BiddingAgent, render_result  # noqa: E402
from src.cost_ledger import CostLedger  # noqa: E402
from src.demo_lots import DEMO_LOTS  # noqa: E402
from src.evaluator import render  # noqa: E402
from src.policy import PolicyConfig  # noqa: E402

# Tax is NOT printed on PropertyRoom lot pages. This is an explicit,
# labelled assumption for the demo (Lynnwood, WA combined rate ~10%) —
# replace it with the ship-to jurisdiction's real rate before bidding.
DEMO_TAX_RATE = 0.10


def build_agent(ledger_path: str) -> BiddingAgent:
    ledger = CostLedger(ledger_path)
    return BiddingAgent(
        ledger=ledger,
        tax_rate=DEMO_TAX_RATE,
        shipping_preference="cheapest",
        policy_config=PolicyConfig(
            tx_cap_usd=4_999.99,      # PropertyRoom card/PayPal ceiling
            session_budget_usd=2_000.00,
            dry_run=True,             # demo never bids
            allow_reserve_lots=False,
            live_bidding_configured=False,
        ),
    )


def evaluate_with_override(agent: BiddingAgent, lot_url: str, all_in_max: float,
                           as_if_live: bool):
    """Evaluate one lot; as_if_live forces the status to live so the BID
    path is exercised on real fee data (demo lots have ended)."""
    import re

    from src.lot_fetcher import fetch_and_parse
    from src.evaluator import evaluate

    parsed, lot_text = fetch_and_parse(lot_url)
    if as_if_live:
        parsed = dict(parsed)
        parsed["status"] = "live"
        # Rewrite the text the same way, so Ember's judgment also sees a
        # live lot instead of the "Ended ..." line.
        lot_text = re.sub(
            r"Ended\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+at\s+[\d:]+\s+[AP]M\s*\(Eastern\)",
            "Ending\n2 day(s), 4 hour(s), 10 min(s), 30 sec(s), Ending",
            lot_text)
    ember_result = agent.ember.evaluate_lot(lot_text, lot_url)
    evaluation = evaluate(
        parsed, ember_result, lot_url, all_in_max,
        tax_rate=agent.tax_rate,
        shipping_preference=agent.shipping_preference,
        allow_reserve_lots=agent.policy_state.config.allow_reserve_lots,
    )
    decision = agent.policy_state.evaluate(
        evaluation.verdict, evaluation.all_in_at_next_bid, lot_url)
    from src.agent import AgentResult
    from src.live_bid import build_bid_request
    bid_text = None
    if decision.decision == "DRY_RUN" and evaluation.verdict == "BID":
        bid_text = build_bid_request(
            evaluation, lot_url, listing_id=parsed.get("listing_id")).describe()
    return AgentResult(evaluation=evaluation, policy_decision=decision.decision,
                       policy_note=decision.note, bid_request_text=bid_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot", help="a PropertyRoom lot URL to evaluate")
    parser.add_argument("--max", type=float, help="all-in max in USD for --lot")
    parser.add_argument("--live-ember", action="store_true",
                        help="use live Muse Ember (needs MODEL_API_KEY)")
    parser.add_argument("--ledger", default=None,
                        help="cost ledger path (default: cost_ledger.jsonl)")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    ledger_path = args.ledger or os.path.join(here, "cost_ledger.jsonl")

    if args.live_ember and not os.environ.get("MODEL_API_KEY"):
        print("ERROR: --live-ember needs MODEL_API_KEY set (the $10 hackathon "
              "credits). Run without it for fixture mode.", file=sys.stderr)
        return 2

    agent = build_agent(ledger_path)
    mode = "LIVE Muse Ember (muse-ember-1.0)" if agent.ember.live else \
        "FIXTURE MODE — no MODEL_API_KEY, no model called, $0.00"
    print("=" * 72)
    print("PropertyRoom bidding agent — demo (evaluation only, no bids)")
    print(f"Mode: {mode}")
    print(f"Tax assumption: {DEMO_TAX_RATE:.0%} (not printed on lot pages — "
          "configured, see README)")
    print("=" * 72)

    lots = DEMO_LOTS
    if args.lot:
        if args.max is None:
            print("ERROR: --lot needs --max (your all-in max in USD).",
                  file=sys.stderr)
            return 2
        lots = [{"url": args.lot, "label": args.lot, "all_in_max": args.max}]

    for lot in lots:
        print()
        print("-" * 72)
        print(f"LOT: {lot['label']}")
        print(f"All-in max: ${lot['all_in_max']:,.2f}")
        print("-" * 72)
        try:
            result = evaluate_with_override(agent, lot["url"],
                                            lot["all_in_max"], as_if_live=False)
        except Exception as exc:  # noqa: BLE001 — demo should show the failure
            print(f"FAILED to evaluate: {exc}")
            continue
        print(render_result(result))

        # As-if-live pass: same real fee data, status forced live, so the
        # BID path is exercised end-to-end.
        print()
        print(">>> AS-IF-LIVE: same lot, evaluated as if the auction were "
              "still running (fee data is real; only the status is forced).")
        try:
            live_result = evaluate_with_override(
                agent, lot["url"], lot["all_in_max"], as_if_live=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED to evaluate as-if-live: {exc}")
            continue
        print(render_result(live_result))

    print()
    print("=" * 72)
    print("COST LEDGER — every Muse Ember call, proving the $10 budget:")
    print(json.dumps(agent.ledger.summary(), indent=2))
    print(f"Ledger file: {ledger_path}")
    print("No bids were placed. Dry-run mode: the agent only prints the "
          "exact bid it would place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
