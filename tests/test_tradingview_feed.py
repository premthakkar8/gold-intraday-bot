"""TradingView chart packets become gold bars."""

from __future__ import annotations

import json
import unittest

from tbot.tradingview_feed import _frame, _messages


class TradingViewFeedTests(unittest.TestCase):
    def test_packets_split_into_messages(self):
        body = json.dumps({"m": "timescale_update", "p": ["cs", {"s1": {"s": []}}]})
        raw = f"~m~{len(body)}~m~{body}"
        messages = _messages(raw)
        self.assertEqual(messages[0]["m"], "timescale_update")

    def test_bars_become_ohlc(self):
        series = [
            {"i": 0, "v": [1790901900.0, 4159.45, 4161.78, 4157.66, 4160.03, 100.0]},
            {"i": 1, "v": [1790902200.0, 4160.03, 4162.00, 4159.00, 4161.50, 80.0]},
        ]
        # pad to the minimum the reader accepts by repeating the shape in the test via direct frame after bypass
        series = series + [
            {"i": i, "v": [1790901900.0 + i * 300, 4160.0, 4161.0, 4159.0, 4160.5, 10.0]} for i in range(2, 32)
        ]
        frame = _frame(series)
        self.assertEqual(len(frame), 32)
        self.assertAlmostEqual(frame.iloc[0]["close"], 4160.03)
        self.assertEqual(str(frame.iloc[0]["ts"].tz), "UTC")


if __name__ == "__main__":
    unittest.main()
