"""Alert text is a complete manual order, and it says no order was sent."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from tbot.runner import alert_text
from tbot.strategy import Plan


def _plan(**overrides) -> Plan:
    base = dict(
        id="a1",
        style="dollar_lead",
        bias="short",
        confidence=0.62,
        thesis="",
        decision="selected",
        entry_low=4189.2,
        entry_high=4198.9,
        invalidation=4200.6,
        target=4175.7,
        target_far=4150.4,
        units=1.0,
        risk_usd=4.80,
        stop_distance=11.4,
        risk_budget=5.0,
        tier="normal",
        reward_ratio=4.0,
        far_ratio=4.0,
        proven=False,
        change_if=[],
        scorecard=[],
        memory_note="",
        regime={},
        price=4165.0,
        atr=3.1,
        sample_weight=0.0,
    )
    base.update(overrides)
    return Plan(**base)


def _brief(plan: Plan):
    chart = SimpleNamespace(last_price=4165.7)
    return SimpleNamespace(plan=plan, chart=chart)


class AlertTests(unittest.TestCase):
    def test_trade_alert_has_the_order_and_says_to_place_it_yourself(self):
        text = alert_text(_brief(_plan()))
        self.assertIn("SELL XAUUSD", text)
        self.assertIn("Entry: 4189.20 - 4198.90", text)
        self.assertIn("SL: 4200.60", text)
        self.assertIn("TP1: 4175.70", text)
        self.assertIn("TP2: 4150.40", text)
        self.assertIn("No order was sent", text)

    def test_stand_aside_is_not_a_trade_alert(self):
        text = alert_text(_brief(_plan(bias="flat", style="stand_aside", entry_low=None, decision="none_cleared")))
        self.assertIn("No entry", text)
        self.assertNotIn("SELL XAUUSD", text)

    def test_blackout_is_named_on_the_alert(self):
        event = SimpleNamespace(title="CPI", when=datetime(2026, 10, 6, 12, 30, tzinfo=timezone.utc))
        text = alert_text(_brief(_plan()), event)
        self.assertIn("CPI", text)
        self.assertIn("12:30 UTC", text)


if __name__ == "__main__":
    unittest.main()
