#!/usr/bin/env python3
"""Evaluate a single PropertyRoom lot. Evaluation only unless --live --confirm.

Usage:
    python3 evaluate.py --lot URL --max 1500
    python3 evaluate.py --lot URL --max 1500 --tax-rate 0.10 --pickup-cost 0
    python3 evaluate.py --lot URL                # no max: suggests one, never bids
    python3 evaluate.py --lot URL --max 1500 --live --confirm   # live bid (refuses: not configured)

Settings come from config.json when it exists (copy config.example.json);
any flag given here wins over it. The API key is read from the environment or
from a .env file next to this script.

With MODEL_API_KEY set, the evaluation calls live Muse Ember; without it,
fixture mode runs (same schema, simulated, $0.00).

With no --max and no all_in_max_default_usd in config.json the agent does not
bid: it reports the all-in max that Muse Ember's value estimate would support.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import render_result  # noqa: E402
from src.cli import add_setting_args, build_agent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot", required=True, help="PropertyRoom lot URL")
    parser.add_argument("--max", type=float, default=None,
                        help="all-in max in USD — the agent never exceeds it "
                             "(default: all_in_max_default_usd in config.json)")
    add_setting_args(parser)
    parser.add_argument("--live", action="store_true",
                        help="live mode (default is dry-run)")
    parser.add_argument("--confirm", action="store_true",
                        help="explicit human confirmation for a live bid")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    agent, settings = build_agent(args, here, dry_run=not args.live)
    all_in_max = args.max if args.max is not None else settings.all_in_max_default_usd

    result = agent.evaluate_lot(args.lot, all_in_max,
                                live=args.live, confirm=args.confirm)
    print(render_result(result))
    print()
    print("Ledger:", json.dumps(agent.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
