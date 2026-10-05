"""Read intraday gold structure from OHLC bars.

This describes the chart. It does not emit a standing buy or sell rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd


@dataclass
class ChartRead:
    last_price: float
    last_ts: datetime
    atr: float
    vwap: float
    day_high: float
    day_low: float
    range_pos: float
    structure: str
    compression: float
    impulse_atr: float
    session: str
    last_swing_high: float | None
    last_swing_low: float | None
    bar_count: int


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise ValueError("no bars")
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if "ts" not in out.columns:
        raise ValueError("bars need a ts column")
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"bars need a {col} column")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    out = out.dropna(subset=["ts", "open", "high", "low", "close"])
    out = out.sort_values("ts").drop_duplicates("ts", keep="last")
    return out.reset_index(drop=True)


def read_chart(df: pd.DataFrame, now: datetime | None = None) -> ChartRead:
    bars = prepare(df)
    if len(bars) < 30:
        raise ValueError("need at least 30 gold bars to read the chart")
    now = now or bars["ts"].iloc[-1].to_pydatetime()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    day = _session_slice(bars, now)
    atr = _atr(bars, 14)
    if atr <= 0:
        span = float(day["high"].max() - day["low"].min())
        atr = max(span * 0.1, 0.5)

    last = bars.iloc[-1]
    last_price = float(last["close"])
    last_ts = last["ts"].to_pydatetime()
    day_high = float(day["high"].max())
    day_low = float(day["low"].min())
    span = day_high - day_low
    if span <= 1e-9:
        range_pos = 0.5
    else:
        range_pos = min(1.0, max(0.0, (last_price - day_low) / span))

    short_atr = _atr(bars.tail(48), 10)
    long_atr = _atr(bars.tail(160), 40)
    if short_atr <= 0 or long_atr <= 0:
        compression = 1.0
    else:
        compression = short_atr / long_atr

    look = min(7, len(bars) - 1)
    impulse = (last_price - float(bars["close"].iloc[-1 - look])) / atr

    window = bars.tail(400).reset_index(drop=True)
    highs, lows = _swings(window, span=2)
    structure = _structure(highs, lows, bars, atr)

    return ChartRead(
        last_price=last_price,
        last_ts=last_ts,
        atr=float(atr),
        vwap=float(_vwap(day)),
        day_high=day_high,
        day_low=day_low,
        range_pos=float(range_pos),
        structure=structure,
        compression=float(compression),
        impulse_atr=float(impulse),
        session=_session_name(last_ts),
        last_swing_high=highs[-1][1] if highs else None,
        last_swing_low=lows[-1][1] if lows else None,
        bar_count=len(bars),
    )


def to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    bars = prepare(df)
    out = (
        bars.set_index("ts")
        .resample("1h")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return out


def _session_slice(bars: pd.DataFrame, now: datetime) -> pd.DataFrame:
    stamp = pd.Timestamp(now)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    sliced = bars[bars["ts"].dt.date == stamp.date()]
    if len(sliced) < 12:
        return bars.tail(78).reset_index(drop=True)
    return sliced.reset_index(drop=True)


def _atr(df: pd.DataFrame, period: int) -> float:
    if len(df) < 2:
        return 0.0
    prev = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.tail(period).mean()
    if pd.isna(value):
        return 0.0
    return float(value)


def _vwap(df: pd.DataFrame) -> float:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    volume = df["volume"].clip(lower=0)
    if float(volume.sum()) <= 0:
        return float(typical.mean())
    return float((typical * volume).sum() / volume.sum())


def _swings(df: pd.DataFrame, span: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    for i in range(span, len(df) - span):
        if high[i] == max(high[i - span : i + span + 1]) and high[i] > high[i - 1] and high[i] > high[i + 1]:
            highs.append((i, float(high[i])))
        if low[i] == min(low[i - span : i + span + 1]) and low[i] < low[i - 1] and low[i] < low[i + 1]:
            lows.append((i, float(low[i])))
    return highs, lows


def _structure(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    bars: pd.DataFrame,
    atr: float,
) -> str:
    tolerance = max(atr * 0.15, 0.01)
    if len(highs) >= 2 and len(lows) >= 2:
        higher_high = highs[-1][1] > highs[-2][1] + tolerance
        lower_high = highs[-1][1] < highs[-2][1] - tolerance
        higher_low = lows[-1][1] > lows[-2][1] + tolerance
        lower_low = lows[-1][1] < lows[-2][1] - tolerance
        if higher_high and higher_low:
            return "up"
        if lower_high and lower_low:
            return "down"
        return "sideways"
    tail = bars.tail(20)
    change = float(tail["close"].iloc[-1] - tail["close"].iloc[0])
    if change > 1.2 * atr:
        return "up"
    if change < -1.2 * atr:
        return "down"
    return "sideways"


def _session_name(ts: datetime) -> str:
    hour = ts.astimezone(timezone.utc).hour
    if hour < 7:
        return "asia"
    if hour < 12:
        return "london"
    if hour < 17:
        return "overlap"
    if hour < 21:
        return "newyork"
    return "late"
