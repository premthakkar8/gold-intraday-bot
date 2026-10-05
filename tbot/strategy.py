"""Build one session plan from the current read.

Nothing here is a standing model. Each call scores a few approaches against
the chart in front of it, then multiplies by how those approaches have done
on similar days. A bad run of results is enough to retire an approach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from tbot.chart import ChartRead
from tbot.context import MacroRead
from tbot.memory import Memory
from tbot.news import NewsRead
from tbot.risk import HIGH, RiskRules, size_plan

MIN_SCORE = 0.50
CONFLICT_GAP = 0.12

# Setup scores are judgement, not probabilities. Above this cap the journal has to
# have backed the approach on similar sessions before confidence can go higher.
UNPROVEN_CAP = 0.88
PROVEN_CAP = 0.97
PROVEN_SAMPLE = 5.0
PROVEN_WINS = 4
PROVEN_MULTIPLIER = 1.30
PROVEN_SETUP = 0.75

STYLES = {
    "dollar_lead": "Trade with the dollar",
    "oil_shock": "Trade the oil shock",
    "trend_align": "Trade with the intraday trend",
    "range_fade": "Fade the session range",
    "stand_aside": "Stand aside",
}


@dataclass
class ScoreLine:
    style: str
    setup: float
    multiplier: float
    adjusted: float
    bias: str
    sample_weight: float
    note: str


@dataclass
class Plan:
    id: str
    style: str
    bias: str
    confidence: float
    thesis: str
    decision: str
    entry_low: float | None
    entry_high: float | None
    invalidation: float | None
    target: float | None
    target_far: float | None
    units: float | None
    risk_usd: float
    stop_distance: float | None
    risk_budget: float
    tier: str
    reward_ratio: float | None
    far_ratio: float | None
    proven: bool
    change_if: list[str]
    scorecard: list[ScoreLine]
    memory_note: str
    regime: dict
    price: float
    atr: float
    sample_weight: float
    shadow_style: str | None = None
    shadow_entry_low: float | None = None
    shadow_entry_high: float | None = None
    shadow_invalidation: float | None = None
    shadow_target: float | None = None
    shadow_target_far: float | None = None


@dataclass
class _Candidate:
    style: str
    bias: str
    setup: float
    multiplier: float = 1.0
    adjusted: float = 0.0
    sample_weight: float = 0.0
    wins: int = 0
    note: str = ""
    entry_low: float | None = None
    entry_high: float | None = None
    invalidation: float | None = None
    target: float | None = None
    target_far: float | None = None


def news_tag(news: NewsRead) -> str:
    if abs(news.usd_score) < 0.2 and abs(news.oil_score) < 0.2:
        return "quiet"
    if abs(news.usd_score) >= 0.35 and abs(news.oil_score) >= 0.35:
        return "mixed"
    if abs(news.usd_score) >= abs(news.oil_score):
        return "usd"
    return "oil"


def make_regime(chart: ChartRead, macro: MacroRead, news: NewsRead) -> dict:
    return {
        "dxy": macro.dxy_state,
        "oil": macro.oil_state,
        "structure": chart.structure,
        "news": news_tag(news),
        "session": chart.session,
    }


def generate(
    chart: ChartRead,
    macro: MacroRead,
    news: NewsRead,
    memory: Memory | None = None,
    *,
    htf_structure: str = "sideways",
    rules: RiskRules | None = None,
    horizon_minutes: int = 120,
    now: datetime | None = None,
) -> Plan:
    memory = memory or Memory()
    rules = rules or RiskRules()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    regime = make_regime(chart, macro, news)

    specs = (
        ("dollar_lead", _score_dollar(chart, macro, news)),
        ("oil_shock", _score_oil(chart, macro, news)),
        ("trend_align", _score_trend(chart, macro, news, htf_structure)),
        ("range_fade", _score_fade(chart, macro, news, htf_structure)),
        ("stand_aside", (_score_aside(chart, macro, news, htf_structure), "flat")),
    )
    candidates: list[_Candidate] = []
    for style, (setup, bias) in specs:
        record = memory.record_for(style, regime, now)
        candidate = _Candidate(
            style=style,
            bias=bias,
            setup=setup,
            multiplier=record.multiplier,
            adjusted=setup * record.multiplier,
            sample_weight=record.sample_weight,
            wins=record.wins,
            note=record.note,
        )
        if bias != "flat":
            (
                candidate.entry_low,
                candidate.entry_high,
                candidate.invalidation,
                candidate.target,
                candidate.target_far,
            ) = _levels(bias, chart)
        candidates.append(candidate)

    aside = next(item for item in candidates if item.style == "stand_aside")
    directional = [item for item in candidates if item.style != "stand_aside"]
    contenders = [item for item in directional if item.bias != "flat" and item.adjusted >= MIN_SCORE]
    contenders.sort(key=lambda item: item.adjusted, reverse=True)
    biased = [item for item in directional if item.bias != "flat"]
    biased.sort(key=lambda item: item.adjusted, reverse=True)
    rejected = biased[0] if biased else None
    second = biased[1] if len(biased) > 1 else None

    if not contenders:
        decision = "none_cleared"
        chosen = aside
    elif news.shock and contenders[0].adjusted < aside.adjusted + 0.06:
        decision = "shock_margin"
        chosen = aside
        rejected = contenders[0]
    elif aside.adjusted > contenders[0].adjusted:
        decision = "aside_higher"
        chosen = aside
    elif (
        len(contenders) > 1
        and contenders[0].bias != contenders[1].bias
        and contenders[0].adjusted - contenders[1].adjusted < CONFLICT_GAP
    ):
        decision = "conflict"
        chosen = aside
        rejected = contenders[0]
        second = contenders[1]
    else:
        decision = "selected"
        chosen = contenders[0]

    units = None
    stop_distance = None
    risk_usd = 0.0
    risk_budget = rules.budget("normal")
    tier = "normal"
    reward_ratio = far_ratio = None
    proven = False
    entry_low = entry_high = invalidation = target = target_far = None
    shadow_style = shadow_low = shadow_high = shadow_stop = shadow_target = shadow_far = None
    sizing_problem = None

    if decision == "selected":
        proven = _is_proven(chosen)
        confidence = min(PROVEN_CAP if proven else UNPROVEN_CAP, max(0.35, chosen.adjusted))
        if chosen.sample_weight < 1.5:
            confidence = min(confidence, 0.58)
        sizing = size_plan(
            bias=chosen.bias,
            entry_low=chosen.entry_low,
            entry_high=chosen.entry_high,
            invalidation=chosen.invalidation,
            target=chosen.target,
            target_far=chosen.target_far,
            atr=chart.atr,
            confidence=confidence,
            rules=rules,
        )
        if isinstance(sizing, str):
            sizing_problem = sizing
            decision = "cannot_size"
            rejected = chosen
            chosen = aside
        else:
            entry_low = sizing.entry_low
            entry_high = sizing.entry_high
            invalidation = chosen.invalidation
            target = sizing.target
            target_far = sizing.target_far
            units = sizing.units
            stop_distance = sizing.stop_distance
            risk_usd = sizing.risk_usd
            risk_budget = sizing.risk_budget
            tier = sizing.tier
            reward_ratio = sizing.reward_ratio
            far_ratio = sizing.far_ratio
            chosen.entry_low, chosen.entry_high = entry_low, entry_high
            chosen.target, chosen.target_far = target, target_far
            thesis = _thesis_selected(chosen, chart, macro, news, htf_structure)
            if sizing.tightened:
                thesis += (
                    " The entry zone was tightened toward the invalidation so the smallest size fits the risk limit."
                )
            memory_note = chosen.note
            sample_weight = chosen.sample_weight

    if decision != "selected":
        thesis = _thesis_flat(decision, chart, macro, news, rejected, second, aside, htf_structure, sizing_problem)
        memory_note = rejected.note if rejected is not None else aside.note
        sample_weight = rejected.sample_weight if rejected is not None else aside.sample_weight
        confidence = min(0.80, max(0.30, aside.adjusted))
        if rejected is not None and rejected.bias != "flat" and rejected.entry_low is not None:
            shadow_style = rejected.style
            shadow_low = rejected.entry_low
            shadow_high = rejected.entry_high
            shadow_stop = rejected.invalidation
            shadow_target = rejected.target
            shadow_far = rejected.target_far
            pending = size_plan(
                bias=rejected.bias,
                entry_low=rejected.entry_low,
                entry_high=rejected.entry_high,
                invalidation=rejected.invalidation,
                target=rejected.target,
                target_far=rejected.target_far,
                atr=chart.atr,
                confidence=min(0.8, max(0.35, rejected.adjusted)),
                rules=rules,
            )
            if not isinstance(pending, str):
                shadow_low = pending.entry_low
                shadow_high = pending.entry_high
                shadow_target = pending.target
                shadow_far = pending.target_far

    return Plan(
        id=uuid.uuid4().hex[:8],
        style=chosen.style,
        bias=chosen.bias if decision == "selected" else "flat",
        confidence=confidence,
        thesis=thesis,
        decision=decision,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=invalidation,
        target=target,
        target_far=target_far,
        units=units,
        risk_usd=risk_usd,
        stop_distance=stop_distance,
        risk_budget=risk_budget,
        tier=tier,
        reward_ratio=reward_ratio,
        far_ratio=far_ratio,
        proven=proven,
        change_if=_change_if(chosen.style if decision == "selected" else "stand_aside", decision, macro, news, invalidation, horizon_minutes),
        scorecard=[
            ScoreLine(
                style=item.style,
                setup=item.setup,
                multiplier=item.multiplier,
                adjusted=item.adjusted,
                bias=item.bias,
                sample_weight=item.sample_weight,
                note=item.note,
            )
            for item in candidates
        ],
        memory_note=memory_note,
        regime=regime,
        price=chart.last_price,
        atr=chart.atr,
        sample_weight=sample_weight,
        shadow_style=shadow_style,
        shadow_entry_low=shadow_low,
        shadow_entry_high=shadow_high,
        shadow_invalidation=shadow_stop,
        shadow_target=shadow_target,
        shadow_target_far=shadow_far,
    )


def _is_proven(candidate: _Candidate) -> bool:
    return (
        candidate.sample_weight >= PROVEN_SAMPLE
        and candidate.wins >= PROVEN_WINS
        and candidate.multiplier >= PROVEN_MULTIPLIER
        and candidate.setup >= PROVEN_SETUP
    )


def signal_levels(plan: Plan) -> dict | None:
    """Entry, SL, and TP for an active order or for the order that is waiting on price."""
    if plan.bias != "flat" and plan.entry_low is not None and plan.invalidation is not None and plan.target is not None:
        return {
            "side": "SELL" if plan.bias == "short" else "BUY",
            "pending": False,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "sl": plan.invalidation,
            "tp": plan.target,
            "tp2": plan.target_far,
        }
    if (
        plan.shadow_entry_low is not None
        and plan.shadow_entry_high is not None
        and plan.shadow_invalidation is not None
        and plan.shadow_target is not None
    ):
        side = "SELL" if plan.shadow_invalidation > plan.shadow_entry_high else "BUY"
        return {
            "side": side,
            "pending": True,
            "entry_low": plan.shadow_entry_low,
            "entry_high": plan.shadow_entry_high,
            "sl": plan.shadow_invalidation,
            "tp": plan.shadow_target,
            "tp2": plan.shadow_target_far,
        }
    return None


def _no_chase(bias: str, chart: ChartRead) -> float:
    """A short at the day's low, or a long at the day's high, is already late."""
    if bias == "short" and chart.range_pos <= 0.35:
        return 0.55
    if bias == "long" and chart.range_pos >= 0.75:
        return 0.55
    return 1.0


def _score_dollar(chart: ChartRead, macro: MacroRead, news: NewsRead) -> tuple[float, str]:
    if macro.dxy_state == "up":
        bias = "short"
    elif macro.dxy_state == "down":
        bias = "long"
    elif news.usd_score >= 0.35:
        bias = "short"
    elif news.usd_score <= -0.35:
        bias = "long"
    else:
        return 0.12, "flat"

    if macro.dxy_state == "flat":
        move = min(1.0, abs(news.usd_score))
    else:
        move = min(1.0, abs(macro.dxy_session_pct) / 0.35)
    corr_bonus = 0.0 if macro.dxy_overlap < 12 else max(0.0, -macro.gold_dxy_corr)
    news_agree = max(0.0, news.usd_score) if bias == "short" else max(0.0, -news.usd_score)
    setup = 0.45 * move + 0.35 * corr_bonus + 0.20 * news_agree
    if macro.dxy_state != "flat":
        setup = min(1.0, setup + 0.04)
    if macro.dxy_overlap >= 12 and macro.gold_dxy_corr > 0.25:
        setup *= 0.7
    if (bias == "short" and news.usd_score < -0.35) or (bias == "long" and news.usd_score > 0.35):
        setup *= 0.8
    if news.shock:
        setup *= 0.75
    if not macro.dxy_available:
        setup *= 0.6
    setup *= _no_chase(bias, chart)
    return min(1.0, max(0.0, setup)), bias


def _score_oil(chart: ChartRead, macro: MacroRead, news: NewsRead) -> tuple[float, str]:
    magnitude = abs(news.oil_score)
    move = min(1.0, abs(macro.oil_session_pct) / 1.3)
    if not macro.oil_available and magnitude < 0.35:
        return 0.05, "flat"
    if magnitude < 0.35 and move < 0.45:
        return 0.08, "flat"
    if news.oil_score > 0.25 or (macro.oil_state == "up" and news.oil_score >= 0):
        bias = "long"
        agreement = max(magnitude, move)
    elif news.oil_score < -0.45 and macro.oil_overlap >= 12 and macro.gold_oil_corr > 0.3:
        bias = "short"
        agreement = magnitude
    else:
        return 0.12, "flat"
    corr = macro.gold_oil_corr if macro.oil_overlap >= 12 else 0.0
    setup = min(0.72, 0.55 * agreement + 0.15 * max(0.0, corr))
    if news.shock and magnitude > 0.4:
        setup = min(0.80, setup + 0.08)
    if not macro.oil_available:
        setup *= 0.7
    setup *= _no_chase(bias, chart)
    return min(1.0, setup), bias


def _score_trend(chart: ChartRead, macro: MacroRead, news: NewsRead, htf: str) -> tuple[float, str]:
    if chart.structure not in ("up", "down"):
        return 0.10, "flat"
    clarity = max(0.25, min(1.0, abs(chart.impulse_atr) / 1.1))
    bias = "long" if chart.structure == "up" else "short"
    opposed = (chart.structure == "up" and macro.dxy_state == "up") or (
        chart.structure == "down" and macro.dxy_state == "down"
    )
    aligned = (chart.structure == "up" and macro.dxy_state == "down") or (
        chart.structure == "down" and macro.dxy_state == "up"
    )
    factor = 0.50 if opposed else 0.92 if aligned else 0.78
    setup = clarity * factor
    if htf == chart.structure:
        setup = min(1.0, setup + 0.06)
    elif htf in ("up", "down") and htf != chart.structure:
        setup *= 0.65
    if news.shock:
        setup *= 0.8
    setup *= _no_chase(bias, chart)
    return min(1.0, setup), bias


def _score_fade(chart: ChartRead, macro: MacroRead, news: NewsRead, htf: str) -> tuple[float, str]:
    if chart.structure != "sideways" or news.shock:
        return 0.10, "flat"
    if chart.range_pos >= 0.62:
        bias = "short"
    elif chart.range_pos <= 0.38:
        bias = "long"
    else:
        return 0.16, "flat"
    extreme = min(1.0, abs(chart.range_pos - 0.5) * 2)
    dxy_noise = min(1.0, abs(macro.dxy_session_pct) / 0.30)
    oil_noise = min(1.0, abs(macro.oil_session_pct) / 0.80)
    quiet = max(0.0, 1.0 - max(dxy_noise, oil_noise * 0.5))
    vol_score = 0.30 if chart.compression >= 1.25 else 1.0
    headlines_quiet = abs(news.usd_score) < 0.25 and abs(news.oil_score) < 0.25
    setup = (0.46 * extreme) + (0.24 * quiet) + (0.15 * vol_score) + (0.15 * (1.0 if headlines_quiet else 0.35))
    if htf in ("up", "down"):
        setup *= 0.8
    if chart.range_pos >= 0.98 and abs(chart.impulse_atr) > 0.8:
        setup *= 0.55
    if not macro.dxy_available:
        setup *= 0.75
    if (bias == "long" and macro.dxy_state == "up") or (bias == "short" and macro.dxy_state == "down"):
        setup *= 0.55
    return min(1.0, setup), bias


def _score_aside(chart: ChartRead, macro: MacroRead, news: NewsRead, htf: str) -> float:
    score = 0.22
    if news.shock:
        score += 0.34
    opposed = (chart.structure == "up" and macro.dxy_state == "up") or (
        chart.structure == "down" and macro.dxy_state == "down"
    )
    if opposed:
        score += 0.20
    if htf in ("up", "down") and chart.structure in ("up", "down") and htf != chart.structure:
        score += 0.12
    if abs(news.usd_score) > 0.55 and macro.dxy_state == "flat":
        score += 0.10
    if not macro.dxy_available:
        score += 0.08
    return min(0.95, score)


def _levels(bias: str, chart: ChartRead) -> tuple[float, float, float, float, float]:
    atr = max(chart.atr, 0.01)
    span = max(chart.day_high - chart.day_low, atr)
    width = max(0.40 * atr, 0.20 * span)
    mid = (chart.day_high + chart.day_low) / 2
    if bias == "short":
        cap = chart.last_swing_high if chart.last_swing_high is not None else chart.day_high
        zone_top = max(chart.day_high, cap)
        zone_bottom = zone_top - width
        invalidation = zone_top + 0.55 * atr
        target = chart.vwap if chart.vwap < zone_bottom - 0.25 * atr else mid
        if target >= zone_bottom:
            target = zone_bottom - atr
        far = chart.day_low if chart.day_low < target - 0.1 * atr else target - atr
        return zone_bottom, zone_top, invalidation, target, far
    floor = chart.last_swing_low if chart.last_swing_low is not None else chart.day_low
    zone_bottom = min(chart.day_low, floor)
    zone_top = zone_bottom + width
    invalidation = zone_bottom - 0.55 * atr
    target = chart.vwap if chart.vwap > zone_top + 0.25 * atr else mid
    if target <= zone_top:
        target = zone_top + atr
    far = chart.day_high if chart.day_high > target + 0.1 * atr else target + atr
    return zone_bottom, zone_top, invalidation, target, far


def _thesis_selected(chosen: _Candidate, chart: ChartRead, macro: MacroRead, news: NewsRead, htf: str) -> str:
    if chosen.style == "range_fade":
        where = "upper part" if chart.range_pos >= 0.62 else "lower part"
        verb = "sell strength back toward the middle" if chosen.bias == "short" else "buy weakness back toward the middle"
        if macro.dxy_state == "flat" and macro.oil_state == "flat":
            context = "The dollar is flat and oil is flat, so neither is driving."
        elif macro.dxy_state != "flat":
            context = (
                f"The dollar is {macro.dxy_state} ({macro.dxy_session_pct:+.2f}% this UTC day) and oil is {macro.oil_state}. "
                "The dollar approach did not lead on adjusted score, so this run uses the range."
            )
        else:
            context = (
                f"Oil is {macro.oil_state} ({macro.oil_session_pct:+.2f}% this UTC day) and the dollar is flat. "
                "Oil did not take the lead, so this run uses the range."
            )
        return (
            f"Gold is sideways and price is in the {where} of the UTC-day range "
            f"({chart.day_low:.2f} to {chart.day_high:.2f}). {context} "
            f"The plan is to {verb}, working {chosen.entry_low:.2f} to {chosen.entry_high:.2f}, "
            f"with the first target at {chosen.target:.2f}. This is a range plan for this session only."
        )
    if chosen.style == "dollar_lead":
        side = "sell rallies" if chosen.bias == "short" else "buy dips"
        if macro.dxy_overlap >= 12:
            corr = f"Recent 5-minute returns correlate {macro.gold_dxy_corr:+.2f} with the dollar."
        else:
            corr = "There is not enough overlapping price yet to trust the correlation."
        return (
            f"DXY is {macro.dxy_state} ({macro.dxy_session_pct:+.2f}% this UTC day). {corr} "
            f"Dollar headlines score {news.usd_score:+.2f}. Oil is {macro.oil_state} and is not the reason for the trade. "
            f"The plan is to {side} in {chosen.entry_low:.2f} to {chosen.entry_high:.2f}. "
            f"The idea is void through {chosen.invalidation:.2f}, and also if the dollar move itself fades."
        )
    if chosen.style == "oil_shock":
        side = "buy dips" if chosen.bias == "long" else "sell rallies"
        return (
            f"Oil is the story this session. WTI is {macro.oil_state} ({macro.oil_session_pct:+.2f}%) "
            f"and oil headlines score {news.oil_score:+.2f}. "
            f"A supply-led rise in oil is treated as support for gold. "
            f"The plan is to {side} in {chosen.entry_low:.2f} to {chosen.entry_high:.2f}, "
            f"invalid through {chosen.invalidation:.2f}. The dollar is {macro.dxy_state}. "
            "If the dollar takes over, run a new brief."
        )
    aligned = (chart.structure == "up" and macro.dxy_state == "down") or (
        chart.structure == "down" and macro.dxy_state == "up"
    )
    if aligned:
        dollar_line = "The dollar agrees with that direction."
    elif macro.dxy_state == "flat":
        dollar_line = "The dollar is quiet, so the chart is the driver."
    else:
        dollar_line = "The dollar is not cleanly aligned, so the target stays the nearer one."
    verb = "buys a dip" if chosen.bias == "long" else "sells a rally"
    return (
        f"The 5-minute structure is {chart.structure} and the 1-hour structure is {htf}. {dollar_line} "
        f"The plan {verb} in {chosen.entry_low:.2f} to {chosen.entry_high:.2f} "
        f"instead of chasing {chart.last_price:.2f}. Invalidation is {chosen.invalidation:.2f}. "
        f"Oil is {macro.oil_state}."
    )


def _thesis_flat(
    decision: str,
    chart: ChartRead,
    macro: MacroRead,
    news: NewsRead,
    rejected: _Candidate | None,
    second: _Candidate | None,
    aside: _Candidate,
    htf: str,
    sizing_problem: str | None = None,
) -> str:
    if decision == "cannot_size" and rejected is not None:
        return (
            f"{STYLES[rejected.style]} ({rejected.bias}) cleared the bar, but {sizing_problem}. "
            "No trade. The risk limit wins over the setup."
        )
    if decision == "shock_margin" and rejected is not None:
        gap = rejected.adjusted - aside.adjusted
        return (
            f"Fresh headline shock is in the tape. {STYLES[rejected.style]} leads standing aside "
            f"by only {gap:.2f}, which is not enough to take a trade while that headline is live. "
            f"{_chase_note(chart, rejected.bias)}"
            "No position. Run a new brief when the price has absorbed the headline, or when the dollar move fades."
        )
    if decision == "conflict" and rejected is not None and second is not None:
        return (
            f"The drivers disagree. {STYLES[rejected.style]} wants to be {rejected.bias} "
            f"(adjusted {rejected.adjusted:.2f}) and {STYLES[second.style]} wants to be {second.bias} "
            f"(adjusted {second.adjusted:.2f}). The gap is too small to force a trade. "
            f"Stand aside until one of them gives way. The 1-hour structure is {htf}. "
            f"The dollar is {macro.dxy_state} and oil is {macro.oil_state}."
        )
    if decision == "aside_higher":
        return (
            "Headline risk outranks the setup. Stand aside until the chart and the dollar agree, "
            "then run a new brief. "
            f"Dollar headlines score {news.usd_score:+.2f}. Oil headlines score {news.oil_score:+.2f}."
        )
    if rejected is not None and rejected.bias != "flat":
        return (
            f"The closest idea was {STYLES[rejected.style]} ({rejected.bias}), "
            f"adjusted score {rejected.adjusted:.2f}, below the {MIN_SCORE:.2f} line required for a trade. "
            f"{_chase_note(chart, rejected.bias)}"
            "Standing aside is the plan until the chart, the dollar, oil, or the journal changes."
        )
    return (
        f"Gold is {chart.structure} at {chart.last_price:.2f}, "
        f"about {chart.range_pos:.0%} of the way up the UTC-day range. "
        f"The dollar is {macro.dxy_state} and oil is {macro.oil_state}. "
        "There is no location and no macro push. No trade."
    )


def _chase_note(chart: ChartRead, bias: str) -> str:
    if bias == "short" and chart.range_pos <= 0.35:
        return (
            f"Price is already near the low of the UTC-day range ({chart.range_pos:.0%} of the way up), "
            "so the short would be chasing a move that has happened. "
        )
    if bias == "long" and chart.range_pos >= 0.75:
        return (
            f"Price is already near the high of the UTC-day range ({chart.range_pos:.0%} of the way up), "
            "so the long would be chasing a move that has happened. "
        )
    return ""


def _change_if(
    style: str,
    decision: str,
    macro: MacroRead,
    news: NewsRead,
    invalidation: float | None,
    horizon_minutes: int,
) -> list[str]:
    items = [
        f"This plan is graded after {horizon_minutes} minutes, or sooner if price hits the invalidation or the target.",
        "Running the brief again before that replaces this plan and does not grade it.",
    ]
    if decision == "selected" and invalidation is not None:
        items.append(f"Price through {invalidation:.2f} kills the idea.")
    if style == "dollar_lead":
        items.append("If the dollar index gives its session move back and is flat again, cancel the idea even if the stop is intact.")
    elif style == "range_fade":
        items.append("If the 5-minute structure turns into a trend, or the dollar leaves a flat session, cancel the fade and run a new brief.")
    elif style == "oil_shock":
        items.append("If oil gives back the session move, or the headline is walked back, cancel the idea.")
    elif style == "trend_align":
        items.append("If the 5-minute structure breaks, or the dollar starts a strong opposing move, cancel the trend idea.")
    elif macro.dxy_state != "flat":
        items.append("Run a new brief if price moves back toward the middle of the day range. Do not trade this extreme only because the dollar has already moved.")
        items.append("If the dollar gives its session move back and is flat again, that dollar idea is finished for this session.")
    elif macro.oil_state != "flat":
        items.append("Run a new brief if oil gives back the session move, or if price reaches the other side of the day range.")
    else:
        items.append("Run a new brief when price reaches the edge of the day range, or when the dollar or oil leaves flat.")
    if abs(news.usd_score) >= 0.2 or abs(news.oil_score) >= 0.2 or news.shock:
        items.append("A headline that flips the dollar or oil story means you run a new brief before entering.")
    if not macro.dxy_available:
        items.append("Dollar index price was missing, so a dollar story is not confirmed.")
    if not macro.oil_available:
        items.append("WTI price was missing. Oil headlines can still change the next plan.")
    return items
