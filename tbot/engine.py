"""One pass: grade the open plan, read the market, write the next plan."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from tbot.chart import read_chart, to_hourly
from tbot.config import Settings
from tbot.context import read_macro
from tbot.memory import Memory
from tbot.news import Headline, score_news
from tbot.report import Brief
from tbot.strategy import generate


def analyze(
    gold: pd.DataFrame,
    dxy: pd.DataFrame | None,
    oil: pd.DataFrame | None,
    headlines: list[Headline],
    memory: Memory,
    settings: Settings,
    *,
    now: datetime | None = None,
    gold_symbol: str = "XAUUSD=X",
    dxy_symbol: str | None = "DX-Y.NYB",
    oil_symbol: str | None = "CL=F",
    price_note: str = "",
    news_warning: str | None = None,
    feed_warnings: list[str] | None = None,
    keep_running_plan: bool = False,
) -> Brief:
    """Grade, read, and plan.

    With keep_running_plan, an unfinished trade plan stays in charge until its stop,
    target, or time window, so scheduled runs do not erase it before it can be graded.
    An open stand-aside plan only gives way to a new trade plan.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    graded = memory.resolve(gold, now)
    chart = read_chart(gold, now)
    try:
        htf_structure = read_chart(to_hourly(gold), now).structure
    except ValueError:
        htf_structure = "unknown"
    macro = read_macro(gold, dxy, oil)
    news = score_news(headlines, now, settings.news_lookback_hours, warning=news_warning)
    plan = generate(
        chart,
        macro,
        news,
        memory,
        htf_structure=htf_structure,
        rules=settings.risk,
        horizon_minutes=settings.horizon_minutes,
        now=now,
    )

    held = None
    replaced = 0
    opened = True
    running = memory.open_plan_now()
    if keep_running_plan and running is not None:
        if running.bias != "flat":
            held = running
            opened = False
        elif plan.bias == "flat":
            held = running
            opened = False
    if opened:
        replaced = memory.replace_open(now)
        memory.open_plan(plan, settings.horizon_minutes, now)
    memory.save()
    return Brief(
        generated_at=now,
        gold_symbol=gold_symbol,
        dxy_symbol=dxy_symbol,
        oil_symbol=oil_symbol,
        price_note=price_note,
        chart=chart,
        htf_structure=htf_structure,
        macro=macro,
        news=news,
        plan=plan,
        graded=graded,
        replaced=replaced,
        horizon_minutes=settings.horizon_minutes,
        rules=settings.risk,
        half_life_days=settings.half_life_days,
        feed_warnings=list(feed_warnings or []),
        held=held,
        opened=opened,
    )
