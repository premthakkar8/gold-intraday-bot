"""Text and HTML for one gold session brief."""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from tbot.chart import ChartRead
from tbot.context import MacroRead
from tbot.memory import Observation
from tbot.news import NewsRead
from tbot.risk import HIGH, RiskRules
from tbot.strategy import STYLES, Plan

SESSION_LABEL = {
    "asia": "Asia (00:00-07:00 UTC)",
    "london": "London (07:00-12:00 UTC)",
    "overlap": "London / New York overlap (12:00-17:00 UTC)",
    "newyork": "New York (17:00-21:00 UTC)",
    "late": "late session (21:00-24:00 UTC)",
}

DECISION = {
    "selected": "Chosen because it cleared the bar and led the other approaches.",
    "none_cleared": "No approach cleared the bar, so the plan is to stand aside.",
    "aside_higher": "Standing aside outranked the setups because headline risk was larger.",
    "shock_margin": "Standing aside. The headline shock is larger than the lead of the best trade.",
    "conflict": "Two approaches want opposite trades and neither has a clear lead.",
    "cannot_size": "A setup cleared the bar, but it does not fit the risk limit.",
}


@dataclass
class Brief:
    generated_at: datetime
    gold_symbol: str
    dxy_symbol: str | None
    oil_symbol: str | None
    price_note: str
    chart: ChartRead
    htf_structure: str
    macro: MacroRead
    news: NewsRead
    plan: Plan
    graded: list[str]
    replaced: int
    horizon_minutes: int
    rules: RiskRules
    half_life_days: float
    feed_warnings: list[str]
    held: Observation | None = None
    opened: bool = True


def render(brief: Brief) -> str:
    plan = brief.plan
    chart = brief.chart
    macro = brief.macro
    news = brief.news
    lines: list[str] = [
        "GOLD INTRADAY BRIEF",
        brief.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        f"Gold: {brief.gold_symbol}    Dollar index: {brief.dxy_symbol or 'unavailable'}    WTI: {brief.oil_symbol or 'unavailable'}",
        "This plan is rebuilt on every run. The journal changes which approach is trusted. It does not freeze a strategy.",
    ]
    if brief.price_note:
        lines.append(brief.price_note)
    for warning in brief.feed_warnings:
        lines.append(warning)
    if news.warning:
        lines.append(news.warning)

    if brief.held is not None:
        lines.extend(["", "RUNNING PLAN"])
        lines.extend(_held_text(brief.held, brief.horizon_minutes))

    heading = "PLAN" if brief.opened else "NEW READ (not opened)"
    lines.extend(["", heading, f"{STYLES[plan.style]}  |  {plan.bias.upper()}  |  confidence {plan.confidence:.2f}  |  id {plan.id}"])
    lines.append(DECISION.get(plan.decision, plan.decision))
    lines.append(plan.thesis)
    if plan.bias == "flat":
        lines.append("No position at the current price.")
        signal = _signal_lines(plan)
        if signal:
            lines.extend(signal)
        elif plan.shadow_style and plan.shadow_entry_low is not None:
            lines.append(
                f"The rejected zone was {plan.shadow_entry_low:.2f} to {plan.shadow_entry_high:.2f} "
                f"({STYLES[plan.shadow_style]}). It is not an order."
            )
    else:
        rules = brief.rules
        lines.extend(_signal_lines(plan))
        if plan.tier == HIGH:
            lines.append(
                f"HIGH-CONFIDENCE TIER: confidence {plan.confidence:.0%} is above {rules.high_confidence:.0%}, "
                f"and the journal has backed this approach on similar sessions. "
                f"Risk budget is {rules.high_confidence_risk_percent:g}% of the account ({plan.risk_budget:.2f} USD). "
                "Targets follow structure instead of the 1:4 cap."
            )
        else:
            lines.append(
                f"Normal tier: risk budget {plan.risk_budget:.2f} USD, targets capped at 1:{rules.max_reward_ratio:g}."
            )
        lines.append(
            f"Size {plan.units:g} units ({plan.units / 100:.2f} lot of XAUUSD at 1 unit = 1 oz). "
            f"Loss at invalidation from the worst fill in the zone: {plan.risk_usd:.2f} USD "
            f"({plan.risk_usd / rules.account_usd:.1%} of {rules.account_usd:.0f} USD). "
            f"Stop distance {plan.stop_distance:.2f}. "
            "This is not an order."
        )
        if chart.last_price < plan.entry_low or chart.last_price > plan.entry_high:
            lines.append(
                f"Price is {chart.last_price:.2f}, outside the entry zone. Wait for that zone. This is not a market order."
            )
    if plan.bias != "flat" and plan.sample_weight < 1.5:
        lines.append("There is not enough graded history for this kind of session. Treat the plan as a draft.")

    lines.extend(["", "WHAT WOULD CHANGE IT"])
    lines.extend(f"- {item}" for item in plan.change_if)

    where = "above" if chart.last_price >= chart.vwap else "below"
    lines.extend(
        [
            "",
            "CHART",
            (
                f"Gold {chart.last_price:.2f} in {SESSION_LABEL.get(chart.session, chart.session)}. "
                f"5-minute structure is {chart.structure}. 1-hour structure is {brief.htf_structure}."
            ),
            (
                f"UTC-day range {chart.day_low:.2f} to {chart.day_high:.2f}. "
                f"Price is {chart.range_pos:.0%} of the way up that range, {where} VWAP {chart.vwap:.2f}. "
                f"ATR {chart.atr:.2f}. Short-range volatility over longer-range volatility is {chart.compression:.2f}."
            ),
        ]
    )
    if chart.session == "overlap":
        lines.append("This is the most liquid part of the gold day.")

    lines.extend(["", "DOLLAR", _dollar_text(macro)])
    lines.extend(["", "OIL", _oil_text(macro)])
    lines.extend(["", "HEADLINES", _news_text(news)])

    lines.extend(["", "WHY THIS APPROACH"])
    lines.append(
        f"Adjusted score = how the setup looks now, times the journal. "
        f"A trade needs at least 0.50. Journal half-life is {brief.half_life_days:.0f} days, "
        "so a lesson from a month ago barely counts."
    )
    for row in sorted(plan.scorecard, key=lambda item: item.adjusted, reverse=True):
        mark = ">" if row.style == plan.style else " "
        lines.append(
            f"{mark} {STYLES[row.style]:<32} {row.setup:4.2f} x {row.multiplier:4.2f} = {row.adjusted:4.2f}  {row.bias}"
        )
    shown: set[str] = set()
    for row in plan.scorecard:
        if row.sample_weight > 0 and row.note not in shown:
            lines.append(row.note)
            shown.add(row.note)
    if plan.memory_note not in shown:
        lines.append(plan.memory_note)

    lines.extend(["", "GRADED THIS RUN"])
    if brief.graded:
        lines.extend(brief.graded)
    else:
        lines.append("Nothing finished its stop, target, or time window on this run.")
    if brief.replaced:
        lines.append(
            f"Replaced {brief.replaced} unfinished plan(s) without grading them. "
            "A quick refresh does not teach the journal."
        )

    lines.extend(
        [
            "",
            "The next run reads the chart, the dollar, and oil again from scratch. "
            "Only the graded results carry forward, and they decay.",
            "",
            "This is a research journal for intraday gold, not a broker and not financial advice. "
            "Intraday gold can move through a stop on a headline. You can lose the amount at risk.",
        ]
    )
    return "\n".join(lines)


