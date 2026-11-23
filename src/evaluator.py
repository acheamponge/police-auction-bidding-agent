"""The all-in math and the verdict. Money is deterministic; Ember judges.

All-in total for a hammer price H:
    premium  = H * premium_pct            (read off the lot page, never assumed)
    tax      = (H + premium) * tax_rate   (tax is NOT on the lot page — the
                                           rate is a configured assumption for
                                           the buyer's jurisdiction, labelled
                                           as such in every verdict)
    shipping = price of the chosen delivery method, read off the lot page
    all-in   = H + premium + tax + shipping

The most the agent may bid (max hammer) for a given all-in max:
    max_hammer = (all_in_max - shipping) / ((1 + premium_pct) * (1 + tax_rate))

Verdicts:
    BID             — the next bid clears the math and no blocking flag is set.
                      In dry-run mode this only PRINTS the exact bid.
    NO_BID          — the math does not clear, or the auction is closed.
    HOLD_FOR_HUMAN  — the all-in total cannot be fixed (premium / shipping /
                      tax unknown) or the lot carries the soft-reserve trap
                      and reserve lots are not allowed in config. A person
                      decides; the agent never bids blind.
"""

from __future__ import annotations

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
    all_in_max: float
    verdict: str
    reason: str
    risk_flags: list = field(default_factory=list)
    ember_stance: str | None = None
    ember_reasoning: str | None = None
    simulated: bool = False


def choose_shipping(parsed: dict, preference: str = "cheapest") -> tuple[str | None, float | None]:
    """Pick a delivery method off the lot page. 'cheapest' = lowest-priced
    available method (usually Customer Pick-up at $0.00)."""
    options = [o for o in parsed.get("shipping_options", []) if o.get("available")]
    if not options:
        return None, None
    if preference != "cheapest":
        for opt in options:
            if preference.lower() in opt["method"].lower():
                return opt["method"], opt["price_usd"]
    best = min(options, key=lambda o: o["price_usd"] if o["price_usd"] is not None else float("inf"))
    return best["method"], best["price_usd"]


def all_in_total(hammer: float, premium_frac: float, tax_rate: float,
                 shipping: float) -> float:
    """premium_frac is a fraction (0.15 = 15%); the lot page reports it as
    a percent (15.00), so callers divide by 100 first."""
    premium = hammer * premium_frac
    tax = (hammer + premium) * tax_rate
    return round(hammer + premium + tax + shipping, 2)


def max_hammer_for(all_in_max: float, premium_frac: float, tax_rate: float,
                   shipping: float) -> float:
    return round((all_in_max - shipping) / ((1 + premium_frac) * (1 + tax_rate)), 2)


