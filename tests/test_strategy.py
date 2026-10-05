"""The plan has to be able to change."""

from __future__ import annotations

import unittest
from dataclasses import replace

from tbot.memory import Memory
from tbot.scenarios import dollar_bid, oil_shock, quiet_range, trend_versus_dollar
from tbot.strategy import generate


def _plan(scenario, memory=None):
    chart, macro, news, now, htf = scenario
    return generate(chart, macro, news, memory, htf_structure=htf, now=now)


def _losses(plan, style: str, now, count: int = 4) -> Memory:
    memory = Memory(half_life_days=5)
    for _ in range(count):
        memory.add_result(regime=plan.regime, style=style, outcome="loss", now=now, bias=plan.bias)
    return memory


class StrategyTests(unittest.TestCase):
    def test_quiet_range_fades_until_the_journal_rejects_it(self):
        chart, macro, news, now, htf = quiet_range()
        first = generate(chart, macro, news, htf_structure=htf, now=now)
        self.assertEqual(first.style, "range_fade")
        self.assertEqual(first.bias, "short")
        second = generate(chart, macro, news, _losses(first, "range_fade", now), htf_structure=htf, now=now)
        self.assertEqual(second.style, "stand_aside")
        self.assertEqual(second.bias, "flat")
        self.assertLess(second.confidence, first.confidence)

    def test_dollar_plan_gives_way_after_losses(self):
        chart, macro, news, now, htf = dollar_bid()
        first = generate(chart, macro, news, htf_structure=htf, now=now)
        self.assertEqual(first.style, "dollar_lead")
        self.assertEqual(first.bias, "short")
        second = generate(chart, macro, news, _losses(first, "dollar_lead", now), htf_structure=htf, now=now)
        self.assertEqual(second.style, "range_fade")
        self.assertEqual(second.bias, "short")
        self.assertNotEqual(first.style, second.style)
        self.assertNotIn("neither is driving", second.thesis)
        self.assertIn("did not lead", second.thesis)

    def test_does_not_sell_the_low_just_because_the_dollar_is_bid(self):
        chart, macro, news, now, htf = dollar_bid()
        chart = replace(chart, range_pos=0.15, last_price=2646.0)
        plan = generate(chart, macro, news, htf_structure=htf, now=now)
        self.assertEqual(plan.bias, "flat")
        self.assertIn("chasing", plan.thesis)

    def test_oil_shock_can_lead_when_oil_is_the_story(self):
        plan = _plan(oil_shock())
        self.assertEqual(plan.style, "oil_shock")
        self.assertEqual(plan.bias, "long")

    def test_trend_and_dollar_disagreement_stands_aside(self):
        plan = _plan(trend_versus_dollar())
        self.assertEqual(plan.style, "stand_aside")
        self.assertEqual(plan.bias, "flat")
        self.assertEqual(plan.decision, "conflict")

    def test_four_wins_raise_trust(self):
        chart, macro, news, now, htf = quiet_range()
        memory = Memory(half_life_days=5)
        probe = generate(chart, macro, news, htf_structure=htf, now=now)
        for _ in range(4):
            memory.add_result(regime=probe.regime, style="range_fade", outcome="win", now=now, bias="short")
        plan = generate(chart, macro, news, memory, htf_structure=htf, now=now)
        fade = next(row for row in plan.scorecard if row.style == "range_fade")
        self.assertAlmostEqual(fade.multiplier, 1.55)
        self.assertGreater(fade.adjusted, fade.setup)


if __name__ == "__main__":
    unittest.main()
