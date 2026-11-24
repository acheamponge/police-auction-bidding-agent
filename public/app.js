// The script for every page of the demo UI. No libraries. Each page is the same
// shell with a different fragment; document.body.dataset.page says which one.
// Everything that came from a lot page or from the model is put on screen as
// text, never as HTML.
"use strict";

const PAGE = document.body.dataset.page;
// Hosted (on Vercel) the server keeps nothing between requests: this
// visitor's lots and verdicts live in this browser and go along with every
// request as a session. Locally the server holds them.
const HOSTED = document.body.dataset.hosted === "true";
const SESSION_KEY = "police-auction-agent-session-v1";

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY)) || null;
  } catch (error) {
    return null;      // storage unavailable or damaged: start fresh
  }
}

function saveSession(session) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch (error) {
    // Private windows and full storage: the page still works for this visit.
  }
}
const $ = (id) => document.getElementById(id);
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const money = (n) => (n === null || n === undefined ? "not read" : usd.format(n));
const small = (n) => "$" + n.toFixed(4);
const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");

let busy = false;
let autoTimer = null;
let settingsShown = false;

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function setStatus(text, isError) {
  const status = $("status");
  status.textContent = text || "";
  status.className = isError ? "status error" : "status";
}

function setBusy(on) {
  busy = on;
  for (const control of document.querySelectorAll("main button, main input, main select")) control.disabled = on;
}

// ---- toasts (sonner): one region, toast() from anywhere ----------------------
// Short, in the interface's voice. Kinds: default, good, warn, destructive.

function toast(message, kind) {
  const region = $("toaster");
  if (!region) return;
  while (region.children.length >= 3) region.firstElementChild.remove();
  const node = el("div", { class: "toast" + (kind && kind !== "default" ? " toast-" + kind : "") },
    el("span", { class: "toast-message" }, message),
    el("button", { class: "toast-close", type: "button", "aria-label": "Dismiss notification",
      onclick: (event) => { event.currentTarget.parentElement.remove(); } }, "×"));
  region.append(node);
  setTimeout(() => { node.classList.add("leaving"); setTimeout(() => node.remove(), 220); }, 4500);
}

const firstSentence = (text) => (text || "").split(".")[0];

function toastVerdict(state, url) {
  const lot = (state.lots || []).find((item) => item.url === url);
  if (!lot || !lot.evaluation) return;
  const o = outcome(lot);
  const e = lot.evaluation;
  if (o.key === "bid") {
    toast("Bid plan ready: " + (e.bid_is_proxy ? "proxy maximum " : "bid ") + money(e.bid_amount) +
      ", all-in at most " + money(e.all_in_at_bid) + ".", "good");
  } else if (o.key === "hold" && e.verdict === "BID") {
    toast("Bid plan blocked by the spending policy. " + (lot.policy_note || "Check the Policy page."), "destructive");
  } else if (o.key === "hold") {
    toast("Held for you: " + firstSentence(e.reason) + ".", "warn");
  } else if (o.key === "nobid") {
    toast("No bid: " + firstSentence(e.reason) + ".", "default");
  }
}

function toastCounts(state) {
  const counts = { bid: 0, hold: 0, nobid: 0, none: 0 };
  for (const lot of state.lots) counts[outcome(lot).key] += 1;
  toast("Checked " + plural(state.lots.length, "lot") + ": " + counts.bid + " bid plan" +
    (counts.bid === 1 ? "" : "s") + ", " + counts.hold + " held, " + counts.nobid + " no bid.", "default");
}

// Send a change to the server (or just read the state) and redraw from the reply.
async function send(path, body, working) {
  if (busy) return false;
  setBusy(true);
  setStatus(working);
  if (HOSTED) {
    // Even reading the state is a POST here: the state is in the session.
    body = { ...(body || {}), session: loadSession() };
  }
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.error || "Something went wrong.", true);
      toast(data.error || "Something went wrong. Try again in a moment.", "destructive");
      return false;
    }
    if (data.session) saveSession(data.session);
    setBusy(false);
    render(data);
    setStatus("");
    return data;
  } catch (error) {
    setStatus(HOSTED ? "Could not reach the server. Try again in a moment."
      : "Could not reach the app. Is `python3 ui.py` still running?", true);
    toast(HOSTED ? "Could not reach the server. Try again in a moment."
      : "Could not reach the app. Is `python3 ui.py` still running?", "destructive");
    return false;
  } finally {
    setBusy(false);
  }
}

const numberOrNull = (input) => (input.value.trim() === "" ? null : Number(input.value));

// ---- shared pieces ---------------------------------------------------------

function kpi(label, value, sub, tone) {
  return el("div", { class: "card kpi" + (tone ? " " + tone : "") },
    el("div", { class: "eyebrow" }, label),
    el("div", { class: "kpi-value" }, value),
    sub && el("div", { class: "kpi-sub" }, sub));
}

// What a result comes to, once the policy gate has had its say.
function outcome(view) {
  if (!view || !view.evaluation) return { key: "none", label: "NOT CHECKED", pill: "", card: "card-slate", stamp: "stamp-none" };
  const verdict = view.evaluation.verdict;
  if (verdict === "BID" && view.policy_decision === "DRY_RUN") return { key: "bid", label: "BID", pill: "pill-go", card: "card-good", stamp: "stamp-bid" };
  if (verdict === "BID") return { key: "hold", label: "BLOCKED BY POLICY", pill: "pill-cond", card: "card-amber", stamp: "stamp-hold" };
  if (verdict === "NO_BID") return { key: "nobid", label: "NO BID", pill: "pill-prop", card: "card-slate", stamp: "stamp-nobid" };
  return { key: "hold", label: "HOLD FOR YOU", pill: "pill-cond", card: "card-amber", stamp: "stamp-hold" };
}

