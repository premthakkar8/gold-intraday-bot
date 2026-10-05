"""US economic calendar: high-impact USD releases, refreshed at most once an hour.

Used to stop new orders around CPI, payrolls, FOMC and similar releases, when
gold can jump through a stop.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass
class Event:
    title: str
    when: datetime
    impact: str
    forecast: str
    previous: str


@dataclass
class Calendar:
    events: list[Event]
    fetched_at: datetime | None
    warning: str | None = None

    def blackout(self, now: datetime, before_minutes: int, after_minutes: int) -> Event | None:
        for event in self.events:
            if event.impact != "High":
                continue
            start = event.when - timedelta(minutes=before_minutes)
            end = event.when + timedelta(minutes=after_minutes)
            if start <= now <= end:
                return event
        return None

    def upcoming(self, now: datetime, hours: int = 24) -> list[Event]:
        horizon = now + timedelta(hours=hours)
        return [
            event
            for event in self.events
            if event.impact in ("High", "Medium") and now - timedelta(minutes=30) <= event.when <= horizon
        ]


def load_calendar(cache: Path, refresh_minutes: int, now: datetime | None = None) -> Calendar:
    now = now or datetime.now(timezone.utc)
    cached = _read_cache(cache)
    if cached is not None and cached.fetched_at is not None:
        if now - cached.fetched_at < timedelta(minutes=refresh_minutes):
            return cached
    try:
        raw = _fetch()
    except Exception as exc:
        if cached is not None:
            cached.warning = f"US calendar refresh failed ({exc.__class__.__name__}); using the copy from {cached.fetched_at:%H:%M UTC}."
            return cached
        return Calendar([], None, warning=f"US calendar unavailable ({exc.__class__.__name__}). Event blackout is off.")
    calendar = parse(raw, now)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": now.isoformat(), "raw": raw}), encoding="utf-8")
    return calendar


def parse(raw: list[dict], fetched_at: datetime | None) -> Calendar:
    events: list[Event] = []
    for row in raw:
        if str(row.get("country", "")).upper() != "USD":
            continue
        try:
            when = datetime.fromisoformat(str(row["date"]))
        except (KeyError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        events.append(
            Event(
                title=str(row.get("title", "")).strip(),
                when=when.astimezone(timezone.utc),
                impact=str(row.get("impact", "")).strip(),
                forecast=str(row.get("forecast", "") or ""),
                previous=str(row.get("previous", "") or ""),
            )
        )
    events.sort(key=lambda event: event.when)
    return Calendar(events, fetched_at)


def describe(calendar: Calendar, now: datetime, blackout: Event | None) -> list[str]:
    lines = ["US CALENDAR"]
    if calendar.warning:
        lines.append(calendar.warning)
    if calendar.fetched_at is not None:
        lines.append(f"Refreshed {calendar.fetched_at:%Y-%m-%d %H:%M UTC}. Refreshes hourly.")
    if blackout is not None:
        minutes = (blackout.when - now).total_seconds() / 60
        timing = f"in {minutes:.0f} minutes" if minutes > 0 else f"{-minutes:.0f} minutes ago"
        lines.append(f"EVENT BLACKOUT: {blackout.title} {timing}. No new orders, and pending orders are cancelled.")
    upcoming = calendar.upcoming(now)
    if not upcoming:
        lines.append("No high or medium impact USD releases in the next 24 hours.")
    for event in upcoming:
        detail = []
        if event.forecast:
            detail.append(f"forecast {event.forecast}")
        if event.previous:
            detail.append(f"previous {event.previous}")
        extra = f" ({', '.join(detail)})" if detail else ""
        lines.append(f"- {event.when:%a %H:%M UTC}  {event.impact:<6} {event.title}{extra}")
    return lines


def _fetch(timeout: int = 15) -> list[dict]:
    request = urllib.request.Request(FEED, headers={"User-Agent": "Mozilla/5.0 (compatible; tBOT-gold-brief/0.1)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("unexpected calendar format")
    return data


def _read_cache(cache: Path) -> Calendar | None:
    if not cache.exists():
        return None
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(payload["fetched_at"])
        return parse(payload["raw"], fetched)
    except (ValueError, KeyError, TypeError):
        return None
