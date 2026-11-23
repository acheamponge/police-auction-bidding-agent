# Police Auction Bidding Agent — built on Muse Ember

**Built entirely with Muse on iPhone** — designed, coded, tested, and demoed from the Muse iPhone app.

An agent that bids on PropertyRoom police auctions without ever bidding
blind. It evaluates a lot, reads the buyer's premium, tax, and shipping
off the lot's own page, sets an all-in max it will not exceed, and bids
only when the math clears. Every evaluation runs on **Muse Ember** — and
the $10 hackathon budget is the product, not a limit on it: cheap Ember
evaluations are exactly what this agent does all day.

Built for the **Muse Ember Hackathon** (Cerebral Valley / Meta). Application
approved Mon Nov 23, 2026. Build window Nov 23 – Dec 2, 2026, 9:00 PM ET.
Solo, virtual, US-only. $5,000 in prizes.

## The idea

PropertyRoom sells seized and surplus goods as-is, and its fees are a trap
for anyone who bids on the hammer price alone:

- **The buyer's premium varies per lot** and the sources conflict.
  PropertyRoom's own help says most everyday lots carry none, with 10% /
  12% / 15% mainly on capital assets; third-party guides claim a 16.5%
  standard. The agent never hardcodes a rate — it reads the premium off
  each lot's own page, and if the page states none, the all-in total is
  *unknown* and the agent holds for a human instead of bidding blind.
- **The soft reserve.** On a reserve auction the seller has one business
  day after close to accept the high bid even if the reserve was never met —
  and acceptance commits you and auto-charges you. Reserve lots are held
  for a human by default.
- **The payment method on file is auto-charged at close.** So the agent's
  all-in math is the safety: hammer + premium + tax + shipping, capped by
  a max it will not cross.

This is the same spending-policy-agent lineage as the author's AI
Infrastructure SimCity (PayPal sandbox): a policy gate evaluates every
spend before anything is created, with a per-transaction cap, a session
budget, and an auto-approve threshold — here the cap is PropertyRoom's own
$4,999.99 card/PayPal ceiling, and the agent never wires money.

## How it works

```
lot URL + all-in max
  -> fetch the lot's own PropertyRoom page (public, no login)
  -> Muse Ember (muse-ember-1.0) reads the page and returns structured JSON:
     current price, premium, increment, reserve, condition, shipping
  -> deterministic math: all-in = hammer + premium + tax + shipping
     max hammer = (all-in max - shipping) / ((1 + premium) * (1 + tax))
  -> verdict: BID / NO_BID / HOLD_FOR_HUMAN
  -> spending policy gate (SimCity pattern): cap, session budget, mode
  -> dry-run (default): prints the exact bid it would place. No money moves.
```

Money fields come from the deterministic parse (numbers must be exact);
Ember does the repeated judgment work — the reserve reading, the condition
risk, the shipping read. One Ember call per lot evaluation.

**Two things the lot page does not tell you, handled honestly:**

1. **Tax is not printed on a lot page** — it is computed at checkout from
   the ship-to state. The agent treats tax as a *configured assumption*
   (`tax_rate`, e.g. 0.10 for the demo) and labels it as such in every
   verdict. With no rate configured, the agent holds every lot for a human.
2. **Pickup is not always free.** The demo lots proved the per-lot read
   matters: the F-150's pickup is $0.00, but the Caprice and the Prius
   both charge a $23.95 pickup fee on their own pages. A hardcoded $0
   would have underpriced both.

## The $10 budget math

Muse Ember on the Meta Model API (verified Nov 23, 2026):
`muse-ember-1.0`, `https://api.meta.ai/v1`, OpenAI-compatible.
$0.10 / 1M input tokens, $0.004 / 1M cached, $0.50 / 1M output
(prompts up to 200K tokens; above that the whole request triples).

One lot evaluation is a ~2,500-token prompt and a ~600-token JSON reply:

| | tokens | cost |
|---|---|---|
| input | ~2,500 | ~$0.00025 |
| output | ~600 | ~$0.00030 |
| **per evaluation** | | **~$0.00055** |

**$10 buys ~18,000 evaluations.** A full auction watch — say 200 lots a
day for the 10-day build window — is about $1.10. The `cost_ledger.jsonl`
logs every call with tokens, cost, and running total, and the ledger
*refuses* any call that would push spend over the $10 budget.

## Demo results (run Nov 23, 2026 — real lots, fetched live)

`python3 run_demo.py` evaluates three real lots, each twice: as listed, and
"as-if-live" (status forced live, fee data untouched) so the full BID path
is exercised. Demo tax assumption: 10%.

