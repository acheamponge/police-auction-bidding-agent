"""Muse Ember client — every evaluation runs through the Meta Model API.

Model: muse-ember-1.0, OpenAI-compatible Chat Completions endpoint.
Base URL: https://api.meta.ai/v1 (override with MODEL_API_BASE)
Key: MODEL_API_KEY environment variable (the $10 hackathon credits).

Pricing (Meta Model API, verified Nov 23, 2026 — prompts up to 200K tokens):
    input $0.10 / 1M tokens, cached input $0.004 / 1M, output $0.50 / 1M.
    Above 200K input tokens the whole request bills at $0.30 / $0.012 / $1.50.

The budget is checked BEFORE each request, against a deliberately high
estimate of what the call could cost (prompt length plus the full output
cap). A call that could cross the budget is never sent. What a call actually
cost is always written to the ledger afterwards.

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

from .lot_fetcher import lot_body, parse_lot_text

MODEL_ID = "muse-ember-1.0"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"

# Meta Model API list prices, USD per 1M tokens (verified Nov 23, 2026).
PRICE_INPUT_PER_M = 0.10
PRICE_CACHED_INPUT_PER_M = 0.004
PRICE_OUTPUT_PER_M = 0.50
LONG_CONTEXT_THRESHOLD_TOKENS = 200_000
LONG_PRICE_INPUT_PER_M = 0.30
LONG_PRICE_CACHED_INPUT_PER_M = 0.012
LONG_PRICE_OUTPUT_PER_M = 1.50

# Muse Ember thinks before it answers, and the thinking is billed as output
# tokens and counts against the output cap. "low" is plenty for reading one
# lot page (the API accepts minimal / low / medium / high / xhigh / max).
REASONING_EFFORT = "low"
# Output cap sent with every request, so the worst-case cost of a call is known
# before it is made. It has to hold the thinking AND the JSON answer: at 1,200
# the model spent the whole cap thinking and returned no answer at all.
MAX_OUTPUT_TOKENS = 4_000
# Conservative on purpose: English text runs about 4 characters per token.
CHARS_PER_TOKEN_FOR_ESTIMATE = 3


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


def worst_case_cost(prompt_chars: int) -> float:
    """An upper estimate of one call's cost, used for the pre-call budget check."""
    return price_call(prompt_chars // CHARS_PER_TOKEN_FOR_ESTIMATE + 1, 0,
                      MAX_OUTPUT_TOKENS)


EVALUATION_SYSTEM_PROMPT = """You are the second pair of eyes for a police-auction bidding agent. \
You read one PropertyRoom.com lot page (pasted as text) and return ONLY a JSON object — no \
other text — with exactly these fields:

{
  "current_price": number or null,
  "premium_pct": number or null,        // e.g. 12.00 means 12%; null if the page states no premium line at all
  "premium_stated": true/false,         // true only if the page itself prints a buyer's premium rate
  "bid_increment": number or null,
  "reserve_not_met": true/false,        // true if the page says "Reserve Not Met" / seller approval
  "status": "live" or "ended",          // "live" if the page prints "Ends <date>", "ended" if it prints "Ended <date>"
  "ends_text": string or null,           // the "Ends ..." / "Ended ..." line as printed, if any
  "condition": string or null,           // e.g. "Untested - Sold as-is"
  "shipping_options": [{"method": string, "price_usd": number or null, "available": true/false}],
  "deposit_required": true/false,        // true if the page mentions a bid deposit
  "estimated_value_usd": {"low": number, "high": number} or null,
                                         // what this exact item, in this condition, would fairly sell for; null if you cannot tell
  "judgment": {
    "stance": "bid" | "no_bid" | "hold",
    "risks": [string],                   // short risk flags, e.g. "no keys", "does not start", "salvage title"
    "reasoning": string                  // one or two sentences, plain words
  }
}

Rules: copy the numbers exactly as the page prints them. Never invent a premium rate — if \
the page prints no premium, premium_pct is null and premium_stated is false. Never invent \
shipping prices; list every delivery method in the page's shipping table, with "available": \
false and a null price where it says "Not Available".

Your stance is about the item itself, the things the numbers cannot see. Use "hold" when a \
careful buyer would want to look closer first and "no_bid" when it should be avoided, and base \
it on specific red flags the listing states or leaves blank: it does not start or run, or \
that is unknown; no key; no title, or a salvage / parts-only title; mileage unknown or not \
credible; specific damage or missing parts called out; a description that does not match the \
item. Boilerplate that every PropertyRoom listing carries is NOT a red flag on its own: "sold \
as-is", "untested", "not inspected by a certified mechanic", and damage fields that only say \
"see images" (you cannot see the images; list that as a risk, but do not hold for it alone). \
Do NOT hold because of the reserve, the fees, the price or the buyer's budget — those are \
checked deterministically outside you. Your read can only make the agent more careful: a \
"bid" stance never forces a bid."""

