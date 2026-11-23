"""Muse Ember client — every evaluation runs through the Meta Model API.

Model: muse-ember-1.0, OpenAI-compatible Chat Completions endpoint.
Base URL: https://api.meta.ai/v1
Key: MODEL_API_KEY environment variable (the $10 hackathon credits).

Pricing (Meta Model API, verified Nov 23, 2026 — prompts up to 200K tokens):
    input $0.10 / 1M tokens, cached input $0.004 / 1M, output $0.50 / 1M.
    Above 200K input tokens the whole request bills at $0.30 / $0.012 / $1.50.
Our prompts are ~2-4K tokens, so a single lot evaluation costs ~$0.0005.

No key? The agent runs in FIXTURE MODE (same pattern as SimCity's
fixture mode): a deterministic local evaluator returns the same JSON schema
Ember would, clearly marked simulated, at $0.00. Nothing is ever sent to a
model in fixture mode, and the cost ledger says so in plain words.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MODEL_ID = "muse-ember-1.0"
BASE_URL = os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1")
CHAT_COMPLETIONS_URL = BASE_URL.rstrip("/") + "/chat/completions"

# Meta Model API list prices, USD per 1M tokens (verified Nov 23, 2026).
PRICE_INPUT_PER_M = 0.10
PRICE_CACHED_INPUT_PER_M = 0.004
PRICE_OUTPUT_PER_M = 0.50
LONG_CONTEXT_THRESHOLD_TOKENS = 200_000
LONG_PRICE_INPUT_PER_M = 0.30
LONG_PRICE_CACHED_INPUT_PER_M = 0.012
LONG_PRICE_OUTPUT_PER_M = 1.50


def price_call(input_tokens: int, cached_input_tokens: int,
               output_tokens: int) -> float:
    """Cost of one call in USD. Long prompts bill the whole request at the
    higher tier — our prompts never get near 200K, but the rule is coded."""
    if input_tokens > LONG_CONTEXT_THRESHOLD_TOKENS:
        return ((input_tokens - cached_input_tokens) * LONG_PRICE_INPUT_PER_M
                + cached_input_tokens * LONG_PRICE_CACHED_INPUT_PER_M
                + output_tokens * LONG_PRICE_OUTPUT_PER_M) / 1_000_000
    return ((input_tokens - cached_input_tokens) * PRICE_INPUT_PER_M
            + cached_input_tokens * PRICE_CACHED_INPUT_PER_M
            + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000


EVALUATION_SYSTEM_PROMPT = """You are the evaluation brain of a police-auction bidding agent. \
You read one PropertyRoom.com lot page (pasted as text) and return ONLY a JSON object — no \
other text — with exactly these fields:

{
  "current_price": number or null,
  "premium_pct": number or null,        // e.g. 12.00 means 12%; null if the page states no premium line at all
  "premium_stated": true/false,         // true only if the page itself prints a buyer's premium rate
  "bid_increment": number or null,
  "reserve_not_met": true/false,        // true if the page says "Reserve Not Met" / seller approval
  "status": "live" or "ended",
  "ends_text": string or null,           // the ended/ending line as printed, if any
  "condition": string or null,           // e.g. "Untested - Sold as-is"
  "shipping_options": [{"method": string, "price_usd": number or null, "available": true/false}],
  "deposit_required": true/false,        // true if the page mentions a bid deposit
  "judgment": {
    "stance": "bid" | "no_bid" | "hold",
    "risks": [string],                   // short risk flags, e.g. "soft reserve", "as-is untested", "wire transfer over $5,000"
    "reasoning": string                  // one or two sentences, plain words
  }
}

Rules: never invent a premium rate — if the page prints no premium, premium_pct is null and \
premium_stated is false. Never invent shipping prices. If the page says the auction ended, \
status is "ended". The stance is your read on whether this lot is worth pursuing at all, \
before any budget math (the math is done deterministically outside you)."""

EVALUATION_USER_TEMPLATE = """Evaluate this PropertyRoom lot page. Return ONLY the JSON object described in the system prompt.