const verdictPill = (view) => {
  const o = outcome(view);
  return el("span", { class: "stamp " + o.stamp, "data-state": o.key }, o.label);
};

// A verdict is two stages, and the stamp only ever shows the final outcome.
// Stage 1 is the evaluator's call (BID / NO_BID / HOLD_FOR_HUMAN) with its
// reason; stage 2 is the spending policy's decision on that call. The stamp
// is decided by the evaluator — unless the evaluator said BID and the policy
// gate denied it, in which case the policy decided.
function decidingStage(view) {
  const e = view.evaluation;
  return (e.verdict === "BID" && view.policy_decision !== "DRY_RUN") ? "the spending policy" : "the evaluator";
}

const stage1Word = (verdict) => verdict === "BID" ? "Bid" : verdict === "NO_BID" ? "No bid" : "Hold for a human";
const stage2Word = (decision) => decision === "DRY_RUN" ? "Allowed — dry run"
  : decision === "DENIED" ? "Denied"
  : decision === "HUMAN_APPROVAL" ? "Needs a human" : "Allowed — live";

function stagesBlock(view) {
  const e = view.evaluation;
  // The headroom figure, in words: the highest hammer bid inside the max
  // against the next bid. A figure like "$5,860 of room" means this gap.
  const headroom = (e.verdict === "BID" && e.max_hammer !== null && e.next_bid !== null)
    ? "Headroom: the highest hammer bid inside your max is " + money(e.max_hammer) +
      "; the next bid is " + money(e.next_bid) +
      (e.max_hammer > e.next_bid ? " — " + money(e.max_hammer - e.next_bid) + " of room." : ".")
    : null;
  return el("div", { class: "stages" },
    el("div", { class: "stage" },
      el("div", { class: "eyebrow" }, "Stage 1 · The evaluator"),
      el("p", {}, el("strong", {}, stage1Word(e.verdict)), " — ", e.reason),
      headroom && el("p", { class: "muted" }, headroom)),
    el("div", { class: "stage" },
      el("div", { class: "eyebrow" }, "Stage 2 · The spending policy"),
      el("p", {}, el("strong", {}, stage2Word(view.policy_decision)), " — ", view.policy_note)));
}

function verdictFinal(view) {
  return el("div", { class: "final" },
    verdictPill(view),
    el("div", { class: "final-note" }, "Final outcome · decided by " + decidingStage(view)));
}

function plan(view) {
  const e = view.evaluation;
  if (e.verdict !== "BID" || e.bid_amount === null) return null;
  const allowed = view.policy_decision === "DRY_RUN";
  return el("div", { class: allowed ? "plan" : "plan blocked" },
    el("div", { class: "eyebrow" }, allowed ? "Bid plan · not sent" : "Stopped by the spending policy"),
    el("div", { class: "plan-figure" }, (e.bid_is_proxy ? "Proxy maximum " : "Bid ") + money(e.bid_amount)),
    el("p", { class: "plan-detail" }, "All-in at most " + money(e.all_in_at_bid) + "."),
    e.bid_note && el("p", { class: "plan-detail" }, e.bid_note));
}

function fact(label, value, sub) {
  return el("div", {}, el("dt", {}, label), el("dd", {}, value, sub && el("span", { class: "sub" }, sub)));
}

function facts(e) {
  const premium = e.premium_stated ? e.premium_pct.toFixed(2) + "%" : e.premium_absent ? "None" : "Unreadable";
  const premiumNote = e.premium_stated ? "read off the lot page"
    : e.premium_absent ? "none in the auction table" : "mentioned, but no rate found";
  return el("dl", { class: "facts" },
    fact("Current price", money(e.current_price)),
    fact("Next bid to lead", money(e.next_bid), e.bid_increment !== null && "increment " + money(e.bid_increment)),
    fact("All-in at next bid", e.all_in_at_next_bid === null ? "n/a" : money(e.all_in_at_next_bid),
      e.all_in_at_next_bid === null && "cannot be worked out yet"),
    fact("Highest bid in your max", e.max_hammer === null ? "n/a" : money(e.max_hammer)),
    fact("Buyer's premium", premium, premiumNote),
    fact("Tax", e.tax_rate === null ? "Not set" : (e.tax_rate * 100).toFixed(2) + "%", "an assumption"),
    fact("Delivery", e.shipping_method ? money(e.shipping_cost) : "None usable",
      e.shipping_method && e.shipping_method +
        (e.pickup_cost_usd !== null ? ", incl. " + money(e.pickup_cost_usd) + " pickup cost" : "")));
}

