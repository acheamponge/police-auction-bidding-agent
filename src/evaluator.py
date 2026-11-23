"""The all-in math, the verdict and the bid plan. Money is deterministic;
Ember can only veto.

All-in total for a hammer price H:
    premium  = H * premium_pct            (read off the lot page, never assumed;
                                           none when the auction table states
                                           none, which is PropertyRoom's rule)
    tax      = (H + premium) * tax_rate   (tax is NOT on the lot page — the
                                           rate is a configured assumption for
                                           the buyer's jurisdiction, labelled
                                           as such in every verdict)
    shipping = price of the chosen delivery method, read off the lot page
               (plus your own cost of collecting it, for Customer Pick-up)
    all-in   = H + premium + tax + shipping

The most the agent may bid (max hammer) for a given all-in max is the largest
whole-cent H whose all-in total is still within the max.

Muse Ember reads the same page independently. Its read can only make the
agent more careful, never less:
    - if its numbers disagree with the deterministic parse, the lot is held;
    - if its stance on the listing is not "bid", the lot is held;
    - if the all-in total is above its top value estimate, the lot is held.

The bid plan. PropertyRoom takes proxy bids: you set a maximum and it bids
the lowest amount needed to lead, never more. A bid in an auction's last
minute extends the auction, so there is nothing to gain from bidding late.
The plan is therefore a proxy bid whose maximum is the highest whole-dollar
hammer price that keeps the all-in total inside every limit: your all-in max,
the top of Ember's value estimate, and what the spending policy has left.
One exception: when the reserve is not met, PropertyRoom bids a proxy's full
maximum at once, so the agent places a plain bid at the next increment.

With no all-in max the agent never bids. It reports what Ember's value
estimate would support and holds for a human.

Verdicts:
    BID             — the next bid clears the math, both reads of the page
                      agree, and Ember raised no objection. In dry-run mode
                      this only PRINTS the exact bid.
    NO_BID          — the math does not clear, or the auction is closed.
    HOLD_FOR_HUMAN  — anything the agent cannot be sure of: the auction status,
                      premium, shipping or tax is unknown, no all-in max was
                      given, the two reads disagree, Ember objects, or the
                      lot carries the soft-reserve trap and reserve lots are
                      not allowed.
                      A person decides; the agent never bids blind.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Cards / PayPal cap on PropertyRoom: $4,999.99. At $5,000+ only wire works.
CARD_PAYMENT_CAP_USD = 4_999.99


@dataclass
class Evaluation:
    lot_url: str
    title: str | None
    status: str
    current_price: float | None
    bid_increment: float | None
    next_bid: float | None
    premium_pct: float | None
    premium_stated: bool
    tax_rate: float | None
    shipping_method: str | None
    shipping_cost: float | None
    max_hammer: float | None
    all_in_at_next_bid: float | None
    all_in_max: float | None
    verdict: str
    reason: str
    risk_flags: list = field(default_factory=list)
    ember_stance: str | None = None
    ember_reasoning: str | None = None
    simulated: bool = False
    pickup_cost_usd: float | None = None
    value_estimate_low: float | None = None
    value_estimate_high: float | None = None
    value_basis: str | None = None
    premium_absent: bool = False
    suggested_all_in_max: float | None = None
    # The bid plan, set only when the verdict is BID.
    bid_amount: float | None = None      # what to enter on the site
    bid_is_proxy: bool = False           # True: bid_amount is a proxy maximum
    all_in_at_bid: float | None = None   # the most this bid can cost, all-in
    bid_note: str | None = None


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _is_pickup(method: str) -> bool:
    return "pickup" in _letters(method)


def choose_shipping(parsed: dict, preference: str = "cheapest",
                    pickup_cost_usd: float | None = None) -> tuple[str | None, float | None]:
    """Pick a delivery method off the lot page and return (method, cost).

    Customer Pick-up is never free in practice: someone has to travel to the
    lot or pay to have it hauled. So pickup is only eligible when
    pickup_cost_usd is set (0 if you are local), and that amount is added to
    the pickup fee printed on the page. A named preference that the page does
    not offer returns no method rather than silently switching to another.
    """
    candidates = []
    for option in parsed.get("shipping_options", []):
        if not option.get("available") or option.get("price_usd") is None:
            continue
        if _is_pickup(option["method"]):
            if pickup_cost_usd is None:
                continue
            candidates.append((option["method"], round(option["price_usd"] + pickup_cost_usd, 2)))
        else:
            candidates.append((option["method"], option["price_usd"]))
    if preference != "cheapest":
        candidates = [c for c in candidates if _letters(preference) in _letters(c[0])]
    if not candidates:
        return None, None
    return min(candidates, key=lambda c: c[1])


def all_in_total(hammer: float, premium_frac: float, tax_rate: float,
                 shipping: float) -> float:
    """premium_frac is a fraction (0.15 = 15%); the lot page reports it as
    a percent (15.00), so callers divide by 100 first."""
    premium = hammer * premium_frac
    tax = (hammer + premium) * tax_rate
    return round(hammer + premium + tax + shipping, 2)


def max_hammer_for(all_in_max: float, premium_frac: float, tax_rate: float,
                   shipping: float) -> float:
    """The largest whole-cent hammer price whose all-in total is within
    all_in_max. Rounded down so it can never land a cent over; the one cent
    above is taken only when its all-in total (itself rounded to cents)
    still fits."""
    cents = math.floor((all_in_max - shipping) / ((1 + premium_frac) * (1 + tax_rate)) * 100)
    if all_in_total((cents + 1) / 100, premium_frac, tax_rate, shipping) <= all_in_max:
        cents += 1
    return max(cents, 0) / 100


def _number(value) -> float | None:
    """A number from the model's JSON. Accepts 15, 15.0, "15.00%", "$4,450.00"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace("$", "").replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
    return None


