"""A full pass grades, replaces, and stores one open plan."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tbot.config import Settings
from tbot.engine import analyze
from tbot.memory import Memory


def _flat(symbol_price: float, end: datetime, count: int = 40) -> pd.DataFrame:
    start = end - timedelta(minutes=5 * (count - 1))
    rows = []
    for index in range(count):
        rows.append(
            {
                "ts": start + timedelta(minutes=5 * index),
                "open": symbol_price,
                "high": symbol_price,
                "low": symbol_price,
                "close": symbol_price,
                "volume": 1,
            }
        )
    return pd.DataFrame(rows)


class EngineTests(unittest.TestCase):
    def test_a_second_run_replaces_the_open_plan_without_grading_it(self):
        end = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            settings = Settings(
                memory_path=Path(tmp) / "memory.json",
                report_dir=Path(tmp),
                half_life_days=5,
            )
            memory = Memory(path=settings.memory_path, half_life_days=5)
            gold = _flat(2650, end)
            dxy = _flat(101, end)
            oil = _flat(72, end)
            first = analyze(gold, dxy, oil, [], memory, settings, now=end, gold_symbol="XAUUSD=X")
            self.assertEqual(first.plan.style, "stand_aside")
            analyze(gold, dxy, oil, [], memory, settings, now=end + timedelta(minutes=5), gold_symbol="XAUUSD=X")
            outcomes = [item.outcome for item in memory.observations]
            self.assertIn("replaced", outcomes)
            self.assertEqual(sum(item.outcome is None for item in memory.observations), 1)
            self.assertTrue(settings.memory_path.exists())

    def test_a_scheduled_run_keeps_a_running_trade_plan(self):
        end = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            settings = Settings(memory_path=Path(tmp) / "memory.json", report_dir=Path(tmp))
            memory = Memory(path=settings.memory_path, half_life_days=5)
            memory.add_result(regime={}, style="range_fade", outcome="win", now=end)
            memory.observations[-1].outcome = None
            memory.observations[-1].resolved_at = None
            memory.observations[-1].opened_at = end.isoformat()
            memory.observations[-1].entry_low = 3000.0
            memory.observations[-1].entry_high = 3001.0
            memory.observations[-1].invalidation = 3002.0
            memory.observations[-1].target = 2990.0
            running_id = memory.observations[-1].id
            gold = _flat(2650, end + timedelta(minutes=10))
            brief = analyze(
                gold, _flat(101, end), _flat(72, end), [], memory, settings,
                now=end + timedelta(minutes=10), keep_running_plan=True,
            )
            self.assertFalse(brief.opened)
            self.assertEqual(brief.held.id, running_id)
            self.assertEqual(sum(item.outcome is None for item in memory.observations), 1)


if __name__ == "__main__":
    unittest.main()
