#!/usr/bin/env python3
"""Watch PropertyRoom lots as their prices move. Dry-run only: never bids.

Usage:
    python3 watch.py --lot URL --max 1500
    python3 watch.py --watchlist watchlist.json --interval 600
    python3 watch.py --watchlist watchlist.json --rounds 1     # one pass, then stop

A watchlist is a JSON list of lots (copy watchlist.example.json):
    [{"url": "https://www.propertyroom.com/l/.../12345678", "all_in_max": 1500}]
Leave "all_in_max" out and the agent suggests one for that lot, without bidding.

Each round re-reads every lot's page. A lot is only sent to Muse Ember again
when its price, status or fees changed, so a quiet auction costs nothing to
watch. It stops when every lot has ended, or after --rounds rounds.

Settings and the API key are read the same way as in evaluate.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.cli import add_setting_args, build_agent  # noqa: E402
from src.watcher import MIN_INTERVAL_S, watch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot", help="a single PropertyRoom lot URL to watch")
    parser.add_argument("--max", type=float, default=None,
                        help="all-in max in USD for --lot")
    parser.add_argument("--watchlist", help="path to a JSON list of lots to watch")
    parser.add_argument("--interval", type=float, default=300,
                        help=f"seconds between rounds (default 300, minimum {MIN_INTERVAL_S})")
    parser.add_argument("--rounds", type=int, default=None,
                        help="stop after this many rounds (default: until every lot ends)")
    add_setting_args(parser)
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    agent, settings = build_agent(args, here)

    lots = []
    if args.watchlist:
        with open(args.watchlist, encoding="utf-8") as fh:
            lots += json.load(fh)
    if args.lot:
        lots.append({"url": args.lot, "all_in_max": args.max})
    if not lots:
        print("ERROR: nothing to watch. Pass --lot URL or --watchlist FILE.",
              file=sys.stderr)
        return 2
    for lot in lots:
        if lot.get("all_in_max") is None:
            lot["all_in_max"] = settings.all_in_max_default_usd

    mode = "live Muse Ember" if agent.ember.live else "fixture mode, no model called"
    print(f"Watching {len(lots)} lot(s), every {max(args.interval, MIN_INTERVAL_S):.0f}s "
          f"({mode}). Dry-run: nothing is bid.\n")
    events = watch(agent, lots, interval_s=args.interval, max_rounds=args.rounds)
    print(f"Done: {len(events)} evaluation(s).")
    print("Ledger:", json.dumps(agent.ledger.summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
