"""Dollar index and WTI context for a gold session."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tbot.chart import prepare

# A quiet UTC-day move. Gold cares about a small dollar shift; oil needs a larger one.
DXY_FLAT_PCT = 0.08
OIL_FLAT_PCT = 0.40


@dataclass
class MacroRead:
    dxy_last: float | None
    dxy_session_pct: float
    dxy_state: str
    dxy_available: bool
    oil_last: float | None
    oil_session_pct: float
    oil_state: str
    oil_available: bool
    gold_dxy_corr: float
    gold_oil_corr: float
    dxy_overlap: int
    oil_overlap: int


def read_macro(
    gold: pd.DataFrame,
    dxy: pd.DataFrame | None,
    oil: pd.DataFrame | None,
) -> MacroRead:
    gold_bars = prepare(gold)
    dxy_last, dxy_pct, dxy_ok = _leg(dxy)
    oil_last, oil_pct, oil_ok = _leg(oil)
    dxy_corr, dxy_n = (0.0, 0) if not dxy_ok else _return_corr(gold_bars, prepare(dxy))
    oil_corr, oil_n = (0.0, 0) if not oil_ok else _return_corr(gold_bars, prepare(oil))
    return MacroRead(
        dxy_last=dxy_last,
        dxy_session_pct=dxy_pct,
        dxy_state=_state(dxy_pct, DXY_FLAT_PCT) if dxy_ok else "flat",
        dxy_available=dxy_ok,
        oil_last=oil_last,
        oil_session_pct=oil_pct,
        oil_state=_state(oil_pct, OIL_FLAT_PCT) if oil_ok else "flat",
        oil_available=oil_ok,
        gold_dxy_corr=dxy_corr,
        gold_oil_corr=oil_corr,
        dxy_overlap=dxy_n,
        oil_overlap=oil_n,
    )


def _leg(df: pd.DataFrame | None) -> tuple[float | None, float, bool]:
    if df is None or len(df) == 0:
        return None, 0.0, False
    try:
        bars = prepare(df)
    except ValueError:
        return None, 0.0, False
    if len(bars) < 2:
        return None, 0.0, False
    day = _utc_day(bars)
    use = day if len(day) >= 2 else bars.tail(36)
    first = float(use.iloc[0]["open"])
    last = float(use.iloc[-1]["close"])
    if first == 0:
        return last, 0.0, True
    return last, (last - first) / first * 100.0, True


def _utc_day(bars: pd.DataFrame) -> pd.DataFrame:
    day = bars["ts"].iloc[-1].date()
    return bars[bars["ts"].dt.date == day]


def _state(pct: float, band: float) -> str:
    if pct >= band:
        return "up"
    if pct <= -band:
        return "down"
    return "flat"


def _return_corr(left: pd.DataFrame, right: pd.DataFrame) -> tuple[float, int]:
    aligned = pd.concat(
        [
            left.set_index("ts")["close"].resample("5min").last().rename("left"),
            right.set_index("ts")["close"].resample("5min").last().rename("right"),
        ],
        axis=1,
        sort=True,
    ).dropna()
    returns = aligned.pct_change().dropna().tail(48)
    count = int(len(returns))
    if count < 12:
        return 0.0, count
    if float(returns["left"].std()) < 1e-12 or float(returns["right"].std()) < 1e-12:
        return 0.0, count
    value = float(returns["left"].corr(returns["right"]))
    if value != value:  # NaN
        return 0.0, count
    return value, count
