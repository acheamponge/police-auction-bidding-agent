#!/usr/bin/env python3
"""Evaluate a single PropertyRoom lot. Evaluation only unless --live --confirm.

Usage:
    python3 evaluate.py --lot URL --max 1500
    python3 evaluate.py --lot URL --max 1500 --tax-rate 0.10 --pickup-cost 0
    python3 evaluate.py --lot URL --max 1500 --live --confirm   # live bid (refuses: not configured)

Settings come from config.json when it exists (copy config.example.json);
any flag given here wins over it. The API key is read from the environment or
from a .env file next to this script.

With MODEL_API_KEY set, the evaluation calls live Muse Ember; without it,
fixture mode runs (same schema, simulated, $0.00).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import BiddingAgent, render_result  # noqa: E402
from src.config import load_env, load_settings  # noqa: E402
from src.cost_ledger import CostLedger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot", required=True, help="PropertyRoom lot URL")
    parser.add_argument("--max", type=float, default=None,
                        help="all-in max in USD — the agent never exceeds it "
                             "(default: all_in_max_default_usd in config.json)")
    parser.add_argument("--tax-rate", type=float, default=None,
                        help="sales-tax rate as a decimal (e.g. 0.10). Tax is "
                             "not printed on lot pages; without it the agent "
                             "holds for a human.")
    parser.add_argument("--shipping", default=None,
                        help="delivery preference: cheapest (default), pickup, "
                             "standard, expedited")
    parser.add_argument("--pickup-cost", type=float, default=None,
                        help="what it costs YOU to collect a pickup lot, in USD "
                             "(0 if you are local). Without it the agent never "
                             "chooses Customer Pick-up.")
    parser.add_argument("--allow-reserve", action="store_true",
                        help="allow bidding on soft-reserve lots (default: hold)")
    parser.add_argument("--live", action="store_true",
                        help="live mode (default is dry-run)")
    parser.add_argument("--confirm", action="store_true",
                        help="explicit human confirmation for a live bid")
    parser.add_argument("--ledger", default=None)
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    load_env(os.path.join(here, ".env"))
    settings = load_settings(os.path.join(here, "config.json"))

    all_in_max = args.max if args.max is not None else settings.all_in_max_default_usd
    if all_in_max is None:
        print("ERROR: no all-in max. Pass --max, or set all_in_max_default_usd "
              "in config.json.", file=sys.stderr)
        return 2

    agent = BiddingAgent(
        ledger=CostLedger(args.ledger or os.path.join(here, "cost_ledger.jsonl")),
        tax_rate=args.tax_rate if args.tax_rate is not None else settings.tax_rate,
        shipping_preference=args.shipping or settings.shipping_preference,
        pickup_cost_usd=(args.pickup_cost if args.pickup_cost is not None
                         else settings.pickup_cost_usd),
        policy_config=dataclasses.replace(
            settings.policy,
            dry_run=not args.live,
            allow_reserve_lots=args.allow_reserve or settings.policy.allow_reserve_lots,
        ),
    )
    result = agent.evaluate_lot(args.lot, all_in_max,
                                live=args.live, confirm=args.confirm)
    print(render_result(result))
    print()
    print("Ledger:", json.dumps(agent.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
