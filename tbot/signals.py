"""Buy and sell setups for gold, sent to ntfy with the data behind them.

Every analysis builds both sides from the same chart: where a buy would make
sense, where a sell would make sense, each with entry, SL, and TP sized to the
risk rules. The strategy's scores say which side it favors. Between analyses a
light price check watches for an entry, a stop, or a target and sends an alert
the moment one happens.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tbot.risk import RiskRules, size_plan
from tbot.strategy import MIN_SCORE, STYLES, _levels

ACTIVE_MINUTES = 120


@dataclass
class Setup:
    side: str
    entry_low: float
    entry_high: float
    sl: float
    tp1: float
    tp2: float | None
    rr: float
    risk_usd: float
    units: float
    score: float
    style: str
    favored: bool
    ready: bool
    status: str = "valid"
    reason: str = ""
    created_at: str = ""
    checked_at: str = ""


VALID = "valid"
FAR = "far"
WEAK = "weak"
PAUSED = "paused"
INVALID = "invalid"
EXPIRED = "expired"
TRADABLE = (VALID, FAR, WEAK)

STATUS_LABEL = {
    VALID: "Valid",
    FAR: "Valid, price far",
    WEAK: "Weak",
    PAUSED: "Paused",
    INVALID: "Invalid",
    EXPIRED: "Expired",
}


def research_check(setup: Setup, brief, blackout) -> tuple[str, str]:
    """Does this side still make sense against the dollar, oil, news, and trend right now?"""
    chart, macro, news = brief.chart, brief.macro, brief.news
    if blackout is not None:
        return PAUSED, f"{blackout.title} at {blackout.when:%H:%M UTC} is a high-impact US release. Wait until it passes."
    corr = macro.gold_dxy_corr if macro.dxy_overlap >= 12 else 0.0
    follows_dollar = corr <= -0.3
    if setup.side == "BUY":
        if macro.dxy_session_pct >= 0.25 and follows_dollar and news.usd_score >= 0.35:
            return INVALID, (
                f"Dollar is up {macro.dxy_session_pct:+.2f}% with hawkish news ({news.usd_score:+.2f}) "
                f"and gold is moving against it (corr {corr:+.2f}). Buying fights the dollar."
            )
        if chart.structure == "down" and brief.htf_structure == "down":
            return INVALID, "Gold is trending down on both the 5-minute and 1-hour charts."
    else:
        if macro.dxy_session_pct <= -0.25 and follows_dollar and news.usd_score <= -0.35:
            return INVALID, (
                f"Dollar is down {macro.dxy_session_pct:+.2f}% with dovish news ({news.usd_score:+.2f}) "
                f"and gold is moving against it (corr {corr:+.2f}). Selling fights the dollar."
            )
        if chart.structure == "up" and brief.htf_structure == "up":
            return INVALID, "Gold is trending up on both the 5-minute and 1-hour charts."
    if setup.score < 0.30:
        return WEAK, f"Strategy score is only {setup.score:.2f}. Take it only with a clear reaction at the entry."
    return VALID, _support_reason(setup, brief)


def price_check(item: dict, price: float, atr: float, now: datetime) -> tuple[str, str] | None:
    """Minute check on price alone. None means the research status stands."""
    created = datetime.fromisoformat(item["created_at"]) if item.get("created_at") else now
    if now - created >= timedelta(minutes=ACTIVE_MINUTES):
        return EXPIRED, f"Signal is {ACTIVE_MINUTES} minutes old without an entry. Waiting for a fresh one."
    if item["side"] == "BUY" and price <= item["sl"]:
        return INVALID, f"Price {price:.2f} broke below the SL {item['sl']:.2f} before the entry triggered."
    if item["side"] == "SELL" and price >= item["sl"]:
        return INVALID, f"Price {price:.2f} broke above the SL {item['sl']:.2f} before the entry triggered."
    gap = item["entry_low"] - price if price < item["entry_low"] else price - item["entry_high"] if price > item["entry_high"] else 0.0
    if gap > 6 * atr:
        return FAR, f"Price is {gap:.2f} away from the entry ({gap / atr:.1f} ATR). Unlikely to fill soon."
    return None


def _support_reason(setup: Setup, brief) -> str:
    macro, news = brief.macro, brief.news
    parts = [f"Score {setup.score:.2f} ({STYLES.get(setup.style, setup.style)})"]
    if setup.side == "BUY":
        if macro.dxy_state == "down":
            parts.append(f"dollar softer {macro.dxy_session_pct:+.2f}%")
        if news.oil_score >= 0.35:
            parts.append(f"oil supply news {news.oil_score:+.2f}")
        if brief.chart.last_price < brief.chart.vwap:
            parts.append("price below VWAP")
    else:
        if macro.dxy_state == "up":
            parts.append(f"dollar firmer {macro.dxy_session_pct:+.2f}%")
        if news.usd_score >= 0.35:
            parts.append(f"hawkish dollar news {news.usd_score:+.2f}")
        if brief.chart.last_price > brief.chart.vwap:
            parts.append("price above VWAP")
    return ", ".join(parts) + "."


def build_setups(brief, rules: RiskRules) -> list[Setup]:
    chart = brief.chart
    plan = brief.plan
    best: dict[str, tuple[float, str]] = {}
    for row in plan.scorecard:
        if row.bias in ("long", "short"):
            if row.bias not in best or row.adjusted > best[row.bias][0]:
                best[row.bias] = (row.adjusted, row.style)
    setups: list[Setup] = []
    for bias in ("long", "short"):
        entry_low, entry_high, sl, tp, tp_far = _levels(bias, chart)
        sizing = size_plan(
            bias=bias,
            entry_low=entry_low,
            entry_high=entry_high,
            invalidation=sl,
            target=tp,
            target_far=tp_far,
            atr=chart.atr,
            confidence=0.5,
            rules=rules,
        )
        if isinstance(sizing, str):
            continue
        score, style = best.get(bias, (0.0, "range_fade"))
        far = sizing.target_far if abs(sizing.target_far - sizing.target) >= 0.5 else None
        setups.append(
            Setup(
                side="BUY" if bias == "long" else "SELL",
                entry_low=round(sizing.entry_low, 2),
                entry_high=round(sizing.entry_high, 2),
                sl=round(sl, 2),
                tp1=round(sizing.target, 2),
                tp2=round(far, 2) if far is not None else None,
                rr=round(sizing.reward_ratio, 1),
                risk_usd=round(sizing.risk_usd, 2),
                units=sizing.units,
                score=round(score, 2),
                style=style,
                favored=False,
                ready=plan.bias == bias,
            )
        )
    if setups:
        top = max(setups, key=lambda item: item.score)
        top.favored = True
    return setups


def research_lines(brief, calendar, blackout, now: datetime) -> list[str]:
    chart, macro, news = brief.chart, brief.macro, brief.news
    lines = [
        f"Gold {chart.last_price:.2f} (VANTAGE:XAUUSD). 5m {chart.structure}, 1h {brief.htf_structure}. "
        f"VWAP {chart.vwap:.2f}, ATR {chart.atr:.2f}, day range {chart.day_low:.2f}-{chart.day_high:.2f}.",
    ]
    if macro.dxy_available and macro.dxy_last is not None:
        corr = f", gold-dollar corr {macro.gold_dxy_corr:+.2f}" if macro.dxy_overlap >= 12 else ""
        lines.append(f"DXY {macro.dxy_last:.2f} ({macro.dxy_session_pct:+.2f}% today, {macro.dxy_state}){corr}.")
    else:
        lines.append("DXY price unavailable this run.")
    if macro.oil_available and macro.oil_last is not None:
        corr = f", gold-oil corr {macro.gold_oil_corr:+.2f}" if macro.oil_overlap >= 12 else ""
        lines.append(f"WTI {macro.oil_last:.2f} ({macro.oil_session_pct:+.2f}% today, {macro.oil_state}){corr}.")
    lines.append(
        f"Headlines: dollar {news.usd_score:+.2f}, oil {news.oil_score:+.2f}"
        + (", fresh shock" if news.shock else "")
        + "."
    )
    if news.usd_headlines:
        lines.append(f"Dollar: {news.usd_headlines[0]}")
    if news.oil_headlines:
        lines.append(f"Oil: {news.oil_headlines[0]}")
    if blackout is not None:
        lines.append(f"US event blackout: {blackout.title} at {blackout.when:%H:%M UTC}. Do not enter until it passes.")
    else:
        upcoming = [event for event in calendar.upcoming(now) if event.impact == "High"]
        if upcoming:
            event = upcoming[0]
            lines.append(f"Next high-impact US release: {event.title} {event.when:%a %H:%M UTC}.")
        else:
            lines.append("No high-impact US release in the next 24 hours.")
    return lines


def setup_text(setup: Setup) -> str:
    tag = "favored" if setup.favored else "other side"
    if setup.ready:
        tag = "strategy says trade"
    tp2 = f" | TP2 {setup.tp2:.2f}" if setup.tp2 is not None else ""
    status = STATUS_LABEL.get(setup.status, setup.status).upper()
    return (
        f"{setup.side} XAUUSD [{status}] ({tag}, score {setup.score:.2f})\n"
        f"Entry {setup.entry_low:.2f}-{setup.entry_high:.2f} | SL {setup.sl:.2f} | TP1 {setup.tp1:.2f}{tp2}\n"
        f"R:R 1:{setup.rr:.1f}, {setup.units:g} oz, about ${setup.risk_usd:.2f} at SL\n"
        f"Check: {setup.reason}"
    )


def update(settings, brief, calendar, blackout, now: datetime, send) -> list[str]:
    """Send the new setups when they change. Returns lines for the page."""
    state = _load(settings)
    setups = build_setups(brief, settings.risk)
    research = research_lines(brief, calendar, blackout, now)
    atr = max(brief.chart.atr, 0.5)
    stance = _stance(brief)
    previous = [Setup(**item) for item in state.get("setups", [])]
    by_side = {item.side: item for item in previous}
    for item in setups:
        item.status, item.reason = research_check(item, brief, blackout)
        prior = by_side.get(item.side)
        same_levels = prior is not None and abs(prior.entry_low - item.entry_low) <= 0.5 * atr and abs(prior.sl - item.sl) <= 0.5 * atr
        item.created_at = prior.created_at if same_levels and prior.created_at else now.isoformat()
        item.checked_at = now.isoformat()
        moved = price_check(asdict(item), brief.chart.last_price, atr, now)
        if moved is not None and item.status in TRADABLE:
            item.status, item.reason = moved
    _pick_favored(setups)
    changed = _changed(previous, setups, atr) or state.get("stance") != stance
    if not changed:
        for item in setups:
            prior = by_side.get(item.side)
            if prior is not None and prior.status != item.status:
                _send_status(send, item, brief.chart.last_price)
    if changed and setups:
        favored = next((item for item in setups if item.favored), setups[0])
        title = f"Gold signals: {favored.side} favored"
        if any(item.ready for item in setups):
            title = f"Gold {next(item for item in setups if item.ready).side} signal"
        body = "\n\n".join(setup_text(item) for item in _ordered(setups))
        body += "\n\n" + stance + "\n\nWhy:\n- " + "\n- ".join(research)
        body += "\n\nWait for price to reach the entry. Alert only, no order sent."
        send(title, body, priority="high" if any(item.ready for item in setups) else "default")
    state.update(
        {
            "updated_at": now.isoformat(),
            "atr": atr,
            "price": brief.chart.last_price,
            "stance": stance,
            "setups": [asdict(item) for item in setups],
            "research": research,
        }
    )
    state.setdefault("active", [])
    _save(settings, state)
    return page_lines(state)


def tick(settings, bars: pd.DataFrame, now: datetime, send) -> list[str]:
    """Check fresh bars against waiting entries and active trades."""
    state = _load(settings)
    if not state.get("setups") and not state.get("active"):
        return []
    last_check = state.get("last_check")
    since = datetime.fromisoformat(last_check) if last_check else now - timedelta(minutes=5)
    fresh = bars[bars["ts"] > since - timedelta(minutes=5)]
    if fresh.empty:
        return []
    high = float(fresh["high"].max())
    low = float(fresh["low"].min())
    price = float(bars["close"].iloc[-1])
    notes: list[str] = []
    active = state.get("active", [])
    taken = {item["side"] for item in active if not item.get("closed")}
    used = {(item["side"], item["entry_low"]) for item in active if item.get("closed")}
    atr = float(state.get("atr") or 1.0)

    for item in state.get("setups", []):
        item["checked_at"] = now.isoformat()
        if item["side"] in taken or (item["side"], item["entry_low"]) in used:
            continue
        if item.get("status") in (INVALID, EXPIRED, PAUSED):
            continue
        moved = price_check(item, price, atr, now)
        if moved is not None and moved[0] != item.get("status"):
            item["status"], item["reason"] = moved
            _send_status(send, Setup(**item), price)
            notes.append(f"{item['side']} now {item['status']}: {item['reason']}")
            if item["status"] in (INVALID, EXPIRED):
                continue
        elif moved is None and item.get("status") == FAR:
            item["status"], item["reason"] = VALID, f"Price {price:.2f} is back within reach of the entry."
            _send_status(send, Setup(**item), price)
            notes.append(f"{item['side']} valid again")
        if item["side"] == "BUY":
            entered = low <= item["entry_high"]
            stopped = low <= item["sl"]
        else:
            entered = high >= item["entry_low"]
            stopped = high >= item["sl"]
        if not entered:
            continue
        if stopped:
            send(f"Gold {item['side']} skipped", f"{item['side']} XAUUSD ran through entry and SL {item['sl']:.2f} in one move. Gold {price:.2f}. No entry.", priority="default")
            notes.append(f"{item['side']} ran through entry and SL")
            taken.add(item["side"])
            active.append({**item, "entered_at": now.isoformat(), "closed": "skipped"})
            continue
        entry = item["entry_high"] if item["side"] == "BUY" else item["entry_low"]
        tp2 = f"\nTP2 {item['tp2']:.2f}" if item.get("tp2") else ""
        send(
            f"ENTRY NOW: {item['side']} XAUUSD",
            (
                f"Gold reached the {item['side']} entry. Price {price:.2f}.\n"
                f"Entry {entry:.2f}\nSL {item['sl']:.2f}\nTP1 {item['tp1']:.2f}{tp2}\n"
                f"{item['units']:g} oz, about ${item['risk_usd']:.2f} at SL. Alert only, no order sent."
            ),
            priority="urgent",
        )
        notes.append(f"{item['side']} entry reached at {price:.2f}")
        taken.add(item["side"])
        active.append({**item, "entry": entry, "entered_at": now.isoformat(), "closed": None})

    for item in active:
        if item.get("closed"):
            continue
        if item["side"] == "BUY":
            hit_sl, hit_tp = low <= item["sl"], high >= item["tp1"]
        else:
            hit_sl, hit_tp = high >= item["sl"], low <= item["tp1"]
        opened = datetime.fromisoformat(item["entered_at"])
        if hit_sl:
            item["closed"] = "sl"
            send(f"SL hit: {item['side']} XAUUSD", f"Stop {item['sl']:.2f} reached. Gold {price:.2f}. About -${item['risk_usd']:.2f}.", priority="high")
            notes.append(f"{item['side']} SL hit")
        elif hit_tp:
            item["closed"] = "tp"
            gain = abs(item["tp1"] - item["entry"]) * item["units"]
            send(f"TP hit: {item['side']} XAUUSD", f"Target {item['tp1']:.2f} reached. Gold {price:.2f}. About +${gain:.2f}.", priority="high")
            notes.append(f"{item['side']} TP hit")
        elif now - opened >= timedelta(minutes=ACTIVE_MINUTES):
            item["closed"] = "time"
            send(f"Close {item['side']} XAUUSD", f"{ACTIVE_MINUTES} minutes passed without SL or TP. Gold {price:.2f}. The strategy closes here.", priority="default")
            notes.append(f"{item['side']} closed on time")

    cutoff = now - timedelta(hours=12)
    state["active"] = [item for item in active if not item.get("closed") or datetime.fromisoformat(item["entered_at"]) > cutoff]
    state["last_check"] = now.isoformat()
    state["price"] = price
    _save(settings, state)
    return notes


def page_lines(state: dict) -> list[str]:
    lines = []
    for item in _ordered([Setup(**raw) for raw in state.get("setups", [])]):
        lines.append(setup_text(item))
        lines.append("")
    if state.get("stance"):
        lines.append(state["stance"])
    open_trades = [item for item in state.get("active", []) if not item.get("closed")]
    for item in open_trades:
        lines.append(f"ACTIVE: {item['side']} from {item['entry']:.2f}, SL {item['sl']:.2f}, TP1 {item['tp1']:.2f}")
    if state.get("research"):
        lines.append("")
        lines.append("Why:")
        lines.extend(f"- {line}" for line in state["research"])
    return lines or ["No setups yet."]


def _status_alert(setup: Setup, price: float) -> tuple:
    label = STATUS_LABEL.get(setup.status, setup.status)
    if setup.status in (INVALID, EXPIRED, PAUSED):
        title = f"{setup.side} XAUUSD signal {label.lower()}"
        body = f"{setup.reason}\nGold {price:.2f}. Do not take the {setup.side} at {setup.entry_low:.2f}-{setup.entry_high:.2f}."
        return title, body, {"priority": "high"}
    title = f"{setup.side} XAUUSD signal {label.lower()}"
    body = (
        f"{setup.reason}\nGold {price:.2f}.\n"
        f"Entry {setup.entry_low:.2f}-{setup.entry_high:.2f} | SL {setup.sl:.2f} | TP1 {setup.tp1:.2f}"
    )
    return title, body, {"priority": "default"}


def _send_status(send, setup: Setup, price: float) -> None:
    title, body, options = _status_alert(setup, price)
    send(title, body, **options)


def _pick_favored(setups: list[Setup]) -> None:
    for item in setups:
        item.favored = False
    live = [item for item in setups if item.status in TRADABLE] or setups
    if live:
        max(live, key=lambda item: item.score).favored = True


def _stance(brief) -> str:
    from tbot.report import DECISION

    plan = brief.plan
    held = getattr(brief, "held", None)
    if held is not None and held.bias != "flat":
        side = "BUY" if held.bias == "long" else "SELL"
        return (
            f"Strategy: the {side} plan from {held.opened_at[11:16]} UTC is still running "
            f"({STYLES.get(held.style, held.style)}). It stays in charge until SL, TP, or 120 minutes."
        )
    if plan.bias != "flat":
        side = "BUY" if plan.bias == "long" else "SELL"
        return f"Strategy: {side} is the trade ({STYLES[plan.style]}, confidence {plan.confidence:.0%})."
    best = max((row.adjusted for row in plan.scorecard if row.bias != "flat"), default=0.0)
    if best < MIN_SCORE:
        reason = f"No side clears the {MIN_SCORE:.2f} bar (best {best:.2f})."
    else:
        reason = DECISION.get(plan.decision, "The strategy is waiting.")
    return f"Strategy: wait. {reason} Take an entry only if price reaches it."


def _ordered(setups: list[Setup]) -> list[Setup]:
    return sorted(setups, key=lambda item: (not item.ready, not item.favored, item.side))


def _changed(old: list[Setup], new: list[Setup], atr: float) -> bool:
    if {item.side for item in old} != {item.side for item in new}:
        return True
    by_side = {item.side: item for item in old}
    for item in new:
        prev = by_side[item.side]
        if prev.favored != item.favored or prev.ready != item.ready:
            return True
        if abs(prev.entry_low - item.entry_low) > 0.5 * atr or abs(prev.sl - item.sl) > 0.5 * atr:
            return True
    return False


def _path(settings) -> Path:
    return settings.memory_path.parent / "signals.json"


def _load(settings) -> dict:
    path = _path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _save(settings, state: dict) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")

