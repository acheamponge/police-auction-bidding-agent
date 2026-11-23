#!/usr/bin/env python3
"""Demo: evaluate four real PropertyRoom lots end-to-end. Evaluation only — no bids.

Usage:
    python3 run_demo.py                      # the four demo lots, fixture mode
    python3 run_demo.py --live-ember         # same, but with live Muse Ember
                                              # (needs MODEL_API_KEY, in the
                                              # environment or in .env)
    python3 run_demo.py --lot URL --max N    # evaluate one lot of your own

With --live-ember every evaluation calls Muse Ember for real and the cost
ledger records the spend. Without it the agent runs in fixture mode, even if
a key is present: the deterministic local evaluator returns the same schema
Ember would, marked simulated, at $0.00.

Each lot is evaluated twice: once as listed (real status — the demo lots
have ended, so the agent correctly refuses to bid), and once "as-if-live"
(the "Ended <date>" line rewritten to "Ends <date>") so the full BID path is
exercised on real fee data.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import BiddingAgent, render_result  # noqa: E402
from src.config import load_env  # noqa: E402
from src.cost_ledger import CostLedger  # noqa: E402
from src.demo_lots import DEMO_LOTS  # noqa: E402
from src.ember_client import EmberClient  # noqa: E402
from src.lot_fetcher import fetch_lot_page, html_to_text, parse_lot_text  # noqa: E402
from src.policy import PolicyConfig  # noqa: E402

# Two things a lot page cannot tell the agent, set here as explicit, labelled
# demo assumptions. Replace both with your own before bidding for real.
#   Tax is not printed on lot pages (Lynnwood, WA combined rate ~10%).
#   Collecting a pickup lot costs the buyer something; the demo assumes a
#   local buyer, so $0 on top of the pickup fee the page prints.
DEMO_TAX_RATE = 0.10
DEMO_PICKUP_COST_USD = 0.00

_ENDED_LINE = r"\bEnded\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+at\s+[\d:]+\s+[AP]M\s*\(Eastern\)"


def as_if_live(lot_text: str) -> str:
    """The same page text with its "Ended <date>" line turned into an
    "Ends <date>" line two days out. Fee data is untouched."""
    ends = datetime.datetime.now() + datetime.timedelta(days=2)
    return re.sub(_ENDED_LINE,
                  ends.strftime("Ends %b %d, %Y at %I:%M %p (Eastern)"), lot_text)


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
    load_env(os.path.join(here, ".env"))
    ledger_path = args.ledger or os.path.join(here, "cost_ledger.jsonl")

    if args.live_ember and not os.environ.get("MODEL_API_KEY"):
        print("ERROR: --live-ember needs MODEL_API_KEY (the $10 hackathon "
              "credits), in the environment or in .env. Run without it for "
              "fixture mode.", file=sys.stderr)
        return 2

    ledger = CostLedger(ledger_path)
    agent = BiddingAgent(
        ledger=ledger,
        tax_rate=DEMO_TAX_RATE,
        shipping_preference="cheapest",
        pickup_cost_usd=DEMO_PICKUP_COST_USD,
        policy_config=PolicyConfig(
            tx_cap_usd=4_999.99,      # PropertyRoom card/PayPal ceiling
            session_budget_usd=2_000.00,
            dry_run=True,             # demo never bids
            allow_reserve_lots=False,
            live_bidding_configured=False,
        ),
        # An empty key forces fixture mode unless --live-ember was asked for.
        ember=EmberClient(ledger=ledger, api_key=None if args.live_ember else ""),
    )
    mode = "LIVE Muse Ember (muse-ember-1.0)" if agent.ember.live else \
        "FIXTURE MODE — no model called, $0.00 (pass --live-ember for the real model)"
    print("=" * 72)
    print("PropertyRoom bidding agent — demo (evaluation only, no bids)")
    print(f"Mode: {mode}")
    print(f"Tax assumption: {DEMO_TAX_RATE:.0%} (not printed on lot pages)")
    print(f"Pickup cost assumption: ${DEMO_PICKUP_COST_USD:,.2f} on top of the "
          "page's pickup fee (a local buyer)")
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
            lot_text = html_to_text(fetch_lot_page(lot["url"]))
            listed = agent.evaluate_page(parse_lot_text(lot_text), lot_text,
                                         lot["url"], lot["all_in_max"])
            print(render_result(listed))
            if listed.evaluation.status != "ended":
                continue

            # As-if-live pass: same real fee data, the ended line rewritten,
            # so the BID path is exercised end-to-end.
            print()
            print(">>> AS-IF-LIVE: same lot, evaluated as if the auction were "
                  "still running (fee data is real; only the end line is changed).")
            live_text = as_if_live(lot_text)
            print(render_result(agent.evaluate_page(
                parse_lot_text(live_text), live_text,
                lot["url"], lot["all_in_max"])))
        except Exception as exc:  # noqa: BLE001 — demo should show the failure
            print(f"FAILED to evaluate: {exc}")

    print()
    print("=" * 72)
    print("COST LEDGER — every Muse Ember call, proving the $10 budget:")
    print(json.dumps(ledger.summary(), indent=2))
    print(f"Ledger file: {ledger_path}")
    print("No bids were placed. Dry-run mode: the agent only prints the "
          "exact bid it would place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