function ember(e) {
  if (!e.ember_stance && e.value_estimate_low === null) return null;
  const hasValue = e.value_estimate_low !== null && e.value_estimate_high !== null;
  let gap = null;
  if (hasValue && e.all_in_at_next_bid !== null) {
    const side = (value) => money(Math.abs(value - e.all_in_at_next_bid)) +
      (e.all_in_at_next_bid <= value ? " below" : " above");
    gap = "The next bid's all-in is " + side(e.value_estimate_low) + " the low estimate and " +
      side(e.value_estimate_high) + " the high estimate.";
  }
  const stancePill = e.ember_stance === "bid" ? "pill-plan" : e.ember_stance ? "pill-cond" : "";
  // What the photos showed, when Muse Ember read them. The photo risks are
  // what can veto a bid: they are already in the risk flags, but they are
  // quoted here next to the verdict so the reason is visible.
  const photo = e.photo_notes;
  const photoBlock = photo ? el("div", { class: "photo-notes" },
      el("div", { class: "eyebrow" }, "What the photos show"),
      photo.summary && el("p", {}, photo.summary),
      photo.matches_listing === false && el("p", { class: "photo-mismatch" }, "The photos do not match the listing."),
      (photo.observations || []).length ? el("ul", { class: "photo-list" },
        photo.observations.map((note) => el("li", {}, note))) : null,
      (photo.risks || []).length ? el("ul", { class: "photo-list photo-risks" },
        photo.risks.map((note) => el("li", {}, note))) : null)
    : e.simulated && e.photo_urls && e.photo_urls.length ? el("p", { class: "muted" },
      "The lot's photos were not read — fixture mode calls no model, so there is no photo read to show.")
    : null;
  return el("div", { class: "ember" },
    el("div", { class: "ember-head" },
      el("strong", {}, e.simulated ? "LOCAL STAND-IN" : "MUSE EMBER"),
      e.ember_stance && el("span", { class: "pill " + stancePill }, "stance: " + e.ember_stance),
      e.simulated && el("span", { class: "pill" }, "no model called")),
    e.ember_reasoning && el("p", {}, e.ember_reasoning),
    hasValue && el("p", {},
      el("span", { class: "value" }, "Worth " + money(e.value_estimate_low) + " to " + money(e.value_estimate_high) + ". "),
      el("span", { class: "muted" }, e.value_basis || "")),
    gap && el("p", { class: "muted" }, gap),
    photoBlock);
}

function flags(e) {
  if (!e.risk_flags || !e.risk_flags.length) return null;
  return el("details", {},
    el("summary", {}, plural(e.risk_flags.length, "risk flag")),
    el("ul", { class: "flags" }, e.risk_flags.map((flag) => el("li", {}, flag))));
}

const resultBody = (view) => {
  const e = view.evaluation;
  return [stagesBlock(view), plan(view), facts(e), ember(e), flags(e)];
};

const lotLink = (url, text) => el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text);
const statusWord = (e) => (e.status === "live" ? "Live" : e.status === "ended" ? "Ended" : "Status unknown");

// ---- the frame every page shares -------------------------------------------

function renderChrome(state) {
  const live = state.mode === "live";
  for (const pill of document.querySelectorAll(".mode-pill")) {
    // Short on purpose: the full "live · muse-ember-1.0" label made the
    // header overflow at desktop width and clipped the last nav link. The
    // detail stays in the title (hover) and in the Mode tile on Overview.
    pill.textContent = live ? "Live" : "Fixture";
    pill.className = "pill mode-pill " + (live ? "pill-go pill-live" : "pill-cond");
    pill.title = live ? "Live: " + state.model + ". Each new or changed lot costs one call to the model."
      : "Fixture mode: no model is called. A local stand-in reads the page: no second opinion, no value estimate.";
  }
}

function setUpNav() {
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === PAGE) link.setAttribute("aria-current", "page");
  }
  const button = $("menu-button");
  const menu = $("mobile-menu");
  const toggle = (open) => {
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  };
  button.addEventListener("click", () => toggle(menu.hidden));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") toggle(false); });
}

// ---- Overview ---------------------------------------------------------------

function renderOverview(state) {
  const ledger = state.ledger;
  const counts = { bid: 0, hold: 0, nobid: 0, none: 0 };
  for (const lot of state.lots) counts[outcome(lot).key] += 1;
  const ready = state.lots.find((lot) => outcome(lot).key === "bid");

  const lead = $("overview-lead");
  if (ready) {
    const e = ready.evaluation;
    lead.replaceChildren(
      el("div", { class: "eyebrow eyebrow-dot" }, "Ready for your review"),
      el("div", { class: "lead-row" },
        el("div", {},
          el("div", { class: "lead-title" }, e.title || ready.url),
          el("p", { class: "lead-note" }, e.bid_note || e.reason)),
        el("div", { class: "lead-stats" },
          el("div", {}, el("div", { class: "eyebrow" }, e.bid_is_proxy ? "Proxy maximum" : "Bid"), el("span", { class: "num" }, money(e.bid_amount))),
          el("div", {}, el("div", { class: "eyebrow" }, "All-in at most"), el("span", { class: "num" }, money(e.all_in_at_bid))),
          el("div", {}, el("div", { class: "eyebrow" }, "Next bid"), el("span", { class: "num" }, money(e.next_bid))))));
  } else {
    const checked = state.lots.length - counts.none;
    let eyebrow = "Start here";
    let title = "No lots on your list yet";
    let note = "Run the demo to watch six real lots go through the agent, or paste a lot link on the Lots page.";
    if (state.lots.length && !checked) {
      eyebrow = "Waiting on you";
      title = plural(counts.none, "lot") + " on your list, not checked yet";
      note = "Lots are not re-read when the app starts, so nothing is spent until you ask. Open Lots and press Check all now.";
    } else if (state.lots.length) {
      eyebrow = "Nothing clears right now";
      title = plural(counts.hold, "lot") + " held for you, " + counts.nobid + " not worth a bid";
      note = "Open Lots to see why each one was held. The agent holds whenever it cannot be sure.";
    }
    lead.replaceChildren(
      el("div", { class: "eyebrow eyebrow-dot" }, eyebrow),
      el("div", { class: "lead-title" }, title),
      el("p", { class: "lead-note" }, note));
  }

  $("overview-kpis").replaceChildren(
    kpi("Model budget spent", small(ledger.total_spent_usd),
      "of " + usd.format(ledger.budget_usd) + ", " + small(ledger.remaining_usd) + " left"),
    kpi("Model calls", String(ledger.live_calls), plural(ledger.simulated_calls, "simulated run")),
    kpi("Your lots", String(state.lots.length),
      counts.bid + " bid plan" + (counts.bid === 1 ? "" : "s") + ", " + counts.hold + " held, " + counts.nobid + " no bid" +
        (counts.none ? ", " + counts.none + " not checked" : ""),
      counts.bid ? "good" : undefined),
    kpi("Mode", state.mode === "live" ? "Live" : "Fixture",
      state.mode === "live" ? state.model : "no model is called", state.mode === "live" ? "good" : "warn"));
}

