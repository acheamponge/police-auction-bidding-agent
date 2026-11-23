"""Cost ledger: every Muse Ember call is logged, and the $10 budget is a wall.

The ledger is a JSONL file — one line per model call — plus a running total.
`check_budget()` is the wall: the client calls it BEFORE each request with a
high estimate of the call's cost, and it refuses a call that could push
cumulative spend over the budget (default $10.00, the entire hackathon
budget). `record()` then writes down what the call really cost. It never
refuses, because by then the money is spent and the ledger must show it.

Ledger lines carry: timestamp, lot URL, model, input/cached/output tokens,
cost, simulated flag, and the running total after the call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_BUDGET_USD = 10.00


class BudgetExceeded(RuntimeError):
    pass


class CostLedger:
    def __init__(self, path: str, budget_usd: float = DEFAULT_BUDGET_USD):
        self.path = path
        self.budget_usd = budget_usd
        self.total_usd = 0.0
        self.calls = 0
        self.simulated_calls = 0
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "call":
                    self.calls += 1
                    self.total_usd += float(entry.get("cost_usd", 0.0))
                    if entry.get("simulated"):
                        self.simulated_calls += 1

    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.total_usd)

    def check_budget(self, estimated_cost_usd: float) -> None:
        if self.total_usd + estimated_cost_usd > self.budget_usd:
            raise BudgetExceeded(
                f"Refusing call: ${self.total_usd:.4f} spent + "
                f"~${estimated_cost_usd:.4f} would exceed the "
                f"${self.budget_usd:.2f} budget.")

    def record(self, *, lot_url: str, model: str, input_tokens: int,
               cached_input_tokens: int, output_tokens: int,
               cost_usd: float, simulated: bool) -> dict:
        self.total_usd += cost_usd
        self.calls += 1
        if simulated:
            self.simulated_calls += 1
        entry = {
            "type": "call",
            "ts": datetime.now(timezone.utc).isoformat(),
            "lot_url": lot_url,
            "model": model,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "simulated": simulated,
            "running_total_usd": round(self.total_usd, 6),
            "budget_usd": self.budget_usd,
            "remaining_usd": round(self.remaining_usd(), 6),
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry

    def summary(self) -> dict:
        return {
            "budget_usd": self.budget_usd,
            "total_spent_usd": round(self.total_usd, 6),
            "remaining_usd": round(self.remaining_usd(), 6),
            "calls": self.calls,
            "simulated_calls": self.simulated_calls,
            "live_calls": self.calls - self.simulated_calls,
        }