def _signal_lines(plan: Plan) -> list[str]:
    from tbot.strategy import signal_levels

    levels = signal_levels(plan)
    if levels is None:
        return []
    lines = [
        f"{levels['side']} XAUUSD",
        f"Entry: {levels['entry_low']:.2f} - {levels['entry_high']:.2f}",
        f"SL: {levels['sl']:.2f}",
        f"TP1: {levels['tp']:.2f}",
    ]
    if levels["tp2"] is not None and abs(levels["tp2"] - levels["tp"]) >= 0.5:
        lines.append(f"TP2: {levels['tp2']:.2f}")
    if levels["pending"]:
        lines.append("This is the order only if price trades into the entry. Do not enter at the current price.")
    return lines


def _held_text(obs: Observation, horizon_minutes: int) -> list[str]:
    opened = datetime.fromisoformat(obs.opened_at)
    stamp = opened.strftime("%H:%M UTC")
    if obs.bias == "flat":
        return [
            f"Standing aside since {stamp} (plan {obs.id}). It keeps running because the new read is also no trade."
        ]
    lines = [
        f"{STYLES.get(obs.style, obs.style)} {obs.bias.upper()} from {stamp} (plan {obs.id}) is still running.",
        f"Entry zone {obs.entry_low:.2f} to {obs.entry_high:.2f}. Invalidation {obs.invalidation:.2f}. Target {obs.target:.2f}.",
    ]
    if obs.units is not None and obs.risk_usd is not None:
        tier = "high-confidence tier" if obs.tier == HIGH else "normal tier"
        lines.append(f"Size {obs.units:g} units, {obs.risk_usd:.2f} USD at risk, {tier}.")
    lines.append(
        f"It stays in charge until its invalidation, its target, or {horizon_minutes} minutes from {stamp}. "
        "The read below is shown for information and was not opened."
    )
    return lines


