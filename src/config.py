"""Local settings: the .env file (the API key) and config.json (how to bid).

Both files are optional and neither is ever committed. Command-line flags
win over config.json, and variables already set in the environment win over
.env.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .policy import PolicyConfig


def load_env(path: str) -> None:
    """Read KEY=VALUE lines from a .env file into the environment. A variable
    that is already set is left alone."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and value:
                os.environ.setdefault(key, value)


@dataclass
class Settings:
    tax_rate: float | None = None
    shipping_preference: str = "cheapest"
    pickup_cost_usd: float | None = None
    all_in_max_default_usd: float | None = None
    policy: PolicyConfig = field(default_factory=PolicyConfig)


def load_settings(path: str) -> Settings:
    """Read config.json. A missing file gives the defaults: no tax rate and
    no pickup cost, which means the agent holds lots that depend on them."""
    if not os.path.exists(path):
        return Settings()
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    policy = raw.get("policy") or {}
    defaults = PolicyConfig()
    return Settings(
        tax_rate=raw.get("tax_rate"),
        shipping_preference=raw.get("shipping_preference") or "cheapest",
        pickup_cost_usd=raw.get("pickup_cost_usd"),
        all_in_max_default_usd=raw.get("all_in_max_default_usd"),
        policy=PolicyConfig(
            tx_cap_usd=policy.get("tx_cap_usd", defaults.tx_cap_usd),
            session_budget_usd=policy.get("session_budget_usd", defaults.session_budget_usd),
            allow_reserve_lots=bool(policy.get("allow_reserve_lots", False)),
            live_bidding_configured=bool((raw.get("live_bidding") or {}).get("configured")),
        ),
    )
