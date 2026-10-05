"""Load gold, the dollar index, and WTI from Yahoo Finance."""

from __future__ import annotations

import logging

import pandas as pd

from tbot.chart import prepare
from tbot.config import Settings

GOLD_FALLBACKS = ("XAUUSD=X", "GC=F")
DXY_FALLBACKS = ("DX-Y.NYB", "DX=F")
OIL_FALLBACKS = ("CL=F",)


class MarketDataError(RuntimeError):
    pass


def load_market(settings: Settings) -> tuple[str, pd.DataFrame, str | None, pd.DataFrame | None, str | None, pd.DataFrame | None, list[str]]:
    warnings: list[str] = []
    gold_symbol, gold = _load_gold(settings, warnings)
    dxy_symbol, dxy = _first_available(settings.dxy_symbol, DXY_FALLBACKS, settings)
    if dxy is None:
        warnings.append("Dollar index price did not load. The brief will use dollar headlines only.")
    oil_symbol, oil = _first_available(settings.oil_symbol, OIL_FALLBACKS, settings)
    if oil is None:
        warnings.append("WTI price did not load. The brief will use oil headlines only.")
    return gold_symbol, gold, dxy_symbol, dxy, oil_symbol, oil, warnings


def load_context(settings: Settings) -> tuple[str | None, pd.DataFrame | None, str | None, pd.DataFrame | None, list[str]]:
    """Dollar index and WTI only, for when gold comes from the broker."""
    warnings: list[str] = []
    dxy_symbol, dxy = _first_available(settings.dxy_symbol, DXY_FALLBACKS, settings)
    if dxy is None:
        warnings.append("Dollar index price did not load. The brief will use dollar headlines only.")
    oil_symbol, oil = _first_available(settings.oil_symbol, OIL_FALLBACKS, settings)
    if oil is None:
        warnings.append("WTI price did not load. The brief will use oil headlines only.")
    return dxy_symbol, dxy, oil_symbol, oil, warnings


def fetch_bars(symbol: str, interval: str, period: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise MarketDataError("yfinance is not installed. Run: pip install -r requirements.txt") from exc
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    frame = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True,
        threads=False,
    )
    bars = _normalize(frame)
    if len(bars) < 30:
        raise MarketDataError(f"{symbol} returned only {len(bars)} bars")
    return bars


def price_note_for(symbol: str) -> str:
    if ":" in symbol:
        link = "https://www.tradingview.com/chart/?symbol=" + symbol.replace(":", "%3A")
        return (
            f"Gold prices are the {symbol} chart on TradingView ({link}). "
            "That is the Vantage XAUUSD gold CFD, so these levels match that chart rather than COMEX futures."
        )
    if symbol.upper() in {"GC=F", "MGC=F"}:
        return (
            "Prices are COMEX gold futures, not spot XAUUSD. "
            "Use the structure and the dollar relationship. Do not copy a futures price onto a spot ticket."
        )
    return ""


def _load_gold(settings: Settings, warnings: list[str]) -> tuple[str, pd.DataFrame]:
    symbol = settings.gold_symbol
    if ":" in symbol:
        try:
            from tbot.tradingview_feed import fetch_chart

            return symbol, fetch_chart(symbol, "5", 1200)
        except MarketDataError as exc:
            warnings.append(f"TradingView chart {symbol} did not load ({exc}). Using the fallback gold price.")
    gold_symbol, gold = _first_available(symbol if ":" not in symbol else "XAUUSD=X", GOLD_FALLBACKS, settings)
    if gold_symbol is None or gold is None:
        raise MarketDataError(
            "Gold prices did not load. Check the connection, then try again. "
            "python main.py demo still runs without a feed."
        )
    return gold_symbol, gold


def _first_available(
    preferred: str,
    fallbacks: tuple[str, ...],
    settings: Settings,
) -> tuple[str | None, pd.DataFrame | None]:
    symbols: list[str] = []
    for symbol in (preferred, *fallbacks):
        if symbol not in symbols:
            symbols.append(symbol)
    for symbol in symbols:
        try:
            return symbol, fetch_bars(symbol, settings.interval, settings.period)
        except MarketDataError:
            continue
    return None, None


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or len(frame) == 0:
        raise MarketDataError("empty response")
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.reset_index()
    out.columns = [str(column).lower().replace(" ", "_") for column in out.columns]
    for name in ("datetime", "date", "index"):
        if name in out.columns:
            out = out.rename(columns={name: "ts"})
            break
    else:
        out = out.rename(columns={out.columns[0]: "ts"})
    if "close" not in out.columns and "adj_close" in out.columns:
        out["close"] = out["adj_close"]
    return prepare(out)
