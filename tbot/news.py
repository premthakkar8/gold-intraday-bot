"""Score dollar and oil headlines.

Positive dollar score means the wording supports a stronger dollar, which is
usually a headwind for gold. Positive oil score means the wording supports
higher crude.
"""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USD_BULL = (
    "rate cuts can wait",
    "cuts can wait",
    "no rush to cut",
    "higher for longer",
    "rate hike",
    "rate hikes",
    "hawkish",
    "strong jobs",
    "job growth",
    "payrolls beat",
    "jobs beat",
    "hot cpi",
    "cpi rises",
    "cpi beats",
    "inflation accelerates",
    "inflation hot",
    "sticky inflation",
    "yields rise",
    "yields climb",
    "yields surge",
    "yields jump",
    "dollar surges",
    "dollar rallies",
    "dollar gains",
    "dollar strengthens",
    "dollar firms",
    "dollar index rises",
    "greenback rises",
    "greenback strengthens",
)

USD_BEAR = (
    "emergency rate cut",
    "rate cut",
    "rate cuts",
    "dovish",
    "fed pivot",
    "weak jobs",
    "payrolls miss",
    "jobs miss",
    "unemployment rises",
    "cool cpi",
    "cpi cools",
    "cpi misses",
    "inflation slows",
    "inflation eases",
    "inflation cools",
    "yields fall",
    "yields drop",
    "yields slide",
    "yields ease",
    "dollar falls",
    "dollar slides",
    "dollar weakens",
    "dollar drops",
    "dollar index falls",
    "greenback falls",
    "greenback weakens",
    "recession fears",
)

OIL_BULL = (
    "surprise output cut",
    "output cut",
    "production cut",
    "opec+ cut",
    "opec cut",
    "supply disruption",
    "supply risk",
    "inventory draw",
    "inventories fall",
    "stockpiles fall",
    "crude rises",
    "crude surges",
    "crude jumps",
    "oil surges",
    "oil jumps",
    "oil gains",
    "oil rises",
    "strait of hormuz",
)

OIL_BEAR = (
    "output increase",
    "production increase",
    "opec+ increase",
    "opec increase",
    "inventory build",
    "inventories rise",
    "stockpiles rise",
    "crude falls",
    "crude slips",
    "crude drops",
    "oil falls",
    "oil slips",
    "oil drops",
    "oil slides",
    "demand concern",
    "demand concerns",
    "demand worries",
    "weak demand",
)

SHOCK = (
    "breaking",
    "surprise",
    "unexpected",
    "emergency",
    "fomc",
    "nonfarm",
    "payrolls",
    "cpi",
    "attack",
    "invasion",
    "hormuz",
)

USD_QUERY = (
    "https://news.google.com/rss/search?q=%22US+dollar%22+OR+DXY+OR+"
    "%22dollar+index%22+OR+%22Federal+Reserve%22+OR+%22Treasury+yields%22+"
    "when:1d&hl=en-US&gl=US&ceid=US:en"
)
OIL_QUERY = (
    "https://news.google.com/rss/search?q=%22crude+oil%22+OR+WTI+OR+OPEC+OR+"
    "%22oil+inventory%22+OR+%22oil+inventories%22+when:1d&hl=en-US&gl=US&ceid=US:en"
)


@dataclass
class Headline:
    title: str
    published: datetime | None
    topic: str


@dataclass
class NewsRead:
    usd_score: float
    oil_score: float
    shock: bool
    usd_headlines: list[str]
    oil_headlines: list[str]
    headline_count: int
    warning: str | None = None


def score_title(title: str) -> tuple[float, float, bool]:
    text = _clean(title)
    usd = _polarity(text, USD_BULL, USD_BEAR)
    oil = _polarity(text, OIL_BULL, OIL_BEAR)
    shock = any(_contains(text, phrase) for phrase in SHOCK)
    return usd, oil, shock


def score_news(
    headlines: list[Headline],
    now: datetime | None = None,
    lookback_hours: int = 18,
    warning: str | None = None,
) -> NewsRead:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    usd_parts: list[tuple[float, float, str]] = []
    oil_parts: list[tuple[float, float, str]] = []
    shock = False
    kept = 0
    seen: set[str] = set()

    for item in headlines:
        title = _clean(item.title)
        if not title or title in seen:
            continue
        seen.add(title)
        age_hours = _age_hours(item.published, now)
        if age_hours is not None and age_hours > lookback_hours:
            continue
        kept += 1
        weight = 0.55 if age_hours is None else max(0.35, 1.0 - (age_hours / max(lookback_hours, 1)))
        usd, oil, is_shock = score_title(title)
        if usd != 0:
            usd_parts.append((weight, usd, item.title.strip()))
        if oil != 0:
            oil_parts.append((weight, oil, item.title.strip()))
        if is_shock and (age_hours is None or age_hours <= 6):
            shock = True

    return NewsRead(
        usd_score=_weighted(usd_parts),
        oil_score=_weighted(oil_parts),
        shock=shock,
        usd_headlines=_top_titles(usd_parts, headlines, "usd"),
        oil_headlines=_top_titles(oil_parts, headlines, "oil"),
        headline_count=kept,
        warning=warning,
    )


def fetch_headlines(lookback_hours: int = 18, timeout: int = 12) -> tuple[list[Headline], str | None]:
    warnings: list[str] = []
    headlines: list[Headline] = []
    for topic, url in (("usd", USD_QUERY), ("oil", OIL_QUERY)):
        try:
            headlines.extend(_fetch_rss(url, topic, timeout=timeout))
        except Exception as exc:  # network, parse, or block
            warnings.append(f"{topic} headlines unavailable ({exc.__class__.__name__})")
    warning = "; ".join(warnings) if warnings else None
    return headlines, warning


def _fetch_rss(url: str, topic: str, timeout: int) -> list[Headline]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; tBOT-gold-brief/0.1)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    items: list[Headline] = []
    for node in root.findall(".//item"):
        title = html.unescape((node.findtext("title") or "")).strip()
        if not title:
            continue
        published = _parse_date(node.findtext("pubDate") or "")
        items.append(Headline(title=title, published=published, topic=topic))
    return items


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _polarity(text: str, bull: tuple[str, ...], bear: tuple[str, ...]) -> float:
    tagged = [(phrase, 1) for phrase in bull] + [(phrase, -1) for phrase in bear]
    tagged.sort(key=lambda item: len(item[0]), reverse=True)
    working = text
    scores: list[int] = []
    for phrase, sign in tagged:
        if _contains(working, phrase):
            scores.append(sign)
            working = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", working)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _contains(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def _clean(title: str) -> str:
    return html.unescape(title or "").lower().replace("’", "'").strip()


def _age_hours(published: datetime | None, now: datetime) -> float | None:
    if published is None:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)


def _weighted(parts: list[tuple[float, float, str]]) -> float:
    if not parts:
        return 0.0
    total = sum(weight for weight, _score, _title in parts)
    if total <= 0:
        return 0.0
    return sum(weight * score for weight, score, _title in parts) / total


def _top_titles(
    parts: list[tuple[float, float, str]],
    headlines: list[Headline],
    topic: str,
) -> list[str]:
    if parts:
        ordered = sorted(parts, key=lambda item: item[0] * abs(item[1]), reverse=True)
        return [_trim(title) for _weight, _score, title in ordered[:3]]
    fallback = [_trim(item.title) for item in headlines if item.topic == topic][:2]
    return fallback


def _trim(title: str) -> str:
    title = " ".join(title.split())
    if len(title) > 160:
        return title[:157] + "..."
    return title
