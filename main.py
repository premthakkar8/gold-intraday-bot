"""Command line for the gold session brief."""

from __future__ import annotations

import argparse
import sys
import time
import traceback

from tbot.broker_mt5 import MT5Broker
from tbot.config import load_settings
from tbot.demo import run_demo
from tbot.memory import Memory
from tbot.runner import log, make_broker, run_live
from tbot import schedule


def emit(text: str) -> None:
    if sys.stdout is None:
        return
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gold intraday brief. Rebuilds a plan from the chart, the dollar, oil, and a journal."
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("demo", help="Show the journal changing a plan on two fixed sessions")
    brief = sub.add_parser("brief", help="Read live gold, the dollar index, WTI, and headlines once")
    brief.add_argument("--fresh", action="store_true", help="Replace a running plan instead of keeping it")
    sub.add_parser("auto", help="One scheduled pass. Skips when the market is closed. Used by Task Scheduler")
    watch = sub.add_parser("watch", help="Keep running in this window, one pass every N minutes")
    watch.add_argument("--every", type=int, default=15, help="Minutes between passes (default 15)")
    sched = sub.add_parser("schedule", help="Run automatically with Windows Task Scheduler")
    sched.add_argument("action", choices=["on", "off", "status"])
    sched.add_argument("--every", type=int, default=15, help="Minutes between passes (default 15)")
    sub.add_parser("review", help="Show graded journal results and MT5 demo results")
    sub.add_parser("mt5", help="Check the MetaTrader 5 connection, symbol, and risk sizing")
    args = parser.parse_args(argv)

    if args.command == "demo":
        emit(run_demo())
        return 0
    if args.command == "brief":
        return run_brief(fresh=args.fresh)
    if args.command == "auto":
        return run_auto()
    if args.command == "watch":
        return run_watch(max(args.every, 5))
    if args.command == "schedule":
        return run_schedule(args.action, max(args.every, 5))
    if args.command == "review":
        return run_review()
    if args.command == "mt5":
        return run_mt5_check()
    parser.print_help()
    emit("\nStart with: python main.py demo")
    return 2


def run_brief(fresh: bool = False) -> int:
    settings = load_settings()
    result = run_live(settings, keep_running_plan=not fresh, scheduled=False)
    emit(result.text or result.summary)
    if result.brief is not None:
        emit("")
        emit(f"Wrote {settings.report_dir / 'latest.txt'}")
        log(settings, "manual: " + result.summary)
    return result.code


def run_auto() -> int:
    settings = load_settings()
    try:
        result = run_live(settings, keep_running_plan=True, scheduled=True)
    except Exception:
        log(settings, "auto failed:\n" + traceback.format_exc())
        return 1
    log(settings, "auto: " + result.summary)
    emit(result.summary)
    return result.code


def run_watch(minutes: int) -> int:
    settings = load_settings()
    emit(f"Watching gold every {minutes} minutes. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                result = run_live(settings, keep_running_plan=True, scheduled=True)
                summary = result.summary
            except Exception as exc:
                summary = f"failed: {exc.__class__.__name__}: {exc}"
            log(settings, "watch: " + summary)
            emit(time.strftime("%H:%M") + "  " + summary)
            time.sleep(minutes * 60)
    except KeyboardInterrupt:
        emit("Stopped.")
        return 0


def run_schedule(action: str, minutes: int) -> int:
    if action == "on":
        code, output = schedule.install(minutes)
        if code == 0:
            emit(
                f"Scheduled: '{schedule.TASK_NAME}' runs every {minutes} minutes while you are logged in. "
                "It skips weekends and the daily 21:00-22:00 UTC pause."
            )
            emit("Log: reports/auto.log   Latest plan: reports/latest.html")
        else:
            emit(output)
        return code
    if action == "off":
        code, output = schedule.remove()
        emit(output)
        return code
    code, output = schedule.status()
    emit(output)
    return code


def run_review() -> int:
    settings = load_settings()
    memory = Memory.load(settings.memory_path, settings.half_life_days)
    emit(memory.summary())
    if settings.broker.enabled:
        broker = make_broker(settings)
        problem = broker.connect()
        emit("")
        if problem:
            emit(problem)
        else:
            emit("\n".join(broker.deals_summary()))
            broker.shutdown()
    return 0


def run_mt5_check() -> int:
    settings = load_settings()
    broker = MT5Broker(settings.broker, settings.memory_path.parent / "broker_state.json")
    problem = broker.connect()
    if problem:
        emit(problem)
        return 1
    try:
        spec = broker.spec
        rules = broker.rules(settings.risk)
        emit(broker.account_text)
        emit(
            f"Symbol {spec.symbol}: min {spec.volume_min:g} lot, step {spec.volume_step:g}, "
            f"{spec.money_per_point_lot:.2f} USD per 1.00 move per lot, minimum stop distance {spec.stops_distance:.2f}"
        )
        emit(f"Server clock offset from UTC: {broker.offset_seconds / 3600:+.1f} h")
        bars = broker.rates(50)
        emit(f"Latest bar {bars['ts'].iloc[-1]:%Y-%m-%d %H:%M UTC}, close {bars['close'].iloc[-1]:.2f}")
        smallest = rules.min_units * rules.point_value_usd
        emit(
            f"Risk: normal {rules.budget('normal'):.2f} USD, high tier {rules.budget('high'):.2f} USD "
            f"(15% of equity {rules.account_usd:.2f}). Smallest size loses {smallest:.2f} USD per 1.00 move."
        )
        blocked = broker.can_trade()
        emit("Orders: allowed" if blocked is None else f"Orders: blocked, {blocked}")
        exposure = broker.exposure()
        emit("Bot exposure: " + ("; ".join(exposure) if exposure else "none"))
    finally:
        broker.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