// ---- Demo -------------------------------------------------------------------
// One lot at a time, three views of the same run: the listing on the site,
// the command line, and this UI.

let demoSelected = 0;
let lastState = null;

function stepTitle(number, text) {
  return el("h2", { class: "step-title" }, el("span", { class: "step-number" }, number), text);
}

// The lot's own photos, loaded straight from PropertyRoom's image host.
// They are image files, not pages, so nothing here is framed or copied.
// A photo that fails to load is dropped rather than shown broken.
function photoStrip(urls) {
  if (!urls || !urls.length) return null;
  return el("figure", { class: "photos" },
    el("figcaption", {}, "The lot's own photos — the agent reads these alongside the text."),
    el("div", { class: "photo-strip" }, urls.map((url, index) => el("img", {
      src: url, loading: "lazy", alt: "Photo " + (index + 1) + " from the lot listing",
      onerror: (event) => { event.target.remove(); } }))));
}

function listingView(lot) {
  const listing = lot.listing;
  const open = el("a", { class: "btn btn-sm", href: lot.url, target: "_blank", rel: "noopener noreferrer" },
    "Open the live page ↗");
  const bar = el("div", { class: "browser-bar" },
    el("span", { class: "dots", "aria-hidden": "true" }, el("i", {}), el("i", {}), el("i", {})),
    el("span", { class: "address" }, lot.url.replace("https://", "")),
    open);
  if (!listing) {
    return el("div", { class: "card browser" }, bar,
      el("div", { class: "browser-body" },
        el("p", { class: "muted" }, lot.error ? "This listing could not be read: " + lot.error
          : "Run the demo to read this listing off PropertyRoom. The button above opens the real page.")));
  }
  const live = listing.status === "live";
  const delivery = (listing.shipping_options || []).map((option) => el("tr", {},
    el("td", {}, option.method),
    el("td", { class: "r" }, option.available ? money(option.price_usd) : "Not available")));
  return el("div", { class: "card browser" }, bar,
    el("div", { class: "browser-body" },
      el("div", { class: "listing-head" },
        el("h3", {}, listing.title || lot.label),
        el("span", { class: "pill " + (live ? "pill-go pill-live" : "pill-prop") },
          live ? "live auction" : listing.status === "ended" ? "ended" : "status unclear")),
      el("p", { class: "listing-ends" }, listing.ends_text || "The page prints no dated Ends or Ended line."),
      listing.reserve_not_met && el("p", { class: "listing-reserve" },
        "Reserve Not Met. Bids on this item are subject to seller approval."),
      photoStrip(listing.photo_urls),
      el("dl", { class: "facts" },
        fact("Current price", money(listing.current_price)),
        fact("Bid increment", money(listing.bid_increment)),
        fact("Buyer's premium", listing.premium_pct !== null ? listing.premium_pct.toFixed(2) + "%"
          : listing.premium_absent ? "Not listed" : "Unreadable",
          listing.premium_pct !== null ? "printed in the auction table" : listing.premium_absent ? "the page never mentions one" : null),
        fact("Condition", listing.condition || "Not stated")),
      delivery.length ? el("div", { class: "table-wrap" }, el("table", { class: "table listing-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "Delivery method"), el("th", { class: "r" }, "Price"))),
        el("tbody", {}, delivery))) : null,
      el("p", { class: "fine" },
        "Read off the listing at " + new Date(lot.fetched_at).toLocaleTimeString() +
        ". PropertyRoom does not let its pages be shown inside other sites, so this is the listing's own text and photos. Tax is not on the page."),
      el("details", { class: "site-peek" },
        el("summary", {}, "What a lot page looks like on the site"),
        el("img", { src: "/propertyroom.gif", width: "680", height: "478", loading: "lazy",
          alt: "A PropertyRoom lot page scrolling from the photo, price and buyer's premium down through the description, vehicle details and pickup rules." }),
        el("p", { class: "fine" }, "A recording of one live lot page (a 2019 Chevrolet Cruze) from Nov 23, 2026, not this lot. The high bidder's line is blurred."))));
}

function terminalView(lot) {
  const command = lot.cli.command;
  const copy = el("button", { class: "btn btn-sm", type: "button",
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(command);
        toast("Command copied. Paste it into a terminal in the project folder.", "good");
      } catch (error) {
        toast("Could not copy. Select the command and copy it by hand.", "destructive");
      }
    } }, "Copy command");
  return el("div", { class: "card terminal" },
    el("div", { class: "browser-bar" },
      el("span", { class: "dots", "aria-hidden": "true" }, el("i", {}), el("i", {}), el("i", {})),
      el("span", { class: "address" }, "Terminal · police-auction-bidding-agent"),
      copy),
    el("pre", { class: "terminal-body", tabindex: "0" },
      el("span", { class: "prompt" }, "$ "), el("span", { class: "command" }, command), "\n",
      lot.cli.output ? "\n" + lot.cli.output
        : el("span", { class: "muted" }, "\nRun the demo to see what this prints.")),
    el("p", { class: "terminal-note" },
      "Both stages are printed here: VERDICT is the evaluator's call, and Policy is the spending gate's decision on it. The stamp in step 03 is the final outcome — the two do not always agree, and that is the point."));
}