def _same_number(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) < 0.005


def _model_shipping_option(ember_result: dict, method: str) -> dict | None:
    wanted = _letters(method)
    for option in ember_result.get("shipping_options") or []:
        if not isinstance(option, dict):
            continue
        name = _letters(str(option.get("method") or ""))
        if name and (name in wanted or wanted in name):
            return option
    return None


def disagreements(parsed: dict, ember_result: dict) -> list[str]:
    """Where Ember's read of the page differs from the deterministic parse.
    An empty list means the two independent reads agree on every money field."""
    found = []

    def _show(value) -> str:
        return "nothing" if value is None else str(value)

    if parsed.get("status") != ember_result.get("status"):
        found.append(f"auction status (parser: {_show(parsed.get('status'))}, "
                     f"Ember: {_show(ember_result.get('status'))})")
    for key, label in (("current_price", "current price"),
                       ("premium_pct", "buyer's premium %"),
                       ("bid_increment", "bid increment")):
        ours, theirs = parsed.get(key), _number(ember_result.get(key))
        if not _same_number(ours, theirs):
            found.append(f"{label} (parser: {_show(ours)}, Ember: {_show(theirs)})")
    if bool(parsed.get("reserve_not_met")) != bool(ember_result.get("reserve_not_met")):
        found.append(f"reserve not met (parser: {bool(parsed.get('reserve_not_met'))}, "
                     f"Ember: {bool(ember_result.get('reserve_not_met'))})")
    for option in parsed.get("shipping_options", []):
        if not option.get("available"):
            continue
        theirs = _model_shipping_option(ember_result, option["method"])
        their_price = _number(theirs.get("price_usd")) if theirs else None
        if not _same_number(option.get("price_usd"), their_price):
            found.append(f"{option['method']} price (parser: {_show(option.get('price_usd'))}, "
                         f"Ember: {_show(their_price)})")
    return found


def plan_bid(next_bid: float, reserve_not_met: bool, limits: list,
             premium_frac: float, tax_rate: float, shipping: float) -> tuple[float, bool, str]:
    """Decide what to enter on the site: (amount, is_proxy, note).

    `limits` is a list of (name, all-in dollars); the tightest one sets the
    proxy maximum.
    """
    if reserve_not_met:
        return next_bid, False, (
            "Plain bid, not a proxy: the reserve is not met, and PropertyRoom "
            "bids a proxy's full maximum at once when it is below the reserve.")
    limit_name, limit = min(limits, key=lambda item: item[1])
    ceiling = float(math.floor(max_hammer_for(limit, premium_frac, tax_rate, shipping)))
    if ceiling <= next_bid:
        next_all_in = all_in_total(next_bid, premium_frac, tax_rate, shipping)
        if next_all_in > limit:
            return next_bid, False, (
                f"No room for a proxy: the next bid's all-in of ${next_all_in:,.2f} "
                f"is over {limit_name} (${limit:,.2f}).")
        return next_bid, False, (
            f"Plain bid: the next bid is already at the ceiling set by "
            f"{limit_name} (${limit:,.2f} all-in).")
    return ceiling, True, (
        f"Proxy bid with a maximum of ${ceiling:,.2f}. PropertyRoom bids the "
        f"lowest amount needed to lead (${next_bid:,.2f} now) and never more "
        f"than the maximum. The ceiling is set by {limit_name} "
        f"(${limit:,.2f} all-in).")


