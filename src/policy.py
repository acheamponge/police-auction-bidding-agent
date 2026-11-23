"""The spending policy: the leash on the bidding agent.

Mirrors the SimCity policy gate (src/lib/policy.ts) — every spend an agent
wants to make is evaluated here BEFORE anything is placed, and the decision
is recorded in the run log.

Rules, evaluated in order:
  1. A NO_BID / HOLD_FOR_HUMAN verdict never spends.
  2. Per-transaction cap: an all-in total above TX_CAP_USD is denied
     outright. Default $4,999.99 — PropertyRoom's card/PayPal ceiling; at
     $5,000+ only a wire transfer works, and the agent never wires money
     on its own. No human can override this from the agent side.
  3. Session budget: committed exposure across this run (all-in totals of
     lots the agent has already committed to) plus the new all-in may not
     exceed SESSION_BUDGET_USD.
  4. Mode: DRY_RUN is the default — the agent only prints the exact bid it
     would place, for a human to approve. LIVE requires live bidding to be
     configured (a registered PropertyRoom account + payment method on file)
     AND an explicit --live --confirm on the command line. Until an account
     is configured, every live attempt is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CARD_PAYMENT_CAP_USD = 4_999.99


@dataclass
class PolicyConfig:
    tx_cap_usd: float = CARD_PAYMENT_CAP_USD
    session_budget_usd: float = 2_000.00
    dry_run: bool = True
    allow_reserve_lots: bool = False
    live_bidding_configured: bool = False  # account + payment method on file


@dataclass
class PolicyDecision:
    decision: str  # DENIED | DRY_RUN | HUMAN_APPROVAL | LIVE
    note: str
    committed_exposure_usd: float = 0.0


@dataclass
class PolicyState:
    config: PolicyConfig = field(default_factory=PolicyConfig)
    committed: list = field(default_factory=list)  # (lot_url, all_in_usd)

    @property
    def exposure_usd(self) -> float:
        return round(sum(amount for _, amount in self.committed), 2)

    def evaluate(self, verdict: str, all_in_usd: float | None,
                 lot_url: str) -> PolicyDecision:
        cfg = self.config
        base = {"committed_exposure_usd": self.exposure_usd}

        if verdict in ("NO_BID", "HOLD_FOR_HUMAN"):
            return PolicyDecision(
                decision="DENIED", note=(
                    f"Policy: verdict is {verdict} — a no-bid/hold never "
                    "spends. Capital stays undeployed."), **base)
        if all_in_usd is None:
            return PolicyDecision(
                decision="DENIED", note=(
                    "Policy: all-in total could not be computed — denied."),
                **base)
        if all_in_usd > cfg.tx_cap_usd:
            return PolicyDecision(
                decision="DENIED", note=(
                    f"Policy: all-in ${all_in_usd:,.2f} exceeds the "
                    f"per-transaction cap of ${cfg.tx_cap_usd:,.2f} "
                    "(PropertyRoom card/PayPal ceiling). Denied outright; "
                    "at $5,000+ only a wire transfer works and the agent "
                    "never wires money on its own."), **base)
        if self.exposure_usd + all_in_usd > cfg.session_budget_usd:
            return PolicyDecision(
                decision="DENIED", note=(
                    f"Policy: committed exposure ${self.exposure_usd:,.2f} "
                    f"plus ${all_in_usd:,.2f} would exceed the session "
                    f"budget of ${cfg.session_budget_usd:,.2f}. Denied; "
                    "raise the session budget or drop a lot first."), **base)
        if cfg.dry_run:
            return PolicyDecision(
                decision="DRY_RUN", note=(
                    f"Policy: dry-run mode — the agent prints the exact bid "
                    f"it would place (${all_in_usd:,.2f} all-in) and stops. "
                    "No money moves; a human approves first."), **base)
        if not cfg.live_bidding_configured:
            return PolicyDecision(
                decision="HUMAN_APPROVAL", note=(
                    "Policy: live bidding is not configured — no registered "
                    "PropertyRoom account or payment method on file. Provide "
                    "them in config and re-run with --live --confirm."), **base)
        return PolicyDecision(
            decision="LIVE", note=(
                f"Policy: live mode, inside the ${cfg.tx_cap_usd:,.2f} cap "
                f"and the ${cfg.session_budget_usd:,.2f} session budget "
                f"(${self.exposure_usd:,.2f} committed). Proceeds only with "
                "an explicit --live --confirm."), **base)

    def commit(self, lot_url: str, all_in_usd: float) -> None:
        self.committed.append((lot_url, round(all_in_usd, 2)))
