"""What the command-line scripts share: the settings flags, and building an
agent from config.json, .env and those flags (a flag wins over config.json).
"""

from __future__ import annotations

import argparse
import dataclasses
import os

from .agent import BiddingAgent
from .config import Settings, load_env, load_settings
from .cost_ledger import CostLedger


def add_setting_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--ledger", default=None,
                        help="cost ledger path (default: cost_ledger.jsonl)")


def build_agent(args: argparse.Namespace, project_dir: str,
                dry_run: bool = True) -> tuple[BiddingAgent, Settings]:
    """Load .env and config.json from project_dir and build the agent."""
    load_env(os.path.join(project_dir, ".env"))
    settings = load_settings(os.path.join(project_dir, "config.json"))
    agent = BiddingAgent(
        ledger=CostLedger(args.ledger or os.path.join(project_dir, "cost_ledger.jsonl")),
        tax_rate=args.tax_rate if args.tax_rate is not None else settings.tax_rate,
        shipping_preference=args.shipping or settings.shipping_preference,
        pickup_cost_usd=(args.pickup_cost if args.pickup_cost is not None
                         else settings.pickup_cost_usd),
        policy_config=dataclasses.replace(
            settings.policy,
            dry_run=dry_run,
            allow_reserve_lots=args.allow_reserve or settings.policy.allow_reserve_lots,
        ),
    )
    return agent, settings