function uiView(lot) {
  if (lot.error) return el("div", { class: "card pad" }, el("p", { class: "error-box" }, lot.error));
  if (!lot.listed) return el("div", { class: "card pad" }, el("p", { class: "muted" }, "Not run yet."));
  const final = lot.as_if_live || lot.listed;
  const runs = [el("div", { class: "run" + (lot.as_if_live ? "" : " single") },
    el("div", { class: "run-head" },
      el("span", { class: "eyebrow" }, "As listed · " + statusWord(lot.listed.evaluation)), verdictFinal(lot.listed)),
    resultBody(lot.listed))];
  if (lot.as_if_live) {
    runs.push(el("div", { class: "run" },
      el("div", { class: "run-head" }, el("span", { class: "eyebrow" }, "As if live"), verdictFinal(lot.as_if_live)),
      resultBody(lot.as_if_live)));
  }
  return el("article", { class: "card card-accent lot " + outcome(final).card },
    el("div", { class: "lot-head" },
      el("div", { class: "lot-title" },
        el("h3", {}, lot.label),
        el("p", { class: "lot-sub" }, "All-in max " + (lot.all_in_max === null ? "not given" : money(lot.all_in_max)) +
          (lot.live_now ? " · set a quarter above the next bid's all-in for the demo" : ""))),
      verdictFinal(final)),
    el("div", { class: "runs" }, runs));
}

function renderDemo(state) {
  lastState = state;
  const demo = state.demo;
  const live = state.mode === "live";
  $("demo-note").textContent =
    "Demo assumptions: " + (demo.tax_rate * 100).toFixed(0) + "% tax and " + money(demo.pickup_cost_usd) +
    " pickup cost on top of the page's fee (a local buyer). " +
    (live ? "On the live model a first run makes up to thirteen calls, a few cents in total. Running it again costs nothing unless a page changed."
      : "Fixture mode: no model is called, so there is no second read and no value estimate. Start the app with a key to see Muse Ember's part.");
  $("demo-run").textContent = demo.ran_at ? "Run it again" : "Run the demo";

  const finals = demo.lots.map((lot) => lot.as_if_live || lot.listed).filter(Boolean);
  const counts = { bid: 0, hold: 0, nobid: 0, none: 0 };
  for (const view of finals) counts[outcome(view).key] += 1;
  const liveLot = demo.lots.find((lot) => lot.live_now);
  $("demo-kpis").replaceChildren(...(demo.ran_at ? [
    kpi("Live right now", liveLot ? "1 auction" : "none found",
      liveLot ? "picked off PropertyRoom at " + new Date(demo.ran_at).toLocaleTimeString() : "the vehicles listing had no live lot",
      liveLot ? "good" : "warn"),
    kpi("Lots run", String(finals.length), "of " + demo.lots.length),
    kpi("Bid plans", String(counts.bid), "math clears and nothing objects", counts.bid ? "good" : undefined),
    kpi("Held for you", String(counts.hold), "a person decides", counts.hold ? "warn" : undefined),
    kpi("No bid", String(counts.nobid), "ended, or over the max"),
  ] : []));

  demoSelected = Math.min(demoSelected, demo.lots.length - 1);
  $("demo-tabs").replaceChildren(...demo.lots.map((lot, index) => {
    const final = lot.as_if_live || lot.listed;
    return el("button", { class: "tab", type: "button", role: "tab",
        "aria-selected": String(index === demoSelected),
        onclick: () => { demoSelected = index; renderDemo(lastState); } },
      el("span", { class: "tab-kind" }, lot.live_now ? "Live now"
        : ((lot.listing && lot.listing.status === "live")
           || (lot.listed && lot.listed.evaluation.status === "live")) ? "Live lot" : "Ended lot"),
      el("span", { class: "tab-name" }, lot.label.split(" — ")[0]),
      lot.error ? el("span", { class: "pill verdict pill-nogo" }, "ERROR") : verdictPill(final));
  }));
  // Keep the selected tab in view: with six lots the strip scrolls, and
  // the bid plan's tab must not sit hidden off the right edge.
  const selectedTab = $("demo-tabs").querySelector('[aria-selected="true"]');
  if (selectedTab) selectedTab.scrollIntoView({ block: "nearest", inline: "nearest" });

  const lot = demo.lots[demoSelected];
  $("demo-views").replaceChildren(
    stepTitle("01", "The listing on PropertyRoom"), listingView(lot),
    stepTitle("02", "The same lot on the command line"), terminalView(lot),
    stepTitle("03", "The same lot in this UI"), uiView(lot));
}

// ---- Lots -------------------------------------------------------------------

function maxForm(lot) {
  const input = el("input", { type: "number", min: "0", step: "0.01", inputmode: "decimal",
    placeholder: "none", value: lot.all_in_max === null ? null : lot.all_in_max });
  const suggested = lot.evaluation && lot.evaluation.suggested_all_in_max;
  return el("form", { class: "max-form",
      onsubmit: async (event) => {
        event.preventDefault();
        const data = await send("/api/lots/max", { url: lot.url, all_in_max: numberOrNull(input) }, "Re-working the verdict…");
        if (data) toastVerdict(data, lot.url);
      } },
    el("label", { class: "field" },
      el("span", {}, "Your all-in max" + (suggested ? " (suggested " + money(suggested) + ")" : "")),
      el("span", { class: "money-input" }, input)),
    el("button", { class: "btn", type: "submit" }, "Update"));
}

