"""Journal trust and grading."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from tbot.memory import Memory, Observation


NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
REGIME = {
    "dxy": "up",
    "oil": "flat",
    "structure": "sideways",
    "news": "usd",
    "session": "overlap",
}


def _bars(rows):
    return pd.DataFrame(rows)


def _obs(**overrides) -> Observation:
    opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
    base = dict(
        id="a1",
        opened_at=opened.isoformat(),
        resolved_at=None,
        horizon_minutes=120,
        regime=dict(REGIME),
        style="dollar_lead",
        bias="short",
        entry_low=99.0,
        entry_high=101.0,
        invalidation=102.0,
        target=97.0,
        price_at_plan=100.0,
        atr=2.0,
        outcome=None,
        r_multiple=None,
    )
    base.update(overrides)
    return Observation(**base)


class MemoryTests(unittest.TestCase):
    def test_four_losses_cut_trust_to_the_floor(self):
        memory = Memory(half_life_days=5)
        for _ in range(4):
            memory.add_result(regime=REGIME, style="range_fade", outcome="loss", now=NOW)
        record = memory.record_for("range_fade", REGIME, NOW)
        self.assertAlmostEqual(record.multiplier, 0.45)
        self.assertEqual(record.losses, 4)

    def test_replaced_plans_do_not_teach(self):
        memory = Memory()
        memory.observations.append(_obs(outcome="replaced", resolved_at=NOW.isoformat()))
        record = memory.record_for("dollar_lead", REGIME, NOW)
        self.assertEqual(record.sample_weight, 0.0)
        self.assertEqual(record.multiplier, 1.0)

    def test_invalidation_is_a_loss_even_if_the_bar_also_hits_the_target(self):
        memory = Memory()
        memory.observations.append(_obs())
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 103,
                    "low": 90,
                    "close": 101,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, NOW)
        self.assertEqual(memory.observations[0].outcome, "loss")
        self.assertEqual(memory.observations[0].r_multiple, -1.0)

    def test_target_is_a_win(self):
        memory = Memory()
        memory.observations.append(_obs())
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 100.4,
                    "low": 96,
                    "close": 97,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, NOW)
        self.assertEqual(memory.observations[0].outcome, "win")
        self.assertAlmostEqual(memory.observations[0].r_multiple, 1.5)

    def test_open_plan_is_not_graded_early(self):
        memory = Memory()
        memory.observations.append(_obs())
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, opened + timedelta(minutes=30))
        self.assertIsNone(memory.observations[0].outcome)

    def test_standing_aside_through_a_clean_trend_is_a_loss(self):
        memory = Memory()
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        memory.observations.append(
            _obs(
                style="stand_aside",
                bias="flat",
                entry_low=None,
                entry_high=None,
                invalidation=None,
                target=None,
                price_at_plan=100.0,
                atr=2.0,
            )
        )
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 103.2,
                    "low": 99.9,
                    "close": 103,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, opened + timedelta(minutes=30))
        self.assertEqual(memory.observations[0].outcome, "loss")

    def test_standing_aside_in_quiet_tape_waits_for_the_horizon(self):
        memory = Memory()
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        memory.observations.append(
            _obs(style="stand_aside", bias="flat", entry_low=None, entry_high=None, invalidation=None, target=None)
        )
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, opened + timedelta(minutes=30))
        self.assertIsNone(memory.observations[0].outcome)
        memory.resolve(bars, opened + timedelta(minutes=130))
        self.assertEqual(memory.observations[0].outcome, "win")

    def test_price_through_the_target_is_not_a_win_if_the_entry_was_never_traded(self):
        memory = Memory()
        opened = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
        memory.observations.append(
            _obs(entry_low=110.0, entry_high=112.0, invalidation=114.0, target=105.0, price_at_plan=100.0)
        )
        bars = _bars(
            [
                {
                    "ts": opened + timedelta(minutes=5),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1,
                }
            ]
        )
        memory.resolve(bars, opened + timedelta(minutes=130))
        self.assertEqual(memory.observations[0].outcome, "scratch")
        self.assertEqual(memory.observations[0].r_multiple, 0.0)


if __name__ == "__main__":
    unittest.main()
