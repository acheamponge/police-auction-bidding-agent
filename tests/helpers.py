"""Shared test helpers: fixture loading and a stand-in for the model's reply."""

import os

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def model_read(parsed, stance="bid", reasoning="stub", **overrides):
    """What Muse Ember would return if it read the page exactly as the parser
    did. Pass overrides to make its read differ on a field."""
    read = {
        "current_price": parsed["current_price"],
        "premium_pct": parsed["premium_pct"],
        "bid_increment": parsed["bid_increment"],
        "reserve_not_met": parsed["reserve_not_met"],
        "status": parsed["status"],
        "shipping_options": [dict(option) for option in parsed["shipping_options"]],
        "estimated_value_usd": None,
        "judgment": {"stance": stance, "risks": [], "reasoning": reasoning},
    }
    read.update(overrides)
    return read
