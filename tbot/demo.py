"""Show the journal changing a plan on two fixed sessions."""

from __future__ import annotations

from tbot.config import Settings, load_settings
from tbot.memory import Memory
from tbot.plot import save_chart, schematic_bars
from tbot.report import render_html
from tbot.scenarios import dollar_bid, quiet_range
from tbot.strategy import STYLES, generate


def run_demo(settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    blocks = [
        "GOLD INTRADAY - HOW THE PLAN CHANGES",
        "",
        "This is not a live price. It is two fixed sessions so you can see the journal rewrite the plan.",
        "Live gold, dollar index, WTI, and headlines: python main.py brief",
        "",
        "Each run reads the chart, the dollar, and oil again. Nothing is a permanent entry rule.",
        "The journal is the only thing that carries forward. Adjusted score = setup x trust.",
        "A trade needs an adjusted score of at least 0.50. Four recent losses cut trust to 0.45,",
        "which is enough to retire that approach until it starts working again.",
        "Lessons lose half their pull after 5 days, so an old win cannot keep the plan frozen.",
        "",
        "Oil is scored every time. It leads only when oil itself is the shock.",
        "The dollar is allowed to lead when the dollar is actually moving.",
        "A quiet dollar does not invent a dollar trade.",
        "If those dollar trades start losing, the journal hands the session back to the chart.",
        "",
        _case(
            "A. Quiet dollar, gold near the high of a range",
            quiet_range(),
            "range_fade",
            settings,
            "demo-quiet.png",
            "After the fade loses, the same chart is not a trade.",
        ),
        "",
        _case(
            "B. Dollar is bid, and the headlines lean hawkish",
            dollar_bid(),
            "dollar_lead",
            settings,
            "demo-dollar.png",
            "After dollar-led trades lose, the same tape goes back to the range plan.",
        ),
        "",
        "Charts, when matplotlib is installed, are sketches of these examples. They are not live prices.",
        "The live chart is written to reports/latest.png by: python main.py brief",
        "",
        "This is a research journal, not a broker and not financial advice. You can lose the amount at risk.",
    ]
    text = "\n".join(blocks)
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    (settings.report_dir / "demo.txt").write_text(text, encoding="utf-8")
    (settings.report_dir / "demo.html").write_text(
        render_html(text, "Gold intraday - how the plan changes"),
        encoding="utf-8",
    )
    return text


def _case(title, scenario, losing_style: str, settings: Settings, image_name: str, change: str) -> str:
    chart, macro, news, now, htf = scenario
    before = generate(
        chart,
        macro,
        news,
        Memory(half_life_days=settings.half_life_days),
        htf_structure=htf,
        rules=settings.risk,
        horizon_minutes=settings.horizon_minutes,
        now=now,
    )
    journal = Memory(half_life_days=settings.half_life_days)
    for _ in range(4):
        journal.add_result(regime=before.regime, style=losing_style, outcome="loss", now=now, bias=before.bias)
    after = generate(
        chart,
        macro,
        news,
        journal,
        htf_structure=htf,
        rules=settings.risk,
        horizon_minutes=settings.horizon_minutes,
        now=now,
    )
    image = save_chart(
        settings.report_dir / image_name,
        schematic_bars(chart),
        chart,
        after,
        heading=title,
        sketch=True,
    )
    lines = [
        title,
        f"Gold {chart.last_price:.2f}  |  5-minute {chart.structure}  |  range position {chart.range_pos:.0%}",
        f"DXY {macro.dxy_state} {macro.dxy_session_pct:+.2f}%  |  WTI {macro.oil_state} {macro.oil_session_pct:+.2f}%",
        f"Dollar headlines {news.usd_score:+.2f}  |  oil headlines {news.oil_score:+.2f}",
        "",
        "Before any journal",
        _scorecard(before),
        before.thesis,
        "",
        f"After 4 losses on {STYLES[losing_style]} in this kind of session",
        _scorecard(after),
        after.thesis,
        "",
        f"What changed: {change}",
        f"Before: {STYLES[before.style]} / {before.bias}. After: {STYLES[after.style]} / {after.bias}.",
    ]
    if image is not None:
        lines.append(f"Sketch: {image}")
    return "\n".join(lines)


def _scorecard(plan) -> str:
    rows = sorted(plan.scorecard, key=lambda item: item.adjusted, reverse=True)
    lines = []
    for row in rows:
        mark = ">" if row.style == plan.style else " "
        lines.append(
            f"{mark} {STYLES[row.style]:<32} {row.setup:4.2f} x {row.multiplier:4.2f} = {row.adjusted:4.2f}  {row.bias}"
        )
    if plan.sample_weight > 0:
        lines.append(plan.memory_note)
    return "\n".join(lines)
