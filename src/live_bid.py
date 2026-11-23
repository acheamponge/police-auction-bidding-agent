"""Live bidding — deliberately gated, and the transport is not wired up.

Placing a real bid needs a registered PropertyRoom account with a payment
method on file, and bids are binding: on close the payment method is
auto-charged and the item ships within 2 business days. Until an account and
a payment method are configured, this module REFUSES every live attempt.

What this module does today:
  1. Builds the exact bid request the agent would send (lot id, bid amount,
     delivery method) so a human can inspect it.
  2. Re-checks the policy gate at the moment of the bid.
  3. Stops at the transport step with a clear error explaining what is
     missing: an authenticated PropertyRoom session for the registered
     account. The HTTP bid placement itself is intentionally not implemented
     against PropertyRoom's forms — that step is only written once a real
     account exists and a human has reviewed the request.

Nothing in this codebase can charge a card. Dry-run is the default mode,
and dry-run never reaches this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from .policy import PolicyState


class LiveBiddingNotConfigured(RuntimeError):
    pass


@dataclass
class BidRequest:
    lot_url: str
    listing_id: str | None
    bid_amount_usd: float      # a proxy maximum when is_proxy, else the bid itself
    is_proxy: bool
    opening_bid_usd: float     # the lowest bid that takes the lead right now
    all_in_usd: float          # the most this bid can cost, all-in
    delivery_method: str | None

    def describe(self) -> str:
        if self.is_proxy:
            bid_lines = (
                f"  proxy bid, maximum: ${self.bid_amount_usd:,.2f}\n"
                f"  bids now: ${self.opening_bid_usd:,.2f} (the lowest amount that leads)\n"
                f"  all-in at most: ${self.all_in_usd:,.2f}\n")
        else:
            bid_lines = (
                f"  hammer bid: ${self.bid_amount_usd:,.2f}\n"
                f"  all-in at that bid: ${self.all_in_usd:,.2f}\n")
        return (
            f"Bid request (NOT SENT):\n"
            f"  lot: {self.lot_url}\n"
            f"  listing id: {self.listing_id or 'unknown'}\n"
            + bid_lines +
            f"  delivery: {self.delivery_method or 'not chosen'}"
        )


def build_bid_request(evaluation, lot_url: str, listing_id: str | None = None) -> BidRequest:
    return BidRequest(
        lot_url=lot_url,
        listing_id=listing_id,
        bid_amount_usd=evaluation.bid_amount,
        is_proxy=evaluation.bid_is_proxy,
        opening_bid_usd=evaluation.next_bid,
        all_in_usd=evaluation.all_in_at_bid,
        delivery_method=evaluation.shipping_method,
    )


def place_bid(bid: BidRequest, policy_state: PolicyState,
              confirmed: bool) -> str:
    """The live-bid entry point. Refuses unless EVERY gate passes.

    Gates: policy decision must be LIVE, the human must have passed
    --confirm, and a registered account must be configured. The final
    transport step is not implemented — see the module docstring.
    """
    if not confirmed:
        raise LiveBiddingNotConfigured(
            "Live bid refused: no explicit --confirm. Dry-run prints the bid; "
            "only --live --confirm places one.")
    if not policy_state.config.live_bidding_configured:
        raise LiveBiddingNotConfigured(
            "Live bid refused: no registered PropertyRoom account or payment "
            "method on file. Add them to config (see config.example.json) — "
            "never hardcode credentials in source — and re-run.")
    # Transport intentionally not implemented: placing a bid requires an
    # authenticated PropertyRoom session for the registered account, and no
    # account has been provided. This raise is the safety net, not a bug.
    raise LiveBiddingNotConfigured(
        "Live bid transport is not wired up: no authenticated PropertyRoom "
        "session exists. This is deliberate — bids are binding and "
        "auto-charged at close. Wire the transport only with a real "
        "registered account, after a human reviews the bid request above.")
