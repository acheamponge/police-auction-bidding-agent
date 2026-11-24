"""Tests for the lot-page parser — run with: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import read_fixture
from src.lot_fetcher import (MAX_PHOTOS, LOT_BODY_LIMIT_CHARS, extract_photo_urls, html_to_text,
                             lot_body, parse_lot_text)


class TestLotParser(unittest.TestCase):
    def test_vehicle_lot_reads_premium(self):
        parsed = parse_lot_text(read_fixture("lot_vehicle.txt"))
        self.assertEqual(parsed["status"], "ended")
        self.assertEqual(parsed["ends_text"], "Ended Nov 11, 2026 at 05:20 PM (Eastern)")
        self.assertEqual(parsed["current_price"], 10850.00)
        self.assertEqual(parsed["premium_pct"], 12.00)
        self.assertTrue(parsed["premium_stated"])
        self.assertEqual(parsed["bid_increment"], 50.00)
        self.assertFalse(parsed["reserve_not_met"])
        self.assertEqual(parsed["condition"], "Untested - Sold as-is")
        methods = {o["method"]: o for o in parsed["shipping_options"]}
        self.assertEqual(methods["Customer Pick-up"]["price_usd"], 0.00)
        self.assertTrue(methods["Customer Pick-up"]["available"])
        self.assertFalse(methods["Standard Shipping"]["available"])

    def test_reserve_lot_flagged(self):
        parsed = parse_lot_text(read_fixture("lot_reserve.txt"))
        self.assertTrue(parsed["reserve_not_met"])
        self.assertEqual(parsed["status"], "live")
        self.assertEqual(parsed["premium_pct"], 15.00)
        self.assertEqual(parsed["current_price"], 4450.00)

    def test_lot_that_never_mentions_a_premium_has_none(self):
        # PropertyRoom states a buyer's premium in the auction table when a
        # lot has one. Most everyday lots have none and never mention it.
        parsed = parse_lot_text(read_fixture("lot_no_premium.txt"))
        self.assertEqual(parsed["status"], "live")
        self.assertIsNone(parsed["premium_pct"])
        self.assertFalse(parsed["premium_stated"])
        self.assertTrue(parsed["premium_absent"])
        self.assertEqual(parsed["current_price"], 4620.00)
        methods = {o["method"]: o for o in parsed["shipping_options"]}
        self.assertEqual(methods["Standard Shipping"]["price_usd"], 12.95)
        self.assertEqual(methods["Expedited Shipping"]["price_usd"], 24.95)

    def test_premium_mentioned_without_a_readable_rate_is_not_absent(self):
        # e.g. the markup changed and the rate moved: that is "unknown",
        # which must never be mistaken for "no premium".
        text = read_fixture("lot_vehicle.txt").replace("12.00%", "see Bid Review")
        parsed = parse_lot_text(text)
        self.assertIsNone(parsed["premium_pct"])
        self.assertFalse(parsed["premium_absent"])
        self.assertFalse(parse_lot_text(read_fixture("lot_vehicle.txt"))["premium_absent"])

    def test_countdown_skeleton_alone_is_not_a_status(self):
        # Every page, live or ended, prints the bare words "Ending" and
        # "Ended" in its countdown. Only the dated line says which it is.
        text = read_fixture("lot_reserve.txt").replace(
            "Ends Nov 30, 2026 at 06:24 PM (Eastern)\n", "")
        self.assertIn("Ending\nEnded\n", text)
        parsed = parse_lot_text(text)
        self.assertEqual(parsed["status"], "unknown")
        self.assertIsNone(parsed["ends_text"])

    def test_ended_line_in_another_format_is_unknown(self):
        text = read_fixture("lot_vehicle.txt").replace(
            "Ended Nov 11, 2026 at 05:20 PM (Eastern)", "Ended 11/11/2026 5:20 PM ET")
        self.assertEqual(parse_lot_text(text)["status"], "unknown")


class TestHtmlToText(unittest.TestCase):
    def setUp(self):
        self.text = html_to_text(read_fixture("lot_page.html"))

    def test_script_style_and_noscript_bodies_are_dropped(self):
        for leaked in ("decoy", "9,999.00", "listingEndDate", "listing-timer", "$7.00"):
            self.assertNotIn(leaked, self.text)

    def test_real_page_layout_parses_end_to_end(self):
        parsed = parse_lot_text(self.text)
        self.assertEqual(parsed["title"], "2007 Toyota Prius (Jamaica, NY 11430)")
        self.assertEqual(parsed["listing_id"], "18690397")
        # The script holds a decoy "Ended ..." line and a decoy price.
        self.assertEqual(parsed["status"], "live")
        self.assertEqual(parsed["ends_text"], "Ends Nov 30, 2026 at 05:00 PM (Eastern)")
        self.assertEqual(parsed["current_price"], 800.00)
        self.assertEqual(parsed["premium_pct"], 15.00)
        self.assertEqual(parsed["bid_increment"], 50.00)
        methods = {o["method"]: o for o in parsed["shipping_options"]}
        # The pickup row carries an address and zip code before its price.
        self.assertEqual(methods["Customer Pick-up"]["price_usd"], 23.95)
        self.assertTrue(methods["Customer Pick-up"]["available"])
        self.assertFalse(methods["Standard Shipping"]["available"])
        self.assertFalse(methods["Expedited Shipping"]["available"])


class TestLotBody(unittest.TestCase):
    def test_starts_at_the_title_line_and_keeps_the_shipping_table(self):
        body = lot_body(html_to_text(read_fixture("lot_page.html")))
        self.assertTrue(body.startswith("2007 Toyota Prius (Jamaica, NY 11430)\nListing ID: 18690397"))
        self.assertNotIn("Sign Out", body)
        self.assertIn("New York 11430\n$23.95", body)

    def test_is_capped(self):
        long_text = "nav\nTitle\nListing ID: 1\n" + "x" * (LOT_BODY_LIMIT_CHARS * 2)
        body = lot_body(long_text)
        self.assertEqual(len(body), LOT_BODY_LIMIT_CHARS)
        self.assertTrue(body.startswith("Title\nListing ID: 1"))

    def test_page_without_a_listing_id_is_sent_from_the_top(self):
        self.assertEqual(lot_body("just some text"), "just some text")


class TestPhotoUrls(unittest.TestCase):
    """The lot's own photos are the damage report: the listing's damage
    fields often say only "SEE VEHICLE IMAGES"."""
    HTML = """
    <html><body>
    <script>var gallery = {"Url": "https://content.propertyroom.com/listings/origimgs/19035234_01.jpg",
    "ThumbnailUrl": "https://content.propertyroom.com/listings/thmbimgs/19035234_01.jpg"};
    </script>
    <img src="https://content.propertyroom.com/listings/ttlimgs/19035234_01.jpg">
    <img src="https://content.propertyroom.com/listings/origimgs/19035234_02.jpg">
    <img src="https://content.propertyroom.com/listings/banners/merchant.jpg">
    <img src="https://example.invalid/not-a-lot-photo.jpg">
    </body></html>
    """

    def test_the_same_photo_at_three_sizes_is_kept_once_at_full_size(self):
        self.assertEqual(extract_photo_urls(self.HTML), [
            "https://content.propertyroom.com/listings/origimgs/19035234_01.jpg",
            "https://content.propertyroom.com/listings/origimgs/19035234_02.jpg",
        ])

    def test_merchant_banners_and_other_hosts_are_not_photos(self):
        urls = extract_photo_urls(self.HTML)
        self.assertNotIn("https://content.propertyroom.com/listings/banners/merchant.jpg", urls)
        self.assertNotIn("https://example.invalid/not-a-lot-photo.jpg", urls)

    def test_a_page_with_no_photos_is_empty_not_an_error(self):
        self.assertEqual(extract_photo_urls("<html><body>no photos here</body></html>"), [])

    def test_the_count_is_capped(self):
        html = "".join(
            f'<img src="https://content.propertyroom.com/listings/origimgs/1_{n}.jpg">'
            for n in range(10))
        self.assertEqual(len(extract_photo_urls(html)), MAX_PHOTOS)


if __name__ == "__main__":
    unittest.main()