def render_html(text: str, title: str = "Gold intraday brief") -> str:
    body = html.escape(text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f4f0e6; color: #1d1a16; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 32px 20px 72px; }}
    h1 {{ font: 600 22px/1.3 "Segoe UI", sans-serif; margin: 0 0 12px; }}
    pre {{ white-space: pre-wrap; font: 15.5px/1.5 "Segoe UI", sans-serif; margin: 0; }}
  </style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <pre>{body}</pre>
</main>
</body>
</html>
"""


def brief_payload(brief: Brief) -> dict:
    plan = brief.plan
    return {
        "generated_at": brief.generated_at.isoformat(),
        "gold_symbol": brief.gold_symbol,
        "dxy_symbol": brief.dxy_symbol,
        "oil_symbol": brief.oil_symbol,
        "price_note": brief.price_note,
        "feed_warnings": brief.feed_warnings,
        "chart": asdict(brief.chart),
        "htf_structure": brief.htf_structure,
        "macro": asdict(brief.macro),
        "news": asdict(brief.news),
        "graded": brief.graded,
        "replaced": brief.replaced,
        "held": asdict(brief.held) if brief.held is not None else None,
        "plan": {
            "id": plan.id,
            "style": plan.style,
            "label": STYLES[plan.style],
            "bias": plan.bias,
            "confidence": plan.confidence,
            "decision": plan.decision,
            "thesis": plan.thesis,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "invalidation": plan.invalidation,
            "target": plan.target,
            "target_far": plan.target_far,
            "units": plan.units,
            "risk_usd": plan.risk_usd,
            "risk_budget": plan.risk_budget,
            "tier": plan.tier,
            "reward_ratio": plan.reward_ratio,
            "far_ratio": plan.far_ratio,
            "proven": plan.proven,
            "opened": brief.opened,
            "stop_distance": plan.stop_distance,
            "change_if": plan.change_if,
            "memory_note": plan.memory_note,
            "regime": plan.regime,
            "scorecard": [asdict(row) for row in plan.scorecard],
        },
    }


def dump_json(brief: Brief) -> str:
    return json.dumps(brief_payload(brief), indent=2, default=_json_default)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value)!r}")


def _dollar_text(macro: MacroRead) -> str:
    if not macro.dxy_available or macro.dxy_last is None:
        return "Dollar index price did not load. Dollar headlines are still scored. Do not treat this as a confirmed dollar-led session."
    if macro.dxy_overlap >= 12:
        relation = f"Correlation of recent 5-minute returns with gold is {macro.gold_dxy_corr:+.2f}."
    else:
        relation = "Not enough overlapping bars to trust the gold-dollar correlation yet."
    if macro.dxy_state == "flat":
        lead = "The dollar is quiet, so it is not the driver right now."
    elif macro.dxy_state == "up" and macro.gold_dxy_corr < -0.15:
        lead = "Gold is responding the usual way to a firmer dollar."
    elif macro.dxy_state == "down" and macro.gold_dxy_corr < -0.15:
        lead = "Gold is responding the usual way to a softer dollar."
    elif macro.dxy_overlap >= 12 and macro.gold_dxy_corr > 0.1:
        lead = "Gold is not following the dollar. Treat a dollar story as weaker until they agree."
    else:
        lead = "The dollar has a session move. It is allowed to lead if the journal still trusts that approach."
    return (
        f"DXY {macro.dxy_last:.2f} ({macro.dxy_session_pct:+.2f}% this UTC day, {macro.dxy_state}). "
        f"{relation} {lead}"
    )


def _oil_text(macro: MacroRead) -> str:
    if not macro.oil_available or macro.oil_last is None:
        return "WTI price did not load. Oil headlines are still scored."
    if macro.oil_overlap >= 12:
        relation = f"Correlation of recent 5-minute returns with gold is {macro.gold_oil_corr:+.2f}."
    else:
        relation = "Not enough overlapping bars to trust the gold-oil correlation yet."
    if macro.oil_state == "flat":
        lead = "Oil is quiet, so it is context rather than the driver."
    elif macro.oil_state == "up":
        lead = "A rising oil tape can support gold when the move is a supply shock. It does not override a strong dollar by itself."
    else:
        lead = "Falling oil is not automatically a gold short. It matters when gold is actually following it."
    return (
        f"WTI {macro.oil_last:.2f} ({macro.oil_session_pct:+.2f}% this UTC day, {macro.oil_state}). "
        f"{relation} {lead}"
    )


def _news_text(news: NewsRead) -> str:
    lines = [
        f"Dollar-headline score {news.usd_score:+.2f} ({_usd_meaning(news.usd_score)}). "
        f"Oil-headline score {news.oil_score:+.2f} ({_oil_meaning(news.oil_score)}). "
        f"Fresh shock wording: {'yes' if news.shock else 'no'}."
    ]
    if news.headline_count == 0:
        lines.append("No headlines were available. The plan is using price only.")
        return "\n".join(lines)
    if news.usd_headlines:
        lines.append("Dollar headlines:")
        lines.extend(f"- {title}" for title in news.usd_headlines)
    else:
        lines.append("No dollar headline carried a clear bullish or bearish phrase.")
    if news.oil_headlines:
        lines.append("Oil headlines:")
        lines.extend(f"- {title}" for title in news.oil_headlines)
    else:
        lines.append("No oil headline carried a clear supply or demand phrase.")
    return "\n".join(lines)


def _usd_meaning(score: float) -> str:
    if score >= 0.35:
        return "wording supports a stronger dollar, usually a headwind for gold"
    if score <= -0.35:
        return "wording supports a weaker dollar, usually a tailwind for gold"
    return "no clear dollar impulse in the wording"


def _oil_meaning(score: float) -> str:
    if score >= 0.35:
        return "wording supports higher crude"
    if score <= -0.35:
        return "wording supports lower crude"
    return "no clear oil impulse in the wording"
