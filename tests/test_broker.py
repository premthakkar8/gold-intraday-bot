"""Order entry choice and the US event blackout."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tbot.broker_mt5 import decide_entry
from tbot.econ_calendar import parse

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


class EntryTests(unittest.TestCase):
    def test_short_below_zone_rests_a_limit_at_the_zone_bottom(self):
        self.assertEqual(decide_entry("short", 2650.0, 2650.3, 2655.0, 2658.0, 2659.5), ("limit", 2655.0))

    def test_short_inside_zone_sells_at_market(self):
        self.assertEqual(decide_entry("short", 2656.0, 2656.3, 2655.0, 2658.0, 2659.5), ("market", 2656.0))

    def test_short_past_invalidation_is_refused(self):
        self.assertIsInstance(decide_entry("short", 2660.0, 2660.3, 2655.0, 2658.0, 2659.5), str)

    def test_long_above_zone_rests_a_limit_at_the_zone_top(self):
        self.assertEqual(decide_entry("long", 2659.7, 2660.0, 2644.0, 2647.0, 2642.5), ("limit", 2647.0))

    def test_long_below_invalidation_is_refused(self):
        self.assertIsInstance(decide_entry("long", 2641.7, 2642.0, 2644.0, 2647.0, 2642.5), str)


class CalendarTests(unittest.TestCase):
    def _calendar(self):
        raw = [
            {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-09T08:30:00-04:00", "impact": "High", "forecast": "150K", "previous": "142K"},
            {"title": "ECB Speech", "country": "EUR", "date": "2026-10-09T08:00:00-04:00", "impact": "High"},
            {"title": "Wholesale Inventories", "country": "USD", "date": "2026-10-09T10:00:00-04:00", "impact": "Low"},
        ]
        return parse(raw, NOW)

    def test_only_usd_events_are_kept(self):
        self.assertEqual(len(self._calendar().events), 2)

    def test_payrolls_blackout_window(self):
        calendar = self._calendar()
        release = datetime(2026, 10, 9, 12, 30, tzinfo=timezone.utc)
        self.assertIsNotNone(calendar.blackout(release - timedelta(minutes=20), 30, 15))
        self.assertIsNotNone(calendar.blackout(release + timedelta(minutes=10), 30, 15))
        self.assertIsNone(calendar.blackout(release - timedelta(minutes=45), 30, 15))
        self.assertIsNone(calendar.blackout(release + timedelta(minutes=20), 30, 15))

    def test_low_impact_does_not_block(self):
        calendar = self._calendar()
        wholesale = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
        self.assertIsNone(calendar.blackout(wholesale, 30, 15))


if __name__ == "__main__":
    unittest.main()