const lotId = (url) => { const match = /\/(\d+)\/?$/.exec(url || ""); return match ? match[1] : null; };
const lotTag = (lot) => { const id = lotId(lot.url); return id ? "Lot " + id : "Lot link"; };

function lotCard(lot) {
  const e = lot.evaluation;
  const actions = el("div", { class: "lot-actions" },
    lot.checked_at && el("span", { class: "checked" },
      "Checked " + new Date(lot.checked_at).toLocaleTimeString() + (lot.used_model ? " · model called" : "")),
    el("button", { class: "btn btn-quiet", type: "button",
      onclick: async () => { const data = await send("/api/lots/check", { url: lot.url }, "Re-reading the lot page…"); if (data) toastVerdict(data, lot.url); } }, "Check again"),
    el("button", { class: "btn btn-quiet", type: "button",
      onclick: async () => { const data = await send("/api/lots/remove", { url: lot.url }, "Removing…"); if (data) toast("Lot removed.", "default"); } }, "Remove"));

  if (!e) {
    return el("article", { class: "card card-accent lot " + (lot.error ? "card-crit" : "card-slate") },
      el("div", { class: "lot-head" },
        el("div", { class: "lot-title" },
          el("h3", {}, lotLink(lot.url, lot.url)),
          el("p", { class: "lot-sub" }, lotTag(lot) + " — " + (lot.error ? "could not be evaluated" : "not checked since the app started"))),
        el("span", { class: "stamp " + (lot.error ? "stamp-nobid" : "stamp-none"), "data-state": lot.error ? "error" : "none" }, lot.error ? "ERROR" : "NOT CHECKED")),
      lot.error && el("p", { class: "error-box" }, lot.error),
      el("div", { class: "lot-foot" }, maxForm(lot), actions));
  }
  return el("article", { class: "card card-accent lot " + outcome(lot).card },
    el("div", { class: "lot-head" },
      el("div", { class: "lot-title" },
        el("h3", {}, lotLink(lot.url, e.title || lot.url)),
        el("p", { class: "lot-sub" }, lotTag(lot), " — ", statusWord(e))),
      verdictFinal(lot)),
    photoStrip(e.photo_urls),
    resultBody(lot),
    el("div", { class: "lot-foot" }, maxForm(lot), actions));
}

function renderLots(state) {
  const s = state.settings;
  $("lots-assumptions").textContent =
    "Using your assumptions: tax " + (s.tax_rate === null ? "not set (every lot will be held)" : (s.tax_rate * 100).toFixed(2) + "%") +
    ", pickup cost " + (s.pickup_cost_usd === null ? "not set (never pick up)" : money(s.pickup_cost_usd)) +
    ", delivery " + s.shipping_preference + ". Change them on the Policy page." +
    (state.hosted ? " Your lots are kept in this browser, not on the server." : "");
  $("lots-limits").textContent =
    "Two limits, and a bid plan must clear both: " + usd.format(state.policy.tx_cap_usd) +
    " is the most any single transaction can cost all-in (PropertyRoom's card and PayPal ceiling); " +
    usd.format(state.policy.session_budget_usd) +
    " is the most this session may spend in total. A bid the evaluator likes can still be stopped by either one.";
  $("lots-title").textContent = "Your lots (" + state.lots.length + ")";
  $("lots").replaceChildren(...(state.lots.length ? state.lots.map(lotCard)
    : [el("div", { class: "card empty" }, "No lots yet. Paste a PropertyRoom lot link above to see what the agent would do.")]));
}

// ---- Policy -------------------------------------------------------------------

function renderPolicy(state) {
  const policy = state.policy;
  $("policy-kpis").replaceChildren(
    kpi("Per-lot cap", usd.format(policy.tx_cap_usd), "all-in, card and PayPal ceiling"),
    kpi("Session budget", usd.format(policy.session_budget_usd), "across every lot in a session"),
    kpi("Committed", usd.format(policy.exposure_usd), "nothing commits in dry-run"),
    kpi("Room left", usd.format(policy.headroom_usd), "the most one bid plan may cost", "good"));
  $("rule-cap").textContent = usd.format(policy.tx_cap_usd);
  $("rule-budget").textContent = usd.format(policy.session_budget_usd);
  $("rule-reserve").textContent = policy.allow_reserve_lots
    ? "Reserve lots are allowed, and get a plain bid instead of a proxy."
    : "Reserve lots are held for you.";
  if (state.hosted) {
    $("policy-source").textContent = "The cap, the session budget and the reserve setting are the project's defaults here. Run it locally to set your own in config.json.";
    $("settings-kept").textContent = "They are kept in this browser.";
  }
  if (!settingsShown) {
    const s = state.settings;
    $("set-tax").value = s.tax_rate === null ? "" : +(s.tax_rate * 100).toFixed(4);
    $("set-pickup").value = s.pickup_cost_usd === null ? "" : s.pickup_cost_usd;
    $("set-shipping").value = s.shipping_preference;
    settingsShown = true;
  }
}

// ---- Ledger -------------------------------------------------------------------

function lotName(url) {
  if (url === "api-parameter-probe") return "API settings check";
  const match = /\/l\/([^/]+)\//.exec(url);
  return match ? match[1].replace(/-/g, " ") : url;
}

