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
  -> deterministic parse: price, premium, increment, reserve, status, shipping
  -> Muse Ember (muse-ember-1.0) reads the same page on its own and returns
     structured JSON: the same fields, a value estimate, and a stance on
     the listing
  -> deterministic math: all-in = hammer + premium + tax + shipping
     max hammer = the largest whole-cent bid whose all-in stays inside the max
  -> verdict: BID / NO_BID / HOLD_FOR_HUMAN
  -> spending policy gate (SimCity pattern): cap, session budget, mode
  -> dry-run (default): prints the exact bid it would place. No money moves.
```

The money numbers always come from the deterministic parse. Ember is the
second pair of eyes, and its read can only make the agent more careful:

- **Two reads must agree.** Ember extracts the price, premium, increment,
  reserve flag, status and shipping prices independently. If any of them
  differs from the parse, one of the two misread the page, and the lot is
  held for a human.
- **Ember can veto.** Its stance covers what the numbers cannot see: does
  the listing say it starts and runs, is there a title and a key, is damage
  hidden behind "see images". Anything other than "bid" turns a clearing
  bid into HOLD_FOR_HUMAN, with Ember's reasoning in the verdict.
- **Ember prices the item.** If the all-in total is above the top of its
  value estimate, the lot is held.

A "bid" stance never forces a bid. Ember cannot move a NO_BID or a hold
toward spending, so a lot page that tries to talk the model into something
gains nothing.

One Ember call per lot evaluation. The model is sent the lot itself (title,
fee table, description, shipping table), not the site navigation around it.

**Three things the lot page does not tell you, handled honestly:**

1. **Tax is not printed on a lot page.** It is computed at checkout from
   the ship-to state. The agent treats tax as a *configured assumption*
   (`tax_rate`, e.g. 0.10 for the demo) and labels it as such in every
   verdict. With no rate configured, the agent holds every lot for a human.
2. **Pickup is not free.** Customer Pick-up is often the only way to get a
   vehicle, and the page prices it at $0.00 to $23.95, but someone still
   has to travel to the lot or pay to haul it. The agent never chooses
   pickup unless you tell it what collecting costs you (`pickup_cost_usd`,
   0 if you are local). That amount is added to the page's pickup fee.
   A pickup-only lot with no pickup cost configured is held for a human.
3. **Whether the auction is still running.** Every page, live or ended,
   carries the words "Ending" and "Ended" in its countdown. The agent only
   treats a lot as live when the page prints an "Ends <date>" line. If it
   cannot tell, it holds.

## The $10 budget math

Muse Ember on the Meta Model API (verified Nov 23, 2026):
`muse-ember-1.0`, `https://api.meta.ai/v1`, OpenAI-compatible.
$0.10 / 1M input tokens, $0.004 / 1M cached, $0.50 / 1M output
(prompts up to 200K tokens; above that the whole request triples).

One lot evaluation, measured on the live runs of Nov 23, 2026 (seven
evaluations of four real lots):