--- LOT PAGE TEXT ---
{lot_text}
--- END LOT PAGE TEXT ---"""


class EmberError(RuntimeError):
    pass


class EmberClient:
    """One Ember call per lot evaluation. Logs every call to the ledger."""

    def __init__(self, ledger=None, api_key: str | None = None,
                 model: str = MODEL_ID, timeout_s: int = 60):
        self.api_key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY")
        self.model = model
        self.timeout_s = timeout_s
        self.ledger = ledger
        self.live = bool(self.api_key)

    def evaluate_lot(self, lot_text: str, lot_url: str) -> dict:
        """Send the lot page to Muse Ember, return its JSON evaluation.

        In fixture mode (no MODEL_API_KEY) returns a deterministic local
        evaluation in the same schema, marked simulated.
        """
        if not self.live:
            result = self._fixture_evaluate(lot_text)
            result["_meta"] = {
                "model": self.model, "simulated": True,
                "note": ("No MODEL_API_KEY set — fixture mode. No model was "
                         "called; this is the deterministic local evaluator "
                         "returning the same schema Ember would."),
            }
            if self.ledger is not None:
                self.ledger.record(lot_url=lot_url, model=self.model,
                                   input_tokens=0, cached_input_tokens=0,
                                   output_tokens=0, cost_usd=0.0, simulated=True)
            return result

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
                {"role": "user",
                 "content": EVALUATION_USER_TEMPLATE.format(lot_text=lot_text[:12000])},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        req = urllib.request.Request(
            CHAT_COMPLETIONS_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EmberError(f"Meta Model API HTTP {exc.code}: "
                             f"{exc.read().decode('utf-8', 'replace')[:500]}") from exc
        except urllib.error.URLError as exc:
            raise EmberError(f"Meta Model API unreachable: {exc}") from exc

        usage = payload.get("usage", {}) or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached = 0
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens", 0))
        cost = price_call(input_tokens, cached, output_tokens)

        if self.ledger is not None:
            self.ledger.record(lot_url=lot_url, model=self.model,
                               input_tokens=input_tokens,
                               cached_input_tokens=cached,
                               output_tokens=output_tokens,
                               cost_usd=cost, simulated=False)

        content = payload["choices"][0]["message"]["content"]
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise EmberError(f"Ember returned non-JSON: {content[:300]}") from exc
        result["_meta"] = {"model": self.model, "simulated": False,
                           "usage": usage, "cost_usd": cost}
        return result

    # -- fixture mode: deterministic local evaluator, same schema ----------
    @staticmethod
    def _fixture_evaluate(lot_text: str) -> dict:
        from .lot_fetcher import parse_lot_text  # local import, avoids cycle
        parsed = parse_lot_text(lot_text)
        risks = []
        if parsed.get("reserve_not_met"):
            risks.append("SOFT RESERVE — seller has 1 business day after close to "
                         "accept the high bid even if the reserve was never met; "
                         "if they accept, you are committed and auto-charged")
        if parsed.get("condition") and "as-is" in parsed["condition"].lower():
            risks.append("sold as-is, untested — condition risk is on the buyer")
        if parsed.get("premium_pct") is None:
            risks.append("no buyer's premium stated on the page — all-in total cannot be fixed")
        stance = "hold"
        if parsed.get("status") == "ended":
            stance = "no_bid"
            risks.append("auction already ended")
        elif parsed.get("premium_pct") is not None and not parsed.get("reserve_not_met"):
            stance = "bid"
        return {
            "current_price": parsed.get("current_price"),
            "premium_pct": parsed.get("premium_pct"),
            "premium_stated": parsed.get("premium_pct") is not None,
            "bid_increment": parsed.get("bid_increment"),
            "reserve_not_met": bool(parsed.get("reserve_not_met")),
            "status": parsed.get("status", "unknown"),
            "ends_text": parsed.get("ends_text"),
            "condition": parsed.get("condition"),
            "shipping_options": parsed.get("shipping_options", []),
            "deposit_required": bool(parsed.get("deposit_required")),
            "judgment": {
                "stance": stance,
                "risks": risks,
                "reasoning": ("Fixture-mode read of the lot page. Live Ember "
                              "would return this same schema from the model."),
            },
        }
