#!/usr/bin/env python3
"""Evaluate a single PropertyRoom lot. Evaluation only unless --live --confirm.

Usage:
    python3 evaluate.py --lot URL --max 1500
    python3 evaluate.py --lot URL --max 1500 --tax-rate 0.10
    python3 evaluate.py --lot URL --max 1500 --live --confirm   # live bid (refuses: not configured)

With MODEL_API_KEY set, the evaluation calls live Muse Ember; without it,
fixture mode runs (same schema, simulated, $0.00).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import BiddingAgent, render_result  # noqa: E402
from src.cost_ledger import CostLedger  # noqa: E402
from src.policy import PolicyConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot", required=True, help="PropertyRoom lot URL")
    parser.add_argument("--max", type=float, required=True,
                        help="all-in max in USD — the agent never exceeds it")
    parser.add_argument("--tax-rate", type=float, default=None,
                        help="sales-tax rate as a decimal (e.g. 0.10). Tax is "
                             "not printed on lot pages; without it the agent "
                             "holds for a human.")
    parser.add_argument("--shipping", default="cheapest",
                        help="delivery preference: cheapest (default), pickup, "
                             "standard, expedited")
    parser.add_argument("--allow-reserve", action="store_true",
                        help="allow bidding on soft-reserve lots (default: hold)")
    parser.add_argument("--live", action="store_true",
                        help="live mode (default is dry-run)")
    parser.add_argument("--confirm", action="store_true",
                        help="explicit human confirmation for a live bid")
    parser.add_argument("--ledger", default=None)
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    ledger_path = args.ledger or os.path.join(here, "cost_ledger.jsonl")

    config_path = os.path.join(here, "config.json")
    live_configured = False
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        live_configured = bool(cfg.get("live_bidding", {}).get("configured"))

    agent = BiddingAgent(
        ledger=CostLedger(ledger_path),
        tax_rate=args.tax_rate,
        shipping_preference=args.shipping,
        policy_config=PolicyConfig(
            dry_run=not args.live,
            allow_reserve_lots=args.allow_reserve,
            live_bidding_configured=live_configured,
        ),
    )
    result = agent.evaluate_lot(args.lot, args.max,
                                live=args.live, confirm=args.confirm)
    print(render_result(result))
    print()
    print("Ledger:", json.dumps(agent.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
