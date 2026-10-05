"""Account risk rules: $5 normal cap, 1:4 reward cap, high tier only above 90%."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tbot.memory import Memory
from tbot.risk import HIGH, NORMAL, RiskRules, size_plan
from tbot.runner import market_open
from tbot.scenarios import quiet_range
from tbot.strategy import generate

RULES = RiskRules()


def _short(**overrides):
    args = dict(
        bias="short",
        entry_low=2655.0,
        entry_high=2658.0,
        invalidation=2659.0,
        target=2600.0,
        target_far=2590.0,
        atr=2.4,
        confidence=0.70,
        rules=RULES,
    )
    args.update(overrides)
    return size_plan(**args)


class RiskTests(unittest.TestCase):
    def test_normal_trade_never_risks_more_than_five_dollars(self):
        sizing = _short()
        self.assertEqual(sizing.tier, NORMAL)
        self.assertLessEqual(sizing.risk_usd, 5.0 + 1e-9)
        self.assertGreaterEqual(sizing.units, 1)

    def test_normal_target_is_capped_at_one_to_four(self):
        sizing = _short()
        self.assertAlmostEqual(sizing.reward_ratio, 4.0)
        self.assertLessEqual(sizing.far_ratio, 4.0 + 1e-9)

    def test_wide_stop_tightens_the_zone_to_fit_five_dollars(self):
        sizing = _short(entry_low=2640.0, entry_high=2652.0, invalidation=2654.0, target=2620.0, target_far=2610.0)
        self.assertTrue(sizing.tightened)
        self.assertAlmostEqual(sizing.entry_low, 2649.0)
        self.assertLessEqual(sizing.risk_usd, 5.0 + 1e-9)

    def test_stop_too_wide_even_at_the_zone_edge_is_no_trade(self):
        result = _short(entry_low=2640.0, entry_high=2645.0, invalidation=2654.0)
        self.assertIsInstance(result, str)
        self.assertIn("more than the 5.00 USD allowed", result)

    def test_ninety_percent_is_not_enough_for_the_high_tier(self):
        self.assertEqual(_short(confidence=0.90).tier, NORMAL)

    def test_high_tier_risks_fifteen_percent_and_drops_the_cap(self):
        sizing = _short(
            entry_low=2645.0, entry_high=2650.0, invalidation=2659.0, confidence=0.93
        )
        self.assertEqual(sizing.tier, HIGH)
        self.assertAlmostEqual(sizing.risk_budget, 15.0)
        self.assertLessEqual(sizing.risk_usd, 15.0 + 1e-9)
        self.assertGreater(sizing.reward_ratio, 4.0)

    def test_unproven_setup_cannot_reach_the_high_tier(self):
        chart, macro, news, now, htf = quiet_range()
        plan = generate(chart, macro, news, htf_structure=htf, now=now)
        self.assertLessEqual(plan.confidence, 0.88)
        self.assertNotEqual(plan.tier, HIGH)

    def test_a_proven_record_can_unlock_the_high_tier(self):
        chart, macro, news, now, htf = quiet_range()
        probe = generate(chart, macro, news, htf_structure=htf, now=now)
        memory = Memory(half_life_days=5)
        for _ in range(6):
            memory.add_result(regime=probe.regime, style="range_fade", outcome="win", now=now, bias="short")
        plan = generate(chart, macro, news, memory, htf_structure=htf, now=now)
        self.assertEqual(plan.style, "range_fade")
        self.assertTrue(plan.proven)
        self.assertGreater(plan.confidence, 0.90)
        self.assertEqual(plan.tier, HIGH)
        self.assertLessEqual(plan.risk_usd, 15.0 + 1e-9)

    def test_waits_out_the_daily_pause_but_not_the_weekend(self):
        from tbot.runner import seconds_until_open

        self.assertEqual(seconds_until_open(datetime(2026, 10, 5, 21, 10, tzinfo=timezone.utc)), 50 * 60)
        self.assertEqual(seconds_until_open(datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)), 0)
        self.assertIsNone(seconds_until_open(datetime(2026, 10, 9, 21, 10, tzinfo=timezone.utc)))

    def test_market_hours(self):
        self.assertFalse(market_open(datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)))  # Saturday
        self.assertFalse(market_open(datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)))  # Sunday before open
        self.assertTrue(market_open(datetime(2026, 10, 4, 22, 30, tzinfo=timezone.utc)))  # Sunday open
        self.assertFalse(market_open(datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)))  # daily pause
        self.assertTrue(market_open(datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)))
        self.assertFalse(market_open(datetime(2026, 10, 9, 21, 5, tzinfo=timezone.utc)))  # Friday close


if __name__ == "__main__":
    unittest.main()
