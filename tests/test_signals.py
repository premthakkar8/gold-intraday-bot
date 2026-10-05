"""Two-sided setups, research text, and live entry, SL, and TP alerts."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from tbot import signals
from tbot.config import Settings
from tbot.econ_calendar import Calendar
from tbot.scenarios import dollar_bid
from tbot.strategy import generate

NOW = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)


def _brief():
    chart, macro, news, now, htf = dollar_bid()
    plan = generate(chart, macro, news, htf_structure=htf, now=now)
    return SimpleNamespace(chart=chart, macro=macro, news=news, plan=plan, htf_structure=htf)


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.settings = Settings(memory_path=Path(self.tmp.name) / "memory.json")
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, title, body, priority=None):
        self.sent.append((title, body, priority))

    def test_both_sides_have_entry_sl_tp_within_risk(self):
        setups = signals.build_setups(_brief(), self.settings.risk)
        self.assertEqual({item.side for item in setups}, {"BUY", "SELL"})
        for item in setups:
            self.assertLessEqual(item.risk_usd, 5.0 + 1e-9)
            self.assertLessEqual(item.rr, 4.0 + 1e-9)
            if item.side == "BUY":
                self.assertLess(item.sl, item.entry_low)
                self.assertGreater(item.tp1, item.entry_high)
            else:
                self.assertGreater(item.sl, item.entry_high)
                self.assertLess(item.tp1, item.entry_low)
        self.assertEqual(sum(item.favored for item in setups), 1)

    def test_alert_carries_the_data_behind_it(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        title, body, _ = self.sent[0]
        self.assertIn("Gold", title)
        self.assertIn("BUY XAUUSD", body)
        self.assertIn("SELL XAUUSD", body)
        self.assertIn("SL", body)
        self.assertIn("TP1", body)
        self.assertIn("DXY 101.40 (+0.30% today, up)", body)
        self.assertIn("gold-dollar corr -0.60", body)
        self.assertIn("Fed official says rate cuts can wait", body)

    def test_same_setups_are_not_sent_twice(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW + timedelta(minutes=15), self.send)
        self.assertEqual(len(self.sent), 1)

    def test_buy_is_invalid_when_the_dollar_is_running_against_gold(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        state = signals._load(self.settings)
        buy = next(item for item in state["setups"] if item["side"] == "BUY")
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        self.assertEqual(buy["status"], "invalid")
        self.assertIn("Dollar is up", buy["reason"])
        self.assertIn(sell["status"], ("valid", "far", "weak"))
        self.assertTrue(sell["favored"])

    def test_minute_check_invalidates_a_signal_when_price_breaks_the_stop(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        state = signals._load(self.settings)
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        spike = pd.DataFrame(
            [{"ts": NOW + timedelta(minutes=1), "open": sell["sl"] + 0.5, "high": sell["sl"] + 0.6, "low": sell["sl"] + 0.4, "close": sell["sl"] + 0.5, "volume": 1}]
        )
        self.sent.clear()
        signals.tick(self.settings, spike, NOW + timedelta(minutes=1), self.send)
        state = signals._load(self.settings)
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        self.assertEqual(sell["status"], "invalid")
        self.assertTrue(any("signal invalid" in title for title, _, _ in self.sent))
        self.assertFalse(any(title.startswith("ENTRY NOW") for title, _, _ in self.sent))

    def test_signal_expires_after_two_hours_without_entry(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        state = signals._load(self.settings)
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        quiet = pd.DataFrame(
            [{"ts": NOW + timedelta(minutes=121), "open": sell["tp1"] - 5, "high": sell["tp1"] - 4.8, "low": sell["tp1"] - 5.2, "close": sell["tp1"] - 5, "volume": 1}]
        )
        signals.tick(self.settings, quiet, NOW + timedelta(minutes=121), self.send)
        state = signals._load(self.settings)
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        self.assertEqual(sell["status"], "expired")

    def test_entry_then_take_profit_alerts(self):
        signals.update(self.settings, _brief(), Calendar([], NOW), None, NOW, self.send)
        state = signals._load(self.settings)
        sell = next(item for item in state["setups"] if item["side"] == "SELL")
        touch = pd.DataFrame(
            [{"ts": NOW + timedelta(minutes=1), "open": sell["entry_low"], "high": sell["entry_low"] + 0.1, "low": sell["entry_low"] - 0.2, "close": sell["entry_low"], "volume": 1}]
        )
        signals.tick(self.settings, touch, NOW + timedelta(minutes=1), self.send)
        self.assertTrue(any(title.startswith("ENTRY NOW: SELL") for title, _, _ in self.sent))
        hit = pd.DataFrame(
            [{"ts": NOW + timedelta(minutes=2), "open": sell["tp1"] + 1, "high": sell["tp1"] + 1, "low": sell["tp1"] - 0.1, "close": sell["tp1"], "volume": 1}]
        )
        signals.tick(self.settings, hit, NOW + timedelta(minutes=2), self.send)
        self.assertTrue(any(title.startswith("TP hit: SELL") for title, _, _ in self.sent))


if __name__ == "__main__":
    unittest.main()