function renderLedger(state) {
  const ledger = state.ledger;
  const entries = state.ledger_entries;
  const evaluations = entries.filter((entry) => !entry.simulated && entry.lot_url !== "api-parameter-probe" && entry.cost_usd > 0);
  const average = evaluations.length
    ? evaluations.reduce((sum, entry) => sum + entry.cost_usd, 0) / evaluations.length : null;
  const share = Math.min(100, (ledger.total_spent_usd / ledger.budget_usd) * 100);
  $("ledger-hosted").hidden = !state.hosted;

  $("ledger-kpis").replaceChildren(
    kpi("Spent", small(ledger.total_spent_usd), "of " + usd.format(ledger.budget_usd)),
    kpi("Left", small(ledger.remaining_usd), "a call that could cross it is never sent", "good"),
    kpi("Model calls", String(ledger.live_calls), plural(ledger.simulated_calls, "simulated run")),
    kpi("Per evaluation", average === null ? "n/a" : small(average),
      average === null ? "no live evaluations yet"
        : "so $10 buys about " + Math.floor(ledger.budget_usd / average).toLocaleString() + " of them"));
  $("budget-share").textContent = share.toFixed(2) + "%";
  $("budget-bar").style.width = share + "%";
  $("budget-meter").setAttribute("aria-label", "Model budget used: " + share.toFixed(2) + " percent");

  const head = el("thead", {}, el("tr", {},
    el("th", {}, "When"), el("th", {}, "Lot"), el("th", { class: "r" }, "Tokens in"),
    el("th", { class: "r" }, "Tokens out"), el("th", { class: "r" }, "Cost"), el("th", {}, "Kind")));
  const rows = entries.slice().reverse().map((entry) => el("tr", {},
    el("td", {}, new Date(entry.ts).toLocaleString()),
    el("td", {}, lotName(entry.lot_url)),
    el("td", { class: "r" }, entry.input_tokens.toLocaleString()),
    el("td", { class: "r" }, entry.output_tokens.toLocaleString()),
    el("td", { class: "r" }, "$" + entry.cost_usd.toFixed(6)),
    el("td", {}, el("span", { class: "pill " + (entry.simulated ? "" : "pill-plan") }, entry.simulated ? "simulated" : "live"))));
  $("ledger-table").replaceChildren(head, el("tbody", {},
    rows.length ? rows : el("tr", {}, el("td", { colspan: "6" }, "No calls yet."))));
}

// ---- wiring ---------------------------------------------------------------

const RENDERERS = { overview: renderOverview, demo: renderDemo, lots: renderLots, policy: renderPolicy, ledger: renderLedger };

function render(state) {
  renderChrome(state);
  if (RENDERERS[PAGE]) RENDERERS[PAGE](state);
}

function wire() {
  if (PAGE === "demo") {
    // One lot per request: each stays short, and results appear as they come.
    $("demo-run").addEventListener("click", async () => {
      const fixed = lastState ? lastState.demo.lots.filter((lot) => !lot.live_now).length : 4;
      const steps = ["live", ...Array.from({ length: fixed }, (unused, index) => index)];
      let finished = true;
      for (const [position, step] of steps.entries()) {
        const working = step === "live"
          ? "Looking for an auction that is live on PropertyRoom right now…"
          : "Running lot " + (position + 1) + " of " + steps.length + "…";
        if (!(await send("/api/demo/run", { step }, working))) { finished = false; break; }
        if (step === "live") demoSelected = 0;
      }
      if (finished && lastState) {
        const finals = lastState.demo.lots.map((lot) => lot.as_if_live || lot.listed).filter(Boolean);
        const counts = { bid: 0, hold: 0, nobid: 0 };
        for (const view of finals) { const key = outcome(view).key; if (counts[key] !== undefined) counts[key] += 1; }
        // The bid plan is the payoff: open on its tab so the BID stamp is
        // visible without scrolling past all the holds.
        const bidIndex = lastState.demo.lots.findIndex((lot) => {
          const final = lot.as_if_live || lot.listed;
          return final && outcome(final).key === "bid";
        });
        if (bidIndex >= 0) { demoSelected = bidIndex; renderDemo(lastState); }
        toast("Demo finished: " + counts.bid + " bid plan" + (counts.bid === 1 ? "" : "s") +
          ", " + counts.hold + " held, " + counts.nobid + " no bid.", "default");
      }
    });
  }
  if (PAGE === "lots") {
    $("add-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const url = $("add-url").value;
      const added = await send("/api/lots",
        { url, all_in_max: numberOrNull($("add-max")) },
        "Reading the lot page and working out a verdict. This can take a few seconds…");
      if (added) {
        $("add-url").value = "";
        $("add-max").value = "";
        toastVerdict(added, url);
      }
    });
    $("check-all").addEventListener("click", async () => {
      const data = await send("/api/lots/check-all", {}, "Re-reading every live lot…");
      if (data) toastCounts(data);
    });
    $("auto-check").addEventListener("change", () => {
      clearInterval(autoTimer);
      const minutes = Number($("auto-check").value);
      if (minutes > 0) {
        autoTimer = setInterval(() => send("/api/lots/check-all", {}, "Re-reading every live lot…"), minutes * 60 * 1000);
      }
    });
  }
  if (PAGE === "policy") {
    $("settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const tax = numberOrNull($("set-tax"));
      const data = await send("/api/settings", {
        tax_rate: tax === null ? null : tax / 100,
        pickup_cost_usd: numberOrNull($("set-pickup")),
        shipping_preference: $("set-shipping").value,
      }, "Re-working every verdict…");
      if (data) toast("Assumptions updated. Verdicts re-worked without a model call.", "good");
    });
  }
}

// ---- theme (shadcn: dark mode is a token swap) ------------------------------

function setTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  try { localStorage.setItem("paba-theme", dark ? "dark" : "light"); } catch (error) {}
  const toggle = $("theme-toggle");
  if (toggle) toggle.setAttribute("aria-pressed", String(dark));
}

