#!/usr/bin/env python3
"""The demo UI: a live auction and the demo lots, your own lots, the policy and the ledger.

Usage:
    python3 ui.py                  # then open http://127.0.0.1:8765
    python3 ui.py --port 9000
    python3 ui.py --fixture        # never call the model, even with a key
    python3 ui.py --hosted         # behave as the Vercel deployment does

It runs on your own machine only and is dry-run only: nothing here can place
a bid. Your lots are kept in watchlist.json, the same file watch.py reads.

Settings and the API key are read the same way as in evaluate.py: config.json
and .env next to this script, with any flag given here winning. The tax rate,
pickup cost and delivery preference can also be changed on the Policy page;
those changes last until you stop the server.

With MODEL_API_KEY set, each new lot (and each lot whose price or fees
changed) costs one Muse Ember call, about $0.0008. Without a key, or with
--fixture, it runs in fixture mode at $0.00.

--hosted runs the stateless mode used on Vercel (see DEPLOY.md): the server
keeps nothing between requests, and lots live in the browser instead of
watchlist.json. It is here so that mode can be tried before deploying.
"""

from __future__ import annotations

import argparse
import functools
import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.cli import add_setting_args, build_agent  # noqa: E402
from src.config import load_env  # noqa: E402
from src.ember_client import EmberClient  # noqa: E402
from src.web import WebApp, hosted_app, make_hosted_handler, serve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fixture", action="store_true",
                        help="fixture mode: never call the model, even if a key is set")
    parser.add_argument("--hosted", action="store_true",
                        help="run the stateless mode used on Vercel")
    add_setting_args(parser)
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    if args.hosted:
        load_env(os.path.join(here, ".env"))
        build = functools.partial(hosted_app, fixture=args.fixture)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_hosted_handler(here, build))
        live = build().agent.ember.live
    else:
        agent, _ = build_agent(args, here)
        if args.fixture:
            agent.ember = EmberClient(ledger=agent.ledger, api_key="")
        server = serve(WebApp(agent, os.path.join(here, "watchlist.json")), args.port, here)
        live = agent.ember.live

    mode = "live Muse Ember" if live else "fixture mode, no model called"
    print(f"Police Auction Bidding Agent: http://127.0.0.1:{args.port}  ({mode}"
          f"{', hosted mode' if args.hosted else ''})")
    print("Dry-run only: nothing is bid. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
