# Deploying the demo UI to Vercel

The project's own deployment is at <https://police-auction-bidding-agent.vercel.app>.

The demo UI runs on Vercel as one Python function plus a folder of static
files. There is nothing to build and nothing to install: the project uses
only Python's standard library.

| Piece | What it is |
|---|---|
| `api/index.py` | The function. Every page and every `/api/...` call goes to it |
| `public/` | The stylesheet, the script and the recording of the site. Vercel serves this folder itself, and it is the only folder it serves |
| `vercel.json` | Sends every path to the function, limits a request to 60 seconds, and makes `public/` the only static folder |
| `.python-version` | Python 3.12 |
| `.vercelignore` | Keeps `.env`, `config.json`, `watchlist.json`, the local ledger and the tests out of a deployment |

## 1. Create the project

1. On [vercel.com/new](https://vercel.com/new) import the GitHub repository.
2. Framework preset: **Other**. Leave the build command, output directory
   and install command alone; `vercel.json` sets what is needed.
3. Under **Environment Variables** add, for Production and Preview:

   | Variable | Value |
   |---|---|
   | `MODEL_API_KEY` | Your Meta Model API key. With it the site calls live Muse Ember. Leave it out and the site runs in fixture mode at $0 |
   | `MODEL_BUDGET_USD` | Optional. The ledger's budget, per server instance. Default 10 |
   | `MODEL_API_BASE` | Optional. Only if you use a different endpoint |

4. Deploy. Every later push to `main` redeploys.

## 2. Check it

Open the site and go through Overview, Demo and Lots. On the Demo page press
**Run the demo**: it finds an auction that is live on PropertyRoom, then runs
the four fixed lots, one request per lot. The pill in the top right says
whether the live model is in use.

If every lot shows an error about PropertyRoom, their site is refusing
requests from Vercel's servers. The hosted demo cannot read listings in that
case; running the project locally still works.

## What is different when it is hosted

**Nothing is kept on the server.** A request can land on any instance and
the disk is read-only, so each visitor's lots, verdicts and assumptions live
in their own browser and travel with each request. Visitors never see each
other's lots. Clearing site data clears a visitor's list.

**Anyone with the link spends your key.** Each new lot, and each lot whose
price or fees changed, is one Muse Ember call, about $0.0008. A full demo
run is about a cent the first time and free after that for the same visitor
until a page changes.

**The budget wall is per instance.** Locally the ledger is a file and the
$10 wall covers every call ever made. On Vercel the ledger sits in an
instance's temporary space and resets when the instance does. The Ledger
page says so. The firm limit on a hosted site is the credit on the key
itself, so use a key whose whole balance you are prepared to see spent.

To keep spending in hand you can leave `MODEL_API_KEY` out (fixture mode),
turn on Vercel's Deployment Protection so only people you invite can open
the site, or remove the variable when the demo is over.

**It is still dry-run only.** There is no live-bidding mode in the UI, and
the API key never reaches a page.

## Try the hosted mode at home

```bash
python3 ui.py --hosted          # then open http://127.0.0.1:8765
```

This runs the same handler Vercel runs: no watchlist file, no state between
requests, lots kept in the browser. `python3 ui.py` without the flag is the
ordinary local UI.
