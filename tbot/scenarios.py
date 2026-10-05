"""Fixed sessions used by the demo and the tests.

These are not live prices. They exist so a quiet range and a dollar-led
session can be compared before and after the journal has results.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tbot.chart import ChartRead
from tbot.context import MacroRead
from tbot.news import NewsRead

NOW = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)


def _chart(**overrides) -> ChartRead:
    base = dict(
        last_price=2656.80,
        last_ts=NOW,
        atr=2.40,
        vwap=2650.00,
        day_high=2658.00,
        day_low=2644.00,
        range_pos=0.91,
        structure="sideways",
        compression=1.0,
        impulse_atr=0.40,
        session="overlap",
        last_swing_high=2658.00,
        last_swing_low=2644.20,
        bar_count=150,
    )
    base.update(overrides)
    return ChartRead(**base)


def _macro(**overrides) -> MacroRead:
    base = dict(
        dxy_last=101.04,
        dxy_session_pct=0.04,
        dxy_state="flat",
        dxy_available=True,
        oil_last=72.05,
        oil_session_pct=0.07,
        oil_state="flat",
        oil_available=True,
        gold_dxy_corr=-0.05,
        gold_oil_corr=0.04,
        dxy_overlap=30,
        oil_overlap=30,
    )
    base.update(overrides)
    return MacroRead(**base)


def quiet_range():
    """Gold near the high of a range. Dollar and oil are quiet."""
    news = NewsRead(
        usd_score=0.0,
        oil_score=0.0,
        shock=False,
        usd_headlines=["Traders wait on the next data point"],
        oil_headlines=["US oil prices little changed ahead of the stockpile figures"],
        headline_count=2,
    )
    return _chart(), _macro(), news, NOW, "sideways"


def dollar_bid():
    """Same range, but the dollar is bid and the wording is hawkish."""
    news = NewsRead(
        usd_score=0.70,
        oil_score=0.0,
        shock=False,
        usd_headlines=["Fed official says rate cuts can wait as jobs stay strong"],
        oil_headlines=["US oil prices little changed ahead of the stockpile figures"],
        headline_count=2,
    )
    macro = _macro(
        dxy_last=101.40,
        dxy_session_pct=0.30,
        dxy_state="up",
        gold_dxy_corr=-0.60,
    )
    return _chart(), macro, news, NOW, "sideways"


def oil_shock():
    """A supply headline in oil, with gold not at a range extreme."""
    news = NewsRead(
        usd_score=0.0,
        oil_score=0.80,
        shock=True,
        usd_headlines=[],
        oil_headlines=["OPEC agrees surprise output cut"],
        headline_count=1,
    )
    chart = _chart(last_price=2651.0, range_pos=0.50, impulse_atr=0.10, vwap=2651.0)
    macro = _macro(oil_last=74.20, oil_session_pct=1.50, oil_state="up", gold_oil_corr=0.20)
    return chart, macro, news, NOW, "sideways"


def trend_versus_dollar():
    """Intraday trend up while the dollar is also up."""
    news = NewsRead(
        usd_score=0.20,
        oil_score=0.0,
        shock=False,
        usd_headlines=[],
        oil_headlines=[],
        headline_count=0,
    )
    chart = _chart(
        last_price=2662.0,
        day_high=2664.0,
        day_low=2648.0,
        range_pos=0.70,
        structure="up",
        impulse_atr=1.2,
        vwap=2656.0,
        last_swing_high=2664.0,
        last_swing_low=2652.0,
    )
    macro = _macro(dxy_last=101.35, dxy_session_pct=0.30, dxy_state="up", gold_dxy_corr=-0.20)
    return chart, macro, news, NOW, "up"