| | tokens | cost |
|---|---|---|
| input (instructions + the lot's page text) | 4,062 to 5,716 | $0.00041 to $0.00057 |
| output (Ember's thinking + its JSON reply) | 573 to 882 | $0.00029 to $0.00044 |
| **per evaluation** | | **$0.0007 to $0.00095** |

**$10 buys roughly 12,000 evaluations.** A full auction watch — say 200
lots a day for the 10-day build window — is about $1.65.

Muse Ember is a reasoning model: its thinking is billed as output and
counts against the output cap. The client asks for `reasoning_effort: low`
and caps output at 4,000 tokens, which leaves room for the thinking and the
answer and keeps the worst case for one call under $0.003. (At a 1,200-token
cap the model spent the whole allowance thinking and returned no answer.)

The budget is a wall in front of the call, not a note after it. Before each
request the client prices the worst case (the whole prompt plus the full
output cap) and refuses to send anything that could push spend over $10.
After the call, what it really cost is always written to
`cost_ledger.jsonl` with tokens and the running total.

Ledger after the runs below: 27 calls, 21 of them live, **$0.0154 spent,
$9.98 left**. That total includes three early calls that came back empty
because of the 1,200-token cap, and five tiny calls that checked which
settings the API accepts.

## Demo results

Run Nov 23, 2026 on live Muse Ember, against real lot pages fetched at run
time. Assumptions: 10% tax, $0 pickup cost (a local buyer).

`python3 run_demo.py --live-ember` evaluates three real lots, each twice:
as listed, and "as-if-live" (the "Ended" line rewritten to "Ends", fee data
untouched) so the paths past "auction ended" are exercised.

| Lot | Premium (read) | Next bid -> all-in | As listed | As-if-live | What Ember saw |
|---|---|---|---|---|---|
| 2019 Ford F-150, ended $10,850, max $13,500 | 12% | $10,900 -> $13,428.80 | NO_BID (ended) | **HOLD_FOR_HUMAN** — math clears, Ember objects | Does not start or run, odometer reads 999,999, check engine light. Worth $8,000 to $12,000 |
| 2014 Chevrolet Caprice, ended $4,450, max $6,000 | 15% | $4,500 -> $5,716.45 | NO_BID (ended) | **HOLD_FOR_HUMAN** — reserve not met (soft-reserve trap) | Starts and runs, key, regular title: stance "bid". Worth $4,500 to $7,500 |
| 2007 Toyota Prius, ended $800, max $1,500 | 15% | $850 -> $1,099.20 | NO_BID (ended) | **HOLD_FOR_HUMAN** — math clears, Ember objects | Salvage certificate only; start/run, key and mileage all unknown. Worth $500 to $1,500 |

One lot that was really live at the time, through
`python3 evaluate.py --lot ... --max 5000 --tax-rate 0.10 --pickup-cost 0`:

| Lot | Premium (read) | Next bid -> all-in | Verdict | Policy gate | What Ember saw |
|---|---|---|---|---|---|
| 2019 Chevrolet Cruze LS, live at $3,750 | 12% | $3,800 -> $4,681.60 | **BID** — math clears, both reads agree, no objection | **DENIED** — $4,681.60 is over the $2,000 session budget | Starts and runs, key, regular title, 123,847 miles; must be towed off the lot. Worth $4,000 to $6,500 |

In all seven evaluations Ember's own read of the page matched the parser on
every money field. Its value estimates move a little from run to run.

The Prius is the case for the second read. On the numbers alone it is a
clean bid at $1,099.20 all-in, and with no model in the loop
(`python3 run_demo.py`, fixture mode) the agent prints exactly that bid.
The listing's own text says salvage certificate, key unknown, running
condition unknown.

No bids were placed. No money moved.

## Run it

```bash
# Demo: three real lots, end to end, evaluation only (fixture mode, $0)
python3 run_demo.py

# Same demo on the real model
python3 run_demo.py --live-ember

# One lot of your own (dry-run; prints the bid it would place)
python3 evaluate.py --lot https://www.propertyroom.com/l/.../12345678 \
  --max 1500 --tax-rate 0.10 --pickup-cost 0

# Tests (68, no network, no API key)
python3 -m unittest discover -s tests
```

Python 3.10+, standard library only — no `pip install` needed.

### Settings

Copy `config.example.json` to `config.json` to set your tax rate, pickup
cost, shipping preference, default all-in max and policy limits once.
`evaluate.py` reads it, and any flag on the command line wins over it.
`config.json` is git-ignored.

### Live Muse Ember

Copy `.env.example` to `.env` and put your key in `MODEL_API_KEY`. Both
scripts read `.env` on their own; a variable already set in your shell wins
over the file. `.env` is git-ignored.

`evaluate.py` uses the real model whenever a key is present. `run_demo.py`
only does when you pass `--live-ember`, so the plain demo never spends.

**Without a key the agent runs in fixture mode** (the SimCity pattern): a
deterministic local evaluator returns the same JSON schema Ember would,
marked `simulated`, at $0.00. Nothing is sent to a model, and the ledger
says so in plain words. In fixture mode the "second read" is the parser
itself, so it always agrees and gives no value estimate.

## Safety: the agent cannot spend money on its own

- **Dry-run is the default.** The agent prints the exact bid it would
  place and stops. A bid needs `--live --confirm` on the command line.
- **When unsure, it holds.** Unknown auction status, no premium line, no
  tax rate, no usable delivery method, a soft reserve, two reads that
  disagree, or an objection from Ember all end in HOLD_FOR_HUMAN.
- **Policy gate, in order:** a NO_BID/HOLD verdict never spends; an
  all-in over the $4,999.99 transaction cap is denied outright (at $5,000+
  only a wire works, and the agent never wires money); committed exposure
  plus the new all-in may not exceed the session budget ($2,000 default).
- **Live bidding is not configured.** `live_bid.py` refuses every live
  attempt until an account is configured, and the bid-placement transport
  itself is intentionally not wired up (it needs an authenticated session
  for a real registered account). Nothing in this codebase can charge a card.

## Files

| Path | What it is |
|---|---|
| `run_demo.py` | Demo: 3 real lots, as-listed + as-if-live, ledger summary |
| `evaluate.py` | CLI: evaluate one lot (`--lot URL --max N [--tax-rate R] [--pickup-cost C]`) |
| `src/agent.py` | Orchestration: fetch -> Ember -> math -> policy gate |
| `src/ember_client.py` | Muse Ember client (OpenAI-compatible), pricing, pre-call budget check, fixture mode |
| `src/cost_ledger.py` | JSONL ledger of every model call + $10 budget wall |
| `src/lot_fetcher.py` | Fetch + parse a lot page (premium read, never hardcoded) |
| `src/evaluator.py` | All-in math, max hammer, the two-read check, verdicts, risk flags |
| `src/policy.py` | Spending policy gate (SimCity pattern, adapted) |
| `src/live_bid.py` | Gated live-bid path — refuses without config + confirm |
| `src/config.py` | Loads `.env` and `config.json` |
| `src/demo_lots.py` | The three demo lots |
| `tests/` + `tests/fixtures/` | 68 unit tests; fixtures mirror the layout of real lot pages |
| `config.example.json` / `.env.example` | Config + key templates (copy, never commit secrets) |
| `cost_ledger.jsonl` | Ledger from the demo runs |

## Not built yet

- **Placing a bid.** The agent stops at the exact bid request. The
  transport needs a registered PropertyRoom account with a payment method
  on file, and a person reviewing the first requests.
- **Watching a lot.** Each run evaluates a lot once, at its current price.
  There is no polling as the price moves and no proxy-bid or late-bid
  strategy.
- **Everyday lots.** Most small lots print no buyer's premium line at all.
  The agent treats a missing line as unknown and holds, so today it only
  reaches BID on lots that state their premium, which are mostly vehicles
  and other capital assets.