def evaluate(parsed: dict, ember_result: dict, lot_url: str, all_in_max: float,
             tax_rate: float | None, shipping_preference: str = "cheapest",
             allow_reserve_lots: bool = False) -> Evaluation:
    """Combine the deterministic parse with Ember's judgment into a verdict.

    Money fields come from the deterministic parse (numbers must be exact);
    Ember supplies the fuzzy judgment — reserve reading, condition risk, and
    a second opinion on the premium/shipping lines.
    """
    risks: list[str] = []
    for risk in (ember_result.get("judgment") or {}).get("risks", []):
        if risk not in risks:
            risks.append(risk)

    def _flag(text: str) -> None:
        if text not in risks:
            risks.append(text)

    premium_pct = parsed.get("premium_pct")
    premium_stated = bool(parsed.get("premium_stated"))
    current_price = parsed.get("current_price")
    bid_increment = parsed.get("bid_increment")
    status = parsed.get("status", "unknown")

    # The next bid needed to take the lead.
    next_bid = None
    if current_price is not None and bid_increment is not None:
        next_bid = round(current_price + bid_increment, 2)
    elif current_price is not None:
        next_bid = round(current_price, 2)

    method, shipping = choose_shipping(parsed, shipping_preference)

    if parsed.get("reserve_not_met"):
        _flag("SOFT RESERVE — seller has 1 business day after close to "
              "accept the high bid even if the reserve was never met; "
              "if they accept, you are committed and auto-charged")
    if parsed.get("condition") and "as-is" in parsed["condition"].lower():
        _flag("sold as-is, untested — condition risk is on the buyer")
    if parsed.get("deposit_required"):
        _flag("this auction may require a refundable bid deposit")
    if method is None:
        _flag("no deliverable shipping method stated on the page")
    if tax_rate is None:
        _flag("tax rate not configured — tax is not printed on the lot "
              "page, so the all-in total is a floor, not a ceiling")

    max_hammer = None
    all_in_next = None
    premium_frac = premium_pct / 100.0 if premium_pct is not None else None
    if (premium_frac is not None and tax_rate is not None
            and shipping is not None and next_bid is not None):
        max_hammer = max_hammer_for(all_in_max, premium_frac, tax_rate, shipping)
        all_in_next = all_in_total(next_bid, premium_frac, tax_rate, shipping)
        if all_in_next >= 5_000:
            _flag("all-in >= $5,000 — cards/PayPal capped at $4,999.99, "
                  "wire transfer only, due in full within 72 hours")

    # -- verdict, in order -----------------------------------------------
    if status == "ended":
        verdict, reason = "NO_BID", "Auction already ended — nothing to bid on."
    elif premium_pct is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "Buyer's premium is not stated on this lot page. "
                           "The all-in total cannot be fixed, so the agent "
                           "will not bid blind. A person checks the lot's "
                           "auction table / Bid Review page first.")
    elif shipping is None:
        verdict, reason = ("HOLD_FOR_HUMAN",
                           "No available delivery method on the lot page — "
                           "shipping cost unknown, all-in total unfixable.")
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
    elif next_bid > max_hammer:
        verdict, reason = ("NO_BID",
                           f"Math does not clear: the next bid ${next_bid:,.2f} "
                           f"would land all-in at ${all_in_next:,.2f}, over the "
                           f"${all_in_max:,.2f} max (max hammer bid is "
                           f"${max_hammer:,.2f}).")
    else:
        verdict, reason = ("BID",
                           f"Math clears: next bid ${next_bid:,.2f} lands "
                           f"all-in at ${all_in_next:,.2f}, inside the "
                           f"${all_in_max:,.2f} max (max hammer bid "
                           f"${max_hammer:,.2f}).")

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
        ember_stance=(ember_result.get("judgment") or {}).get("stance"),
        ember_reasoning=(ember_result.get("judgment") or {}).get("reasoning"),
        simulated=bool((ember_result.get("_meta") or {}).get("simulated")),
    )


def render(evaluation: Evaluation) -> str:
    """Human-readable verdict card."""
    e = evaluation
    lines = [
        f"Lot: {e.title or e.lot_url}",
        f"URL: {e.lot_url}",
        f"Status: {e.status}",
        f"Current price: ${e.current_price:,.2f}" if e.current_price is not None else "Current price: not read",
        f"Bid increment: ${e.bid_increment:,.2f}" if e.bid_increment is not None else "Bid increment: not read",
        (f"Buyer's premium: {e.premium_pct:.2f}% (read off the lot page)"
         if e.premium_stated else "Buyer's premium: NOT STATED on the lot page — never assumed"),
        (f"Tax: {e.tax_rate:.2%} assumed rate (not printed on lot pages — configured assumption)"
         if e.tax_rate is not None else "Tax: rate not configured"),
        (f"Shipping: {e.shipping_method} — ${e.shipping_cost:,.2f}"
         if e.shipping_method else "Shipping: no available method on the page"),
        f"All-in max: ${e.all_in_max:,.2f}",
        (f"Next bid to lead: ${e.next_bid:,.2f} -> all-in ${e.all_in_at_next_bid:,.2f} "
         f"(max hammer bid ${e.max_hammer:,.2f})"
         if e.next_bid is not None and e.all_in_at_next_bid is not None
         else "Next bid / all-in: cannot be computed from this page"),
        f"VERDICT: {e.verdict}",
        f"Reason: {e.reason}",
    ]
    if e.risk_flags:
        lines.append("Risk flags:")
        lines.extend(f"  - {flag}" for flag in e.risk_flags)
    if e.ember_stance:
        lines.append(f"Ember stance: {e.ember_stance}"
                     + (f" — {e.ember_reasoning}" if e.ember_reasoning else "")
                     + (" [SIMULATED — fixture mode, no MODEL_API_KEY]"
                        if e.simulated else " [live Muse Ember]"))
    return "\n".join(lines)
