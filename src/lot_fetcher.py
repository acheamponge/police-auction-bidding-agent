"""Fetch a PropertyRoom lot page and read its fee table — never assume one.

Lot URLs look like:
    https://www.propertyroom.com/l/<slug>/<listing-id>
    https://www.propertyroom.com/listing.aspx?l=<listing-id>

Fields the parser reads off the lot's OWN page (verified against live pages
Nov 23, 2026):
    - title, listing id, item number
    - status: live ("Ends <date> at <time> (Eastern)"), ended ("Ended <date>
      at <time> (Eastern)"), or unknown when neither line is printed. Every
      page carries the bare words "Ending" and "Ended" in its countdown
      skeleton, so only the dated line counts.
    - current price ("Current Price US $X")
    - buyer's premium ("Buyer's Premium is non-refundable. NN.NN%") — the
      rate is READ, never hardcoded. If the page prints no premium line,
      premium_pct is None and premium_stated is False. The sources conflict
      (PropertyRoom help: most everyday lots carry none, 10/12/15% mainly on
      capital assets; third-party guides claim 16.5% standard) — so a missing
      line means "unknown", never 0% and never 16.5%.
    - bid increment ("Bid Increment (US) $X")
    - reserve: "Reserve Not Met — Bids on this item are subject to seller
      approval." (the soft-reserve trap)
    - condition ("Condition: Untested - Sold as-is")
    - shipping table: Customer Pick-up / Standard / Expedited with a price
      or "Not Available"
    - bid deposit mentions

Tax is NOT printed on a lot page — it is computed at checkout from the
ship-to state. The agent treats tax as a configured assumption (see
evaluator.py), never as a fact read from the page.
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


def fetch_lot_page(url: str, timeout_s: int = 30) -> str:
    """Fetch the raw HTML of a lot page. No login, no session — public pages."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"PropertyRoom returned HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Could not reach PropertyRoom: {exc}") from exc


_INVISIBLE_TAGS = ("script", "style", "noscript")


class _TextExtractor(HTMLParser):
    """Strip tags but keep line structure, so the fee labels stay findable.
    Script and style bodies are dropped: they are not page text."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._invisible_depth = 0

    def _flush(self):
        text = "".join(self._buf).strip()
        if text:
            self.lines.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _INVISIBLE_TAGS:
            self._invisible_depth += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "td", "th"):
            self._flush()

    def handle_endtag(self, tag):
        if tag in _INVISIBLE_TAGS:
            self._invisible_depth = max(0, self._invisible_depth - 1)
        if tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "td", "th"):
            self._flush()

    def handle_data(self, data):
        if not self._invisible_depth:
            self._buf.append(data)


def html_to_text(html_text: str) -> str:
    parser = _TextExtractor()
    parser.feed(html_text)
    parser._flush()
    return "\n".join(html_lib.unescape(line) for line in parser.lines)


_MONEY = r"\$?\s*([\d,]+\.\d{2})"
_END_DATE = r"[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+at\s+[\d:]+\s+[AP]M\s*\(Eastern\)"

# The lot itself starts at its "Listing ID" line; everything above is site
# navigation. The fee table, description and shipping table all sit within
# the first ~17K characters after it on the pages checked.
LOT_BODY_LIMIT_CHARS = 20_000


def lot_body(text: str) -> str:
    """The part of the page text worth sending to the model: from the lot's
    title line (the line above "Listing ID") onward, capped in length."""
    marker = text.find("Listing ID:")
    if marker == -1:
        return text[:LOT_BODY_LIMIT_CHARS]
    title_start = text.rfind("\n", 0, max(0, text.rfind("\n", 0, marker))) + 1
    return text[title_start:title_start + LOT_BODY_LIMIT_CHARS]


def _money(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_lot_text(text: str) -> dict:
    """Parse the cleaned text of a lot page into structured fields."""
    flat = re.sub(r"[ \t]+", " ", text)

    title = None
    match = re.search(r"^(.+?)\s*-\s*PropertyRoom\.com", text, re.MULTILINE)
    if match:
        title = match.group(1).strip()

    listing_id = None
    match = re.search(r"Listing ID:\s*(\d+)", flat)
    if match:
        listing_id = match.group(1)
    item_number = None
    match = re.search(r"Item #:\s*([\d\-]+)", flat)
    if match:
        item_number = match.group(1)

    status = "unknown"
    ends_text = None
    match = re.search(r"\b(Ended|Ends)\s+(" + _END_DATE + r")", flat)
    if match:
        status = "ended" if match.group(1) == "Ended" else "live"
        ends_text = f"{match.group(1)} {match.group(2)}"

    current_price = _money(r"Current Price\s*(?:US)?\s*" + _MONEY, flat)

    premium_pct = None
    premium_stated = False
    match = re.search(r"Buyer's Premium[^0-9%]{0,80}?(\d{1,2}(?:\.\d{1,2})?)\s*%",
                      flat, re.IGNORECASE)
    if match:
        premium_pct = float(match.group(1))
        premium_stated = True

    bid_increment = _money(r"Bid Increment\s*(?:\(US\))?\s*" + _MONEY, flat)

    reserve_not_met = bool(re.search(r"Reserve Not Met", flat, re.IGNORECASE))

    condition = None
    match = re.search(r"Condition:\s*(.+)", flat)
    if match:
        condition = match.group(1).strip()

    deposit_required = bool(
        re.search(r"bid deposit", flat, re.IGNORECASE))

    shipping_options = []
    for method in ("Customer Pick-up", "Standard Shipping", "Expedited Shipping"):
        pattern = re.escape(method) + r"\s*(?:\([^)]*\))?\s*(Not Available|" + _MONEY + r")"
        match = re.search(pattern, flat, re.IGNORECASE)
        if match:
            raw = match.group(1)
            if raw and raw.lower().startswith("not available"):
                shipping_options.append({"method": method, "price_usd": None,
                                         "available": False})
            else:
                try:
                    price = float(raw.replace(",", "").replace("$", ""))
                except (ValueError, AttributeError):
                    price = None
                shipping_options.append({"method": method, "price_usd": price,
                                         "available": price is not None})

    return {
        "title": title,
        "listing_id": listing_id,
        "item_number": item_number,
        "status": status,
        "ends_text": ends_text,
        "current_price": current_price,
        "premium_pct": premium_pct,
        "premium_stated": premium_stated,
        "bid_increment": bid_increment,
        "reserve_not_met": reserve_not_met,
        "condition": condition,
        "deposit_required": deposit_required,
        "shipping_options": shipping_options,
    }


def fetch_and_parse(url: str) -> tuple[dict, str]:
    """Fetch a lot page and return (parsed fields, cleaned page text)."""
    raw_html = fetch_lot_page(url)
    text = html_to_text(raw_html)
    return parse_lot_text(text), text