function initTheme() {
  const toggle = $("theme-toggle");
  if (!toggle) return;
  toggle.setAttribute("aria-pressed", String(document.documentElement.classList.contains("dark")));
  toggle.addEventListener("click", () => setTheme(!document.documentElement.classList.contains("dark")));
}

// ---- command palette (cmdk): keyboard-first, grouped, data attributes ------

const COMMANDS = [
  { group: "Pages", label: "Go to Overview", hint: "1", run: () => { location.href = "/"; } },
  { group: "Pages", label: "Go to Demo", hint: "2", run: () => { location.href = "/demo"; } },
  { group: "Pages", label: "Go to Lots", hint: "3", run: () => { location.href = "/lots"; } },
  { group: "Pages", label: "Go to Policy", hint: "4", run: () => { location.href = "/policy"; } },
  { group: "Pages", label: "Go to Ledger", hint: "5", run: () => { location.href = "/ledger"; } },
  { group: "Pages", label: "Go to Method", hint: "6", run: () => { location.href = "/method"; } },
  { group: "Pages", label: "Go to Command line", hint: "7", run: () => { location.href = "/cli"; } },
  { group: "Actions", label: "Evaluate a lot", hint: "", run: () => {
    try { sessionStorage.setItem("paba-focus-add-url", "1"); } catch (error) {}
    location.href = "/lots";
  } },
  { group: "Actions", label: "Check all lots", hint: "", run: async () => {
    const data = await send("/api/lots/check-all", {}, "Re-reading every live lot…");
    if (data) toastCounts(data);
  } },
  { group: "Actions", label: "Run the demo", hint: "", run: () => {
    if (PAGE === "demo") { $("demo-run").click(); return; }
    try { sessionStorage.setItem("paba-auto-run-demo", "1"); } catch (error) {}
    location.href = "/demo";
  } },
  { group: "Actions", label: "Toggle dark mode", hint: "", run: () => setTheme(!document.documentElement.classList.contains("dark")) },
];

let paletteOpen = false;
let paletteSelected = 0;
let paletteItems = [];
let paletteReturnFocus = null;

function renderPaletteList(filter) {
  const list = $("palette-list");
  const query = (filter || "").trim().toLowerCase();
  paletteItems = COMMANDS.filter((cmd) => !query || cmd.label.toLowerCase().includes(query));
  paletteSelected = 0;
  list.replaceChildren();
  let lastGroup = null;
  for (const [index, cmd] of paletteItems.entries()) {
    if (cmd.group !== lastGroup) {
      list.append(el("div", { class: "palette-group", role: "presentation" }, cmd.group));
      lastGroup = cmd.group;
    }
    list.append(el("button", {
      class: "palette-item", type: "button", role: "option", id: "palette-item-" + index,
      "data-cmdk-item": "", "data-selected": String(index === paletteSelected),
      "aria-selected": String(index === paletteSelected),
      onclick: () => { paletteSelected = index; paletteRun(); },
    }, el("span", {}, cmd.label), cmd.hint && el("span", { class: "hint" }, cmd.hint)));
  }
  $("palette-empty").hidden = paletteItems.length > 0;
  const input = $("palette-input");
  input.setAttribute("aria-activedescendant", paletteItems.length ? "palette-item-0" : "");
}

function paletteMove(delta) {
  if (!paletteItems.length) return;
  paletteSelected = (paletteSelected + delta + paletteItems.length) % paletteItems.length;
  for (const [index, node] of [...$("palette-list").querySelectorAll("[data-cmdk-item]")].entries()) {
    node.setAttribute("data-selected", String(index === paletteSelected));
    node.setAttribute("aria-selected", String(index === paletteSelected));
  }
  $("palette-input").setAttribute("aria-activedescendant", "palette-item-" + paletteSelected);
  const active = $("palette-item-" + paletteSelected);
  if (active) active.scrollIntoView({ block: "nearest" });
}

function paletteRun() {
  const cmd = paletteItems[paletteSelected];
  if (!cmd) return;
  closePalette();
  cmd.run();
}

function openPalette() {
  paletteOpen = true;
  paletteReturnFocus = document.activeElement;
  $("palette").hidden = false;
  $("palette-input").value = "";
  renderPaletteList("");
  $("palette-input").focus();
}

function closePalette() {
  paletteOpen = false;
  $("palette").hidden = true;
  if (paletteReturnFocus && paletteReturnFocus.focus) paletteReturnFocus.focus();
}

function initPalette() {
  const palette = $("palette");
  const input = $("palette-input");
  const trigger = $("palette-button");
  if (!palette || !input) return;
  if (trigger) trigger.addEventListener("click", openPalette);
  palette.addEventListener("click", (event) => { if (event.target === palette) closePalette(); });
  input.addEventListener("input", () => renderPaletteList(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); paletteMove(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); paletteMove(-1); }
    else if (event.key === "Enter") { event.preventDefault(); paletteRun(); }
    else if (event.key === "Escape") { event.preventDefault(); closePalette(); }
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (paletteOpen) closePalette(); else openPalette();
    }
  });
}

function initDeepLinks() {
  try {
    if (sessionStorage.getItem("paba-focus-add-url")) {
      sessionStorage.removeItem("paba-focus-add-url");
      const input = $("add-url");
      if (input) input.focus();
    }
    if (sessionStorage.getItem("paba-auto-run-demo")) {
      sessionStorage.removeItem("paba-auto-run-demo");
      const button = $("demo-run");
      if (button) button.click();
    }
  } catch (error) {}
}

setUpNav();
wire();
initTheme();
initPalette();
initDeepLinks();
send("/api/state", undefined, "");