def evaluate(parsed: dict, ember_result: dict, lot_url: str, all_in_max: float | None,
             tax_rate: float | None, shipping_preference: str = "cheapest",
             allow_reserve_lots: bool = False,
             pickup_cost_usd: float | None = None,
             spend_limit_usd: float | None = None) -> Evaluation:
    """Combine the deterministic parse with Ember's read into a verdict.

    The money numbers come from the deterministic parse. Ember's read is the
    second opinion: it is compared field by field, and its stance and value
    estimate can turn a BID into a HOLD_FOR_HUMAN, never the other way.

    all_in_max may be None: the agent then never bids, and suggests a max
    from Ember's low value estimate. spend_limit_usd is what the spending
    policy has left; it caps a proxy bid's maximum.
    """
    judgment = ember_result.get("judgment") or {}
    risks: list[str] = []

    def _flag(text: str) -> None:
        if text and text not in risks:
            risks.append(text)

    for risk in judgment.get("risks") or []:
        _flag(str(risk))

    premium_pct = parsed.get("premium_pct")
    premium_stated = bool(parsed.get("premium_stated"))
    premium_absent = premium_pct is None and bool(parsed.get("premium_absent"))
    current_price = parsed.get("current_price")
    bid_increment = parsed.get("bid_increment")
    status = parsed.get("status", "unknown")

    # The next bid needed to take the lead.
    next_bid = None
    if current_price is not None and bid_increment is not None:
        next_bid = round(current_price + bid_increment, 2)
    elif current_price is not None:
        next_bid = round(current_price, 2)

    method, shipping = choose_shipping(parsed, shipping_preference, pickup_cost_usd)
    pickup_offered = any(o.get("available") and _is_pickup(o["method"])
                         for o in parsed.get("shipping_options", []))
    used_pickup = method is not None and _is_pickup(method)

    stance = judgment.get("stance")
    reasoning = judgment.get("reasoning")
    value = ember_result.get("estimated_value_usd")
    value = value if isinstance(value, dict) else {}
    value_low, value_high = _number(value.get("low")), _number(value.get("high"))
    value_basis = value.get("basis") if isinstance(value.get("basis"), str) else None
    mismatches = disagreements(parsed, ember_result)
    simulated = bool((ember_result.get("_meta") or {}).get("simulated"))
    suggested_max = value_low if all_in_max is None else None
    budget = all_in_max if all_in_max is not None else suggested_max

    if parsed.get("reserve_not_met"):
        _flag("SOFT RESERVE — seller has 1 business day after close to "
              "accept the high bid even if the reserve was never met; "
              "if they accept, you are committed and auto-charged")
    if parsed.get("condition") and "as-is" in parsed["condition"].lower():
        _flag("sold as-is, untested — condition risk is on the buyer")
    if parsed.get("deposit_required"):
        _flag("this auction may require a refundable bid deposit")
    if premium_absent:
        _flag("no buyer's premium in the auction table — the Bid Review "
              "page, shown before a bid is final, is the last word on fees")
    if method is None:
        _flag("no usable delivery method on the page")
    if tax_rate is None:
        _flag("tax rate not configured — tax is not printed on the lot "
              "page, so the all-in total is a floor, not a ceiling")
    for mismatch in mismatches:
        _flag(f"the two reads of the page disagree on {mismatch}")

    max_hammer = None
    all_in_next = None
    if premium_pct is not None:
        premium_frac = premium_pct / 100.0
    else:
        premium_frac = 0.0 if premium_absent else None
    if (premium_frac is not None and tax_rate is not None
            and shipping is not None and next_bid is not None):
        if budget is not None:
            max_hammer = max_hammer_for(budget, premium_frac, tax_rate, shipping)
        all_in_next = all_in_total(next_bid, premium_frac, tax_rate, shipping)
        if all_in_next >= 5_000:
            _flag("all-in >= $5,000 — cards/PayPal capped at $4,999.99, "
                  "wire transfer only, due in full within 72 hours")

    bid_amount, bid_is_proxy, all_in_at_bid, bid_note = None, False, None, None

    # -- verdict, in order -----------------------------------------------
    if status == "ended":
        verdict, reason = "NO_BID", "Auction already ended — nothing to bid on."
    elif status != "live":
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "Could not tell from the page whether this auction "
                           "is still running. The agent only bids on a lot "
                           "that prints an \"Ends <date>\" line.")
    elif mismatches:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "Muse Ember's read of the page disagrees with the "
                           "deterministic parse on: " + "; ".join(mismatches)
                           + ". One of them misread the page, so a person "
                           "checks it before any bid.")
    elif premium_frac is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "This lot page mentions a buyer's premium but no "
                           "rate could be read from it. The all-in total "
                           "cannot be fixed, so the agent will not bid blind. "
                           "A person checks the lot's auction table / Bid "
                           "Review page first.")
    elif shipping is None and pickup_offered and pickup_cost_usd is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "The only way to get this lot is Customer Pick-up, "
                           "and no pickup cost is configured. Collecting it is "
                           "not free unless you are local: set a pickup cost "
                           "(0 if you are) before bidding.")
    elif shipping is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "No usable delivery method on the lot page for the "
                           f"\"{shipping_preference}\" preference — shipping "
                           "cost unknown, all-in total unfixable.")
    elif tax_rate is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "No tax rate configured. Tax is not printed on "
                           "PropertyRoom lot pages; set tax_rate for the "
                           "ship-to jurisdiction before bidding.")
    elif parsed.get("reserve_not_met") and not allow_reserve_lots:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "Reserve not met (soft reserve). The seller can "
                           "accept the high bid up to 1 business day after "
                           "close even though the reserve was never met — and "
                           "acceptance commits you and auto-charges you. "
                           "Reserve lots need a human go-ahead.")
    elif next_bid is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "Could not read the current price or bid increment "
                           "off the lot page.")
    elif all_in_max is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "No all-in max was given, so the agent will not bid. "
                           + (f"Muse Ember values this lot at ${value_low:,.2f} to "
                              f"${value_high:,.2f}. A max of ${suggested_max:,.2f}, "
                              f"its low estimate, would allow a hammer bid up to "
                              f"${max_hammer:,.2f}; the next bid lands all-in at "
                              f"${all_in_next:,.2f}"
                              + (", which is already past it" if all_in_next > suggested_max else "")
                              + ". Re-run with that max, or your own, to act on it."
                              if suggested_max is not None and value_high is not None
                              else "There is no value estimate to suggest one "
                                   "from; pass your own max."))
    elif all_in_next > all_in_max:
        verdict, reason = ("NO_BID",
                           f"Math does not clear: the next bid ${next_bid:,.2f} "
                           f"would land all-in at ${all_in_next:,.2f}, over the "
                           f"${all_in_max:,.2f} max (max hammer bid is "
                           f"${max_hammer:,.2f}).")
    elif stance != "bid":
        verdict, reason = ("HOLD_FOR_HUMAN",
                           f"The math clears (all-in ${all_in_next:,.2f}), but "
                           f"Muse Ember's stance on the listing is "
                           f"\"{stance or 'missing'}\""
                           + (f": {reasoning}" if reasoning else ".")
                           + " A person decides.")
    elif value_high is not None and all_in_next > value_high:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           f"The math clears, but the all-in ${all_in_next:,.2f} "
                           f"is above Muse Ember's top value estimate of "
                           f"${value_high:,.2f}. A person decides whether it "
                           "is worth it.")
    else:
        verdict, reason = ("BID",
                           f"Math clears: next bid ${next_bid:,.2f} lands "
                           f"all-in at ${all_in_next:,.2f}, inside the "
                           f"${all_in_max:,.2f} max (max hammer bid "
                           f"${max_hammer:,.2f}). "
                           + ("No model checked this lot (fixture mode): there "
                              "was no second read and there is no value estimate."
                              if simulated else
                              "Both reads of the page agree and Muse Ember "
                              "raised no objection."))
        limits = [("your all-in max", all_in_max)]
        if value_high is not None:
            limits.append(("Muse Ember's top value estimate", value_high))
        if spend_limit_usd is not None:
            limits.append(("what the spending policy has left", spend_limit_usd))
        bid_amount, bid_is_proxy, bid_note = plan_bid(
            next_bid, bool(parsed.get("reserve_not_met")), limits,
            premium_frac, tax_rate, shipping)
        all_in_at_bid = all_in_total(bid_amount, premium_frac, tax_rate, shipping)

    return Evaluation(
        lot_url=lot_url,
        title=parsed.get("title"),
        status=status,
        current_price=current_price,
        bid_increment=bid_increment,
        next_bid=next_bid,
        premium_pct=premium_pct,
        premium_stated=premium_stated,
        tax_rate=tax_rate,
        shipping_method=method,
        shipping_cost=shipping,
        max_hammer=max_hammer,
        all_in_at_next_bid=all_in_next,
        all_in_max=all_in_max,
        verdict=verdict,
        reason=reason,
        risk_flags=risks,
        ember_stance=stance,
        ember_reasoning=reasoning,
        simulated=simulated,
        pickup_cost_usd=pickup_cost_usd if used_pickup else None,
        value_estimate_low=value_low,
        value_estimate_high=value_high,
        value_basis=value_basis,
        premium_absent=premium_absent,
        suggested_all_in_max=suggested_max,
        bid_amount=bid_amount,
        bid_is_proxy=bid_is_proxy,
        all_in_at_bid=all_in_at_bid,
        bid_note=bid_note,
    )


