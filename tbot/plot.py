"""Save a chart with the session levels drawn on it."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from tbot.chart import ChartRead
from tbot.strategy import STYLES, Plan


def save_chart(
    path: Path,
    bars: pd.DataFrame,
    chart: ChartRead,
    plan: Plan,
    *,
    heading: str,
    sketch: bool = False,
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
    fig, ax = plt.subplots(figsize=(11.5, 6.2), dpi=120)
    fig.patch.set_facecolor("#f4f0e6")
    ax.set_facecolor("#fbf8f2")
    ax.plot(view["ts"], view["close"], color="#1d1a16", linewidth=1.15, label="Gold")
    ax.axhline(chart.vwap, color="#8a6a2f", linestyle="--", linewidth=0.9, label=f"VWAP {chart.vwap:.2f}")
    ax.axhline(chart.day_high, color="#b7b1a6", linewidth=0.7)
    ax.axhline(chart.day_low, color="#b7b1a6", linewidth=0.7)

    if plan.entry_low is not None and plan.entry_high is not None:
        ax.axhspan(plan.entry_low, plan.entry_high, color="#e2c56b", alpha=0.35, label="Entry zone")
        if plan.invalidation is not None:
            ax.axhline(plan.invalidation, color="#8d2f2f", linestyle=":", linewidth=1.1, label=f"Invalidation {plan.invalidation:.2f}")
        if plan.target is not None:
            ax.axhline(plan.target, color="#1f6b45", linestyle=":", linewidth=1.1, label=f"Target {plan.target:.2f}")
    elif plan.shadow_entry_low is not None and plan.shadow_entry_high is not None:
        label = "Rejected zone"
        if plan.shadow_style:
            label = f"Rejected: {STYLES[plan.shadow_style]}"
        ax.axhspan(plan.shadow_entry_low, plan.shadow_entry_high, color="#b7b1a6", alpha=0.35, label=label)
        if plan.shadow_invalidation is not None:
            ax.axhline(plan.shadow_invalidation, color="#8d2f2f", linestyle=":", linewidth=1.0, label="Rejected invalidation")

    title = heading
    if sketch:
        title = f"{heading}  |  sketch, not a live chart"
    ax.set_title(title, loc="left", fontsize=12, color="#1d1a16")
    ax.tick_params(colors="#1d1a16")
    ax.grid(True, axis="y", color="#e4ddd0", linewidth=0.6)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


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