| Lot | Premium (read) | Next bid -> all-in | Verdict as listed | As-if-live |
|---|---|---|---|---|
| 2019 Ford F-150, ended $10,850, max $13,500 | 12% | $10,900 -> $13,428.80 | NO_BID (ended) | BID on math, then **policy DENIED** — $13,428.80 exceeds the $4,999.99 card cap (wire only) |
| 2014 Chevrolet Caprice, ended $4,450, max $6,000 | 15% | $4,500 -> $5,716.45 | NO_BID (ended) | **HOLD_FOR_HUMAN** — reserve not met (soft-reserve trap) |
| 2007 Toyota Prius, ended $800, max $1,500 | 15% | $850 -> $1,099.20 | NO_BID (ended) | **BID** — dry-run prints the exact bid: hammer $850.00, all-in $1,099.20, pickup |

No bids were placed. No money moved. Ledger after the demo: 6 calls,
$0.00 (fixture mode — see below).

## Run it

```bash
# Demo: three real lots, end to end, evaluation only
python3 run_demo.py

# One lot of your own (dry-run; prints the bid it would place)
python3 evaluate.py --lot https://www.propertyroom.com/l/.../12345678 \
  --max 1500 --tax-rate 0.10

# Tests (22, no network, no API key)
python3 -m unittest discover -s tests
```

Python 3.10+, standard library only — no `pip install` needed.

### Live Muse Ember

Set `MODEL_API_KEY` (copy `.env.example` to `.env` and fill it in) and
run `python3 run_demo.py --live-ember`. The key is the $10 in Meta Model
API credits from the hackathon — they arrive at build-window start or
within 24 hours of approval, from the Model API dashboard (dev.meta.ai).

**Without a key the agent runs in fixture mode** (the SimCity pattern): a
deterministic local evaluator returns the same JSON schema Ember would,
marked `simulated`, at $0.00. Nothing is sent to a model, and the ledger
says so in plain words. The demo above ran in fixture mode — the live
Ember path is the same code, with the key plugged in.

## Safety: the agent cannot spend money on its own

- **Dry-run is the default.** The agent prints the exact bid it would
  place and stops. A bid needs `--live --confirm` on the command line.
- **Policy gate, in order:** a NO_BID/HOLD verdict never spends; an
  all-in over the $4,999.99 transaction cap is denied outright (at $5,000+
  only a wire works, and the agent never wires money); committed exposure
  plus the new all-in may not exceed the session budget ($2,000 default).
- **Live bidding is not configured.** No PropertyRoom account or payment
  method has been provided, and `live_bid.py` refuses every live attempt
  until one is — and the bid-placement transport itself is intentionally
  not wired up (it needs an authenticated session for a real registered
  account). Nothing in this codebase can charge a card.

## Files

| Path | What it is |
|---|---|
| `run_demo.py` | Demo: 3 real lots, as-listed + as-if-live, ledger summary |
| `evaluate.py` | CLI: evaluate one lot (`--lot URL --max N [--tax-rate R]`) |
| `src/agent.py` | Orchestration: fetch -> Ember -> math -> policy gate |
| `src/ember_client.py` | Muse Ember client (OpenAI-compatible), pricing, fixture mode |
| `src/cost_ledger.py` | JSONL ledger of every model call + $10 budget wall |
| `src/lot_fetcher.py` | Fetch + parse a lot page (premium read, never hardcoded) |
| `src/evaluator.py` | All-in math, max hammer, verdicts, risk flags |
| `src/policy.py` | Spending policy gate (SimCity pattern, adapted) |
| `src/live_bid.py` | Gated live-bid path — refuses without config + confirm |
| `src/demo_lots.py` | The three demo lots (verified live Nov 23, 2026) |
| `tests/` + `tests/fixtures/` | 22 unit tests; fixtures modeled on real lot pages |
| `config.example.json` / `.env.example` | Config + key templates (copy, never commit secrets) |
| `cost_ledger.jsonl` | Ledger from the demo run |

## What is still needed from Kwame

1. **The $10 Model API key** (`MODEL_API_KEY`) once the hackathon
   credits land — to flip the demo from fixture mode to live Muse Ember.
   One line in `.env`, then `run_demo.py --live-ember`.
2. **A live lot to watch** — a lot URL + an all-in max, whenever he
   wants a real bid evaluated. (`evaluate.py --lot URL --max N`.)
3. **A registered PropertyRoom account + payment method** — only if he
   ever wants a live bid placed. Not provided, not invented, not needed
   for the demo or the submission.

## Submission

Build window ends Wed Dec 2, 2026, 9:00 PM ET. This repo is the build;
nothing has been pushed to GitHub and nothing has been submitted to the
hackathon — that step is Kwame's call.
