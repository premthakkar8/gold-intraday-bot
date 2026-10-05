"""Run one live pass and write its reports. Shared by the manual and scheduled commands."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from tbot.broker_mt5 import MT5Broker
from tbot.paper import PaperBroker
from tbot.site import build_site
from tbot.config import Settings
from tbot.econ_calendar import describe, load_calendar
from tbot.engine import analyze
from tbot.market import MarketDataError, load_context, load_market, price_note_for
from tbot.memory import Memory
from tbot.news import fetch_headlines
from tbot.plot import save_chart
from tbot.report import Brief, dump_json, render, render_html
from tbot.risk import HIGH
from tbot.strategy import STYLES

STALE_MINUTES = 45
LOCK_STALE_MINUTES = 10
CREATE_NO_WINDOW = 0x08000000


@dataclass
class RunResult:
    code: int
    summary: str
    text: str = ""
    brief: Brief | None = None


def market_open(now: datetime) -> bool:
    """Spot gold and COMEX futures: Sunday 22:00 UTC to Friday 21:00 UTC, with a daily 21:00-22:00 UTC pause."""
    now = now.astimezone(timezone.utc)
    weekday = now.weekday()
    if weekday == 5:
        return False
    if weekday == 4 and now.hour >= 21:
        return False
    if weekday == 6 and now.hour < 22:
        return False
    if now.hour == 21:
        return False
    return True


def run_live(settings: Settings, *, keep_running_plan: bool, scheduled: bool) -> RunResult:
    now = datetime.now(timezone.utc)
    if scheduled and not market_open(now):
        return RunResult(0, "market closed, skipped")
    lock = settings.memory_path.parent / "run.lock"
    if not _acquire(lock):
        return RunResult(0, "another run is in progress, skipped")
    try:
        return _run(settings, now, keep_running_plan=keep_running_plan, scheduled=scheduled)
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _run(settings: Settings, now: datetime, *, keep_running_plan: bool, scheduled: bool) -> RunResult:
    calendar = load_calendar(settings.calendar_cache, settings.calendar_refresh_minutes, now)
    blackout = calendar.blackout(now, settings.blackout_before_minutes, settings.blackout_after_minutes)
    broker_lines: list[str] = []
    broker = None
    if settings.broker.enabled and not settings.alerts_only:
        broker = make_broker(settings)
        problem = broker.connect()
        if problem is not None:
            broker_lines.append(f"Not connected: {problem}")
            marker = settings.memory_path.parent / "mt5_warned"
            stale = not marker.exists() or time.time() - marker.stat().st_mtime > 2 * 3600
            if settings.notify and stale:
                notify("Gold bot: MT5 not connected", problem)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(now.isoformat(), encoding="utf-8")
            broker = None
    try:
        return _run_connected(
            settings, now, broker, broker_lines, calendar, blackout,
            keep_running_plan=keep_running_plan, scheduled=scheduled,
        )
    finally:
        if broker is not None:
            broker.shutdown()


def _run_connected(
    settings: Settings,
    now: datetime,
    broker,
    broker_lines: list[str],
    calendar,
    blackout,
    *,
    keep_running_plan: bool,
    scheduled: bool,
) -> RunResult:
    if broker is not None and broker.provides_prices:
        try:
            gold = broker.rates()
        except RuntimeError as exc:
            return RunResult(1, str(exc), text=str(exc))
        gold_symbol = f"{broker.spec.symbol} (MT5 broker prices)"
        dxy_symbol, dxy, oil_symbol, oil, warnings = load_context(settings)
        price_note = ""
    else:
        try:
            gold_symbol, gold, dxy_symbol, dxy, oil_symbol, oil, warnings = load_market(settings)
        except MarketDataError as exc:
            return RunResult(1, f"no prices: {exc}", text=str(exc))
        price_note = price_note_for(gold_symbol)
    if broker is not None:
        for note in broker.manage(now, settings.horizon_minutes, cancel_pending=blackout is not None, bars=gold):
            broker_lines.append(note)
        broker_lines.insert(0, broker.account_text)
        settings = dc_replace(settings, risk=broker.rules(settings.risk))

    last_bar = gold["ts"].max().to_pydatetime()
    age = now - last_bar
    if scheduled and age > timedelta(minutes=STALE_MINUTES):
        return RunResult(0, f"latest gold bar is {age.total_seconds() / 60:.0f} minutes old, skipped")

    headlines, news_warning = fetch_headlines(settings.news_lookback_hours)
    memory = Memory.load(settings.memory_path, settings.half_life_days)
    brief = analyze(
        gold,
        dxy,
        oil,
        headlines,
        memory,
        settings,
        now=now,
        gold_symbol=gold_symbol,
        dxy_symbol=dxy_symbol,
        oil_symbol=oil_symbol,
        price_note=price_note,
        news_warning=news_warning,
        feed_warnings=warnings,
        keep_running_plan=keep_running_plan,
    )

    order_note = None
    plan = brief.plan
    if broker is not None and brief.opened and plan.bias != "flat":
        blocked = broker.can_trade()
        exposure = broker.exposure()
        if blocked:
            order_note = f"order not sent: {blocked}"
        elif blackout is not None:
            order_note = f"order not sent: {blackout.title} at {blackout.when:%H:%M UTC} is inside the event blackout"
        elif exposure:
            order_note = "order not sent: the bot already has " + "; ".join(exposure)
        else:
            order_note = broker.place(plan)
        broker_lines.append(f"Plan {plan.id}: {order_note}")
        if settings.notify:
            notify("Gold bot order", order_note)
    if broker is not None:
        exposure = broker.exposure()
        broker_lines.append("Bot exposure: " + ("; ".join(exposure) if exposure else "none"))

    text = render(brief)
    extra = ["", *describe(calendar, now, blackout)]
    if settings.broker.enabled:
        extra += ["", "DEMO ACCOUNT", *broker_lines]
        if broker is not None:
            extra += ["", *broker.deals_summary()]
    text = text + "\n" + "\n".join(extra)
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    (settings.report_dir / "latest.txt").write_text(text, encoding="utf-8")
    (settings.report_dir / "latest.html").write_text(render_html(text), encoding="utf-8")
    (settings.report_dir / "latest.json").write_text(dump_json(brief), encoding="utf-8")
    shown = brief.plan
    chart_path = save_chart(
        settings.report_dir / "latest.png",
        gold,
        brief.chart,
        shown,
        heading=f"{settings.gold_symbol} 5m | {STYLES[shown.style]}",
    )
    if settings.notify:
        _notify_brief(brief, blackout, notify_plan=settings.alerts_only or broker is None)
    summary = _summary(brief)
    if blackout is not None:
        summary += f" | blackout {blackout.title}"
    for line in broker_lines[1:]:
        summary += f" | broker: {line}"
    if settings.broker.enabled and broker is None and broker_lines:
        summary += f" | broker: {broker_lines[0]}"
    if settings.alerts_only:
        account_lines = [alert_text(brief, blackout)]
        lead = "Alerts only. No orders are sent. A phone alert fires when a trade plan opens. This page reloads every 5 minutes."
        section = "Current alert"
    elif broker is not None:
        account_lines = broker.deals_summary()
        lead = "Paper demo account. Updated every 15 minutes while the gold market is open. This page reloads every 5 minutes."
        section = "Demo account"
    else:
        account_lines = broker_lines or ["No demo account connected."]
        lead = "Updated every 15 minutes while the gold market is open. This page reloads every 5 minutes."
        section = "Status"
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    build_site(
        settings.site_dir,
        text,
        chart_path,
        account_lines,
        [*_log_tail(settings, 19), f"{stamp}  {summary}"],
        lead=lead,
        section_title=section,
        tv_symbol=settings.gold_symbol if ":" in settings.gold_symbol else None,
    )
    return RunResult(0, summary, text=text, brief=brief)


def _log_tail(settings: Settings, count: int) -> list[str]:
    if not settings.log_path.exists():
        return []
    lines = settings.log_path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip()][-count:]


def make_broker(settings: Settings):
    if settings.broker.platform == "mt5":
        return MT5Broker(settings.broker, settings.memory_path.parent / "broker_state.json")
    return PaperBroker(settings.paper, settings.memory_path.parent / "paper.json", symbol=settings.gold_symbol)


def log(settings: Settings, message: str) -> None:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with settings.log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}  {message}\n")


def _summary(brief: Brief) -> str:
    plan = brief.plan
    parts = [f"gold {brief.chart.last_price:.2f}"]
    if brief.graded:
        parts.append("graded: " + "; ".join(brief.graded))
    if brief.held is not None:
        held = brief.held
        parts.append(f"holding {held.bias} {held.style} {held.id}")
    if brief.opened:
        if plan.bias == "flat":
            parts.append(f"opened stand aside {plan.id}")
        else:
            parts.append(
                f"opened {plan.bias} {plan.style} {plan.id} zone {plan.entry_low:.2f}-{plan.entry_high:.2f} "
                f"stop {plan.invalidation:.2f} target {plan.target:.2f} units {plan.units:g} "
                f"risk {plan.risk_usd:.2f} USD tier {plan.tier} conf {plan.confidence:.2f}"
            )
    else:
        parts.append(f"new read {plan.bias} {plan.style} not opened")
    return " | ".join(parts)


def alert_text(brief: Brief, blackout=None) -> str:
    plan = brief.plan
    price = f"Gold {brief.chart.last_price:.2f}."
    if plan.bias == "flat" or plan.entry_low is None:
        return f"No trade. {price} {STYLES[plan.style]}."
    side = "SELL" if plan.bias == "short" else "BUY"
    tier = " High-confidence size." if plan.tier == HIGH else ""
    lines = [
        f"{side} gold. {STYLES[plan.style]}. Confidence {plan.confidence:.0%}.{tier}",
        f"Entry {plan.entry_low:.2f} to {plan.entry_high:.2f}.",
        f"Stop {plan.invalidation:.2f}. Target {plan.target:.2f} (1:{plan.reward_ratio:.1f}), then {plan.target_far:.2f}.",
        f"Size {plan.units:g} oz, about {plan.risk_usd:.2f} USD at the stop.",
        "Alert only. Place this yourself. No order was sent.",
    ]
    if blackout is not None:
        lines.append(f"Wait: {blackout.title} is a high-impact US release at {blackout.when:%H:%M UTC}.")
    return " ".join(lines)


def _notify_brief(brief: Brief, blackout, notify_plan: bool = True) -> None:
    for line in brief.graded:
        notify("Gold plan graded", line, priority="default")
    plan = brief.plan
    if notify_plan and brief.opened and plan.bias != "flat":
        tier = "HIGH-CONFIDENCE " if plan.tier == HIGH else ""
        notify(
            f"Gold {tier}{plan.bias.upper()} alert",
            alert_text(brief, blackout),
            priority="high",
        )


def notify(title: str, body: str, *, priority: str | None = None) -> None:
    """Best-effort phone push (ntfy.sh) and Windows notification. Never raises."""
    topic = os.environ.get("TBOT_NTFY_TOPIC", "").strip()
    if topic:
        headers = {
            "Title": title.encode("ascii", "replace").decode("ascii"),
            "Tags": "chart_with_upwards_trend",
            "Click": "https://premthakkar8.github.io/gold-intraday-bot/",
        }
        if priority:
            headers["Priority"] = priority
        try:
            request = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=body.encode("utf-8"),
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(request, timeout=10).close()
        except Exception:
            pass
    if sys.platform != "win32":
        return
    payload = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )
    script = (
        "$ErrorActionPreference='Stop';"
        "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime];"
        "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime];"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml($env:TBOT_TOAST);"
        "$t=New-Object Windows.UI.Notifications.ToastNotification $x;"
        "$app='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($t)"
    )
    env = dict(os.environ, TBOT_TOAST=payload)
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            timeout=20,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _acquire(lock) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < LOCK_STALE_MINUTES * 60:
            return False
        lock.unlink(missing_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return False
    return True
