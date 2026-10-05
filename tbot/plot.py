"""Save a chart with the session levels drawn on it."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from tbot.chart import ChartRead
from tbot.strategy import Plan


def save_chart(
    path: Path,
    bars: pd.DataFrame,
    chart: ChartRead,
    plan: Plan,
    *,
    heading: str,
    sketch: bool = False,
    setups: list[dict] | None = None,
) -> Path | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    view = bars.tail(180).copy()
    if view.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11.5, 6.6), dpi=120)
    fig.patch.set_facecolor("#11151c")
    ax.set_facecolor("#11151c")
    for spine in ax.spines.values():
        spine.set_color("#1e2530")
    ax.plot(view["ts"], view["close"], color="#e8ebf0", linewidth=1.2, label="Gold")
    ax.axhline(chart.vwap, color="#e3b341", linestyle="--", linewidth=0.9, alpha=0.8, label=f"VWAP {chart.vwap:.2f}")
    last_x = view["ts"].iloc[-1]
    ax.scatter([last_x], [chart.last_price], color="#e3b341", s=26, zorder=5)
    ax.annotate(
        f"{chart.last_price:.2f}",
        (last_x, chart.last_price),
        textcoords="offset points",
        xytext=(8, 6),
        fontsize=9,
        color="#e3b341",
        fontweight="bold",
    )

    if setups:
        colors = {"BUY": "#2fbf71", "SELL": "#ef5350"}
        for item in setups:
            color = colors.get(item["side"], "#8a94a6")
            alpha = 0.28 if item.get("favored") or item.get("ready") else 0.14
            ax.axhspan(item["entry_low"], item["entry_high"], color=color, alpha=alpha,
                       label=f"{item['side']} entry {item['entry_low']:.2f}-{item['entry_high']:.2f}")
            ax.axhline(item["sl"], color="#ef5350", linestyle="-", linewidth=1.0, alpha=0.9)
            ax.axhline(item["tp1"], color="#2fbf71", linestyle="--", linewidth=1.0, alpha=0.9)
            ax.annotate(f"{item['side']} SL {item['sl']:.2f}", (view["ts"].iloc[0], item["sl"]), textcoords="offset points",
                        xytext=(4, 3), fontsize=8, color="#ef5350")
            ax.annotate(f"{item['side']} TP {item['tp1']:.2f}", (view["ts"].iloc[0], item["tp1"]), textcoords="offset points",
                        xytext=(4, 3), fontsize=8, color="#2fbf71")
        ax.set_title(f"{heading}  |  buy and sell setups", loc="left", fontsize=12, color="#e8ebf0")
        _finish(ax, fig, path, plt)
        return path

    levels = None
    if plan.bias != "flat" and plan.entry_low is not None:
        levels = (plan.entry_low, plan.entry_high, plan.invalidation, plan.target, plan.target_far, False)
        side = "SELL" if plan.bias == "short" else "BUY"
    elif plan.shadow_entry_low is not None and plan.shadow_invalidation is not None and plan.shadow_target is not None:
        levels = (
            plan.shadow_entry_low,
            plan.shadow_entry_high,
            plan.shadow_invalidation,
            plan.shadow_target,
            plan.shadow_target_far,
            True,
        )
        side = "SELL" if plan.shadow_invalidation > plan.shadow_entry_high else "BUY"
    else:
        side = ""

    if levels is not None:
        entry_low, entry_high, sl, tp, tp2, pending = levels
        name = f"Pending {side} entry" if pending else f"{side} entry"
        ax.axhspan(entry_low, entry_high, color="#e2c56b", alpha=0.45, label=f"{name} {entry_low:.2f}-{entry_high:.2f}")
        ax.axhline(sl, color="#8d2f2f", linestyle="-", linewidth=1.2, label=f"SL {sl:.2f}")
        ax.axhline(tp, color="#1f6b45", linestyle="-", linewidth=1.2, label=f"TP1 {tp:.2f}")
        if tp2 is not None and abs(tp2 - tp) >= 0.5:
            ax.axhline(tp2, color="#1f6b45", linestyle=":", linewidth=1.0, label=f"TP2 {tp2:.2f}")
        title = f"{heading}  |  {side} if entry" if pending else f"{heading}  |  {side}"
    else:
        title = f"{heading}  |  no entry"

    if sketch:
        title = f"{title}  |  sketch"
    ax.set_title(title, loc="left", fontsize=12, color="#e8ebf0")
    _finish(ax, fig, path, plt)
    return path


def _finish(ax, fig, path: Path, plt) -> None:
    ax.tick_params(colors="#8a94a6")
    ax.grid(True, axis="y", color="#1e2530", linewidth=0.6)
    legend = ax.legend(frameon=False, fontsize=8, loc="lower right")
    for text in legend.get_texts():
        text.set_color("#c7cdd8")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def schematic_bars(chart: ChartRead, n: int = 180) -> pd.DataFrame:
    """A range-shaped sketch so the demo can show levels without live prices."""
    end = pd.Timestamp(chart.last_ts)
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    stamps = pd.date_range(end=end, periods=n, freq="5min")
    mid = (chart.day_high + chart.day_low) / 2
    amp = max((chart.day_high - chart.day_low) / 2, chart.atr)
    closes = [mid + amp * math.sin(i / 6.5) for i in range(n)]
    shift = chart.last_price - closes[-1]
    rows = []
    for stamp, close in zip(stamps, closes):
        price = close + shift
        rows.append(
            {
                "ts": stamp,
                "open": price,
                "high": price + chart.atr * 0.12,
                "low": price - chart.atr * 0.12,
                "close": price,
                "volume": 1.0,
            }
        )
    return pd.DataFrame(rows)
