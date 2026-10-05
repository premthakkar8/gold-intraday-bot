"""Paper demo account fills, stops, targets, and spread."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from tbot.paper import PaperBroker, PaperSettings

START = datetime(2026, 10, 6, 13, 0, tzinfo=timezone.utc)


def _bars(rows):
    return pd.DataFrame(
        [
            {"ts": START + timedelta(minutes=5 * i), "open": c, "high": h, "low": l, "close": c, "volume": 1}
            for i, (h, l, c) in enumerate(rows)
        ]
    )


def _plan(**overrides):
    base = dict(
        id="p1", bias="short", entry_low=2655.0, entry_high=2658.0, invalidation=2659.0,
        target=2647.0, units=1.0, risk_budget=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class PaperTests(unittest.TestCase):
    def _broker(self, tmp):
        broker = PaperBroker(PaperSettings(start_balance=100, spread=0.30), Path(tmp) / "paper.json")
        broker.connect()
        return broker

    def test_limit_fills_then_hits_target(self):
        with TemporaryDirectory() as tmp:
            broker = self._broker(tmp)
            broker.manage(START, 120, False, _bars([(2651, 2649, 2650)]))
            note = broker.place(_plan())
            self.assertIn("SELL LIMIT", note)
            broker.state["orders"][0]["placed_at"] = START.isoformat()
            bars = _bars([(2651, 2649, 2650), (2655.5, 2652, 2655), (2654, 2646, 2647)])
            notes = broker.manage(START + timedelta(minutes=15), 120, False, bars)
            self.assertTrue(any("take profit" in item for item in notes))
            self.assertAlmostEqual(broker.state["balance"], 100 + (2655 - 2647) - 0.30)

    def test_stop_wins_when_one_bar_touches_both(self):
        with TemporaryDirectory() as tmp:
            broker = self._broker(tmp)
            broker.manage(START, 120, False, _bars([(2651, 2649, 2650)]))
            broker.place(_plan())
            broker.state["orders"][0]["placed_at"] = START.isoformat()
            bars = _bars([(2651, 2649, 2650), (2660, 2640, 2650)])
            broker.manage(START + timedelta(minutes=10), 120, False, bars)
            self.assertAlmostEqual(broker.state["balance"], 100 - (2659 - 2655) - 0.30)

    def test_unfilled_limit_expires(self):
        with TemporaryDirectory() as tmp:
            broker = self._broker(tmp)
            broker.manage(START, 120, False, _bars([(2651, 2649, 2650)]))
            broker.place(_plan())
            broker.state["orders"][0]["placed_at"] = START.isoformat()
            notes = broker.manage(START + timedelta(minutes=125), 120, False, _bars([(2651, 2649, 2650)]))
            self.assertTrue(any("time window ended" in item for item in notes))
            self.assertEqual(broker.state["orders"], [])
            self.assertEqual(broker.state["balance"], 100)

    def test_risk_with_spread_never_exceeds_budget(self):
        with TemporaryDirectory() as tmp:
            broker = self._broker(tmp)
            broker.manage(START, 120, False, _bars([(2651, 2649, 2650)]))
            note = broker.place(_plan(entry_low=2654.8, invalidation=2659.8, units=1.0))
            self.assertIn("order not sent", note)


if __name__ == "__main__":
    unittest.main()
