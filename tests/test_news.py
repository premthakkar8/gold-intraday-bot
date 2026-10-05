"""Headline scoring for the dollar and for oil."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tbot.news import Headline, score_news, score_title


class NewsTests(unittest.TestCase):
    def test_hawkish_phrase_is_not_read_as_a_rate_cut(self):
        usd, oil, shock = score_title("Fed official says rate cuts can wait as jobs stay strong")
        self.assertGreater(usd, 0)
        self.assertEqual(oil, 0)
        self.assertFalse(shock)

    def test_soft_dollar_headline_is_bearish_for_the_dollar(self):
        usd, oil, shock = score_title("Dollar slides after cool CPI")
        self.assertLess(usd, 0)
        self.assertTrue(shock)
        self.assertEqual(oil, 0)

    def test_oil_supply_cut_is_bullish_and_a_shock(self):
        usd, oil, shock = score_title("OPEC agrees surprise output cut")
        self.assertGreater(oil, 0)
        self.assertTrue(shock)
        self.assertEqual(usd, 0)

    def test_inventory_build_is_bearish_oil(self):
        usd, oil, shock = score_title("Crude inventories post a large inventory build")
        self.assertLess(oil, 0)
        self.assertFalse(shock)
        self.assertEqual(usd, 0)

    def test_old_shock_headline_does_not_keep_the_session_in_shock(self):
        now = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        headlines = [
            Headline(
                title="CPI beats and the dollar surges",
                published=now - timedelta(hours=10),
                topic="usd",
            )
        ]
        read = score_news(headlines, now=now, lookback_hours=18)
        self.assertGreater(read.usd_score, 0)
        self.assertFalse(read.shock)


if __name__ == "__main__":
    unittest.main()
