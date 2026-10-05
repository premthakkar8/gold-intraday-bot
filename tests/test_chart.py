"""Chart structure from OHLC bars."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from tbot.chart import read_chart


def _bars(closes, end: datetime) -> pd.DataFrame:
    start = end - timedelta(minutes=5 * (len(closes) - 1))
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "ts": start + timedelta(minutes=5 * index),
                "open": close,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1,
            }
        )
    return pd.DataFrame(rows)


class ChartTests(unittest.TestCase):
    def test_rising_swings_are_an_uptrend(self):
        price = 100.0
        closes = []
        for _ in range(6):
            for step in (1.2, 1.1, 1.0, 0.8, -0.5, -0.45, -0.35):
                price += step
                closes.append(price)
        end = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        read = read_chart(_bars(closes, end), now=end)
        self.assertEqual(read.structure, "up")
        self.assertEqual(read.session, "overlap")

    def test_a_wave_is_sideways(self):
        closes = [100 + 3 * math.sin(i / 2.2) for i in range(80)]
        end = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        read = read_chart(_bars(closes, end), now=end)
        self.assertEqual(read.structure, "sideways")
        self.assertGreaterEqual(read.vwap, read.day_low)
        self.assertLessEqual(read.vwap, read.day_high)

    def test_short_history_is_rejected(self):
        end = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            read_chart(_bars([100.0] * 10, end), now=end)


if __name__ == "__main__":
    unittest.main()