EVALUATION_USER_TEMPLATE = """Evaluate this PropertyRoom lot page. Return ONLY the JSON object described in the system prompt.

--- LOT PAGE TEXT ---
{lot_text}
--- END LOT PAGE TEXT ---"""


class EmberError(RuntimeError):
    pass


def post_json(url: str, body: dict, headers: dict, timeout_s: int) -> dict:
    """POST a JSON body and return the decoded JSON reply."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EmberError(f"Meta Model API HTTP {exc.code}: "
                         f"{exc.read().decode('utf-8', 'replace')[:500]}") from exc
    except urllib.error.URLError as exc:
        raise EmberError(f"Meta Model API unreachable: {exc}") from exc


class EmberClient:
    """One Ember call per lot evaluation. Logs every call to the ledger.

    `transport` is the function that performs the HTTP POST; tests pass a
    fake one so no network or key is needed.
    """

    def __init__(self, ledger=None, api_key: str | None = None,
                 model: str = MODEL_ID, timeout_s: int = 60,
                 base_url: str | None = None, transport=post_json):
        self.api_key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY")
        self.model = model
        self.timeout_s = timeout_s
        self.ledger = ledger
        self.live = bool(self.api_key)
        self.url = (base_url or os.environ.get("MODEL_API_BASE")
                    or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
        self.transport = transport

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

        user_prompt = EVALUATION_USER_TEMPLATE.format(lot_text=lot_body(lot_text))
        if self.ledger is not None:
            self.ledger.check_budget(
                worst_case_cost(len(EVALUATION_SYSTEM_PROMPT) + len(user_prompt)))

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "reasoning_effort": REASONING_EFFORT,
        }
        payload = self.transport(
            self.url, body,
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {self.api_key}"},
            self.timeout_s)

        usage = payload.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        cost = price_call(input_tokens, cached, output_tokens)

        if self.ledger is not None:
            self.ledger.record(lot_url=lot_url, model=self.model,
                               input_tokens=input_tokens,
                               cached_input_tokens=cached,
                               output_tokens=output_tokens,
                               cost_usd=cost, simulated=False)

        choice = payload["choices"][0]
        content = choice["message"].get("content")
        if not content:
            raise EmberError(
                "Ember returned no answer (finish_reason: "
                f"{choice.get('finish_reason')}, {output_tokens} output tokens "
                "used). If that is \"length\", its thinking used up the output cap.")
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise EmberError(f"Ember returned non-JSON: {content[:300]}") from exc
        if not isinstance(result, dict):
            raise EmberError(f"Ember returned JSON that is not an object: {content[:300]}")
        result["_meta"] = {"model": self.model, "simulated": False,
                           "usage": usage, "cost_usd": cost}
        return result

    # -- fixture mode: deterministic local evaluator, same schema ----------
    @staticmethod
    def _fixture_evaluate(lot_text: str) -> dict:
        parsed = parse_lot_text(lot_text)
        risks = []
        if parsed.get("premium_pct") is None:
            risks.append("no buyer's premium stated on the page — all-in total cannot be fixed")
        stance = "hold"
        if parsed.get("status") == "ended":
            stance = "no_bid"
            risks.append("auction already ended")
        elif parsed.get("status") == "live" and parsed.get("premium_pct") is not None:
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
            "estimated_value_usd": None,
            "judgment": {
                "stance": stance,
                "risks": risks,
                "reasoning": ("Fixture-mode read of the lot page. Live Ember "
                              "would return this same schema from the model."),
            },
        }