def _gap(cost: float, value: float) -> str:
    """How far a cost sits from a value: "$400.80 below" / "$599.20 above"."""
    return f"${abs(value - cost):,.2f} {'below' if cost <= value else 'above'}"


def render(evaluation: Evaluation) -> str:
    """Human-readable verdict card."""
    e = evaluation
    shipping_line = "Shipping: no usable delivery method on the page"
    if e.shipping_method:
        shipping_line = f"Shipping: {e.shipping_method} — ${e.shipping_cost:,.2f}"
        if e.pickup_cost_usd is not None:
            shipping_line += (f" (page fee plus your configured pickup cost of "
                              f"${e.pickup_cost_usd:,.2f})")
    if e.premium_stated:
        premium_line = f"Buyer's premium: {e.premium_pct:.2f}% (read off the lot page)"
    elif e.premium_absent:
        premium_line = ("Buyer's premium: none (the auction table states none; "
                        "PropertyRoom states it there when a lot has one)")
    else:
        premium_line = "Buyer's premium: mentioned on the page but no rate could be read"
    if e.all_in_max is not None:
        max_line = f"All-in max: ${e.all_in_max:,.2f}"
    elif e.suggested_all_in_max is not None:
        max_line = (f"All-in max: not given (suggested: ${e.suggested_all_in_max:,.2f}, "
                    "Ember's low value estimate)")
    else:
        max_line = "All-in max: not given"
    if e.next_bid is None or e.all_in_at_next_bid is None:
        next_line = "Next bid / all-in: cannot be computed from this page"
    else:
        next_line = f"Next bid to lead: ${e.next_bid:,.2f} -> all-in ${e.all_in_at_next_bid:,.2f}"
        if e.max_hammer is not None:
            next_line += f" (max hammer bid ${e.max_hammer:,.2f})"
    lines = [
        f"Lot: {e.title or e.lot_url}",
        f"URL: {e.lot_url}",
        f"Status: {e.status}",
        f"Current price: ${e.current_price:,.2f}" if e.current_price is not None else "Current price: not read",
        f"Bid increment: ${e.bid_increment:,.2f}" if e.bid_increment is not None else "Bid increment: not read",
        premium_line,
        (f"Tax: {e.tax_rate:.2%} assumed rate (not printed on lot pages — configured assumption)"
         if e.tax_rate is not None else "Tax: rate not configured"),
        shipping_line,
        max_line,
        next_line,
        f"VERDICT: {e.verdict}",
        f"Reason: {e.reason}",
    ]
    if e.bid_note:
        lines.append(f"Bid plan: {e.bid_note} All-in at most ${e.all_in_at_bid:,.2f}.")
    if e.risk_flags:
        lines.append("Risk flags:")
        lines.extend(f"  - {flag}" for flag in e.risk_flags)
    source = (" [SIMULATED — fixture mode, no model called]"
              if e.simulated else " [live Muse Ember]")
    if e.value_estimate_low is not None and e.value_estimate_high is not None:
        lines.append(f"Ember value estimate: ${e.value_estimate_low:,.2f} to "
                     f"${e.value_estimate_high:,.2f}"
                     + (f" — {e.value_basis}" if e.value_basis else "") + source)
        if e.all_in_at_next_bid is not None:
            lines.append("Value vs cost: the next bid's all-in is "
                         + _gap(e.all_in_at_next_bid, e.value_estimate_low) + " the low estimate and "
                         + _gap(e.all_in_at_next_bid, e.value_estimate_high) + " the high estimate")
    if e.ember_stance:
        lines.append(f"Ember stance: {e.ember_stance}"
                     + (f" — {e.ember_reasoning}" if e.ember_reasoning else "")
                     + source)
    return "\n".join(lines)
