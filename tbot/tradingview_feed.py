"""5-minute bars from a TradingView chart symbol, such as VANTAGE:XAUUSD.

The public chart session is enough for recent intraday bars. No TradingView login is used.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone

import pandas as pd

from tbot.market import MarketDataError

SOCKET = "wss://data.tradingview.com/socket.io/websocket"
ORIGIN = "https://data.tradingview.com"


def fetch_chart(symbol: str, resolution: str = "5", bars: int = 1200) -> pd.DataFrame:
    """Return ts, open, high, low, close, volume for a TradingView symbol."""
    try:
        from websocket import create_connection
    except ImportError as exc:
        raise MarketDataError("websocket-client is not installed. Run: pip install -r requirements.txt") from exc

    ws = create_connection(SOCKET, header={"Origin": ORIGIN}, timeout=20)
    try:
        session = "cs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        _send(ws, "set_auth_token", ["unauthorized_user_token"])
        _send(ws, "chart_create_session", [session, ""])
        payload = "=" + json.dumps({"symbol": symbol, "adjustment": "splits"}, separators=(",", ":"))
        _send(ws, "resolve_symbol", [session, "symbol_1", payload])
        _send(ws, "create_series", [session, "s1", "s1", "symbol_1", resolution, bars])
        for _ in range(50):
            raw = ws.recv()
            if _heartbeat(raw):
                ws.send(raw)
                continue
            for message in _messages(raw):
                method = message.get("m")
                if method in ("symbol_error", "series_error"):
                    raise MarketDataError(f"TradingView chart {symbol} did not load ({method})")
                if method not in ("timescale_update", "du"):
                    continue
                series = message["p"][1].get("s1", {}).get("s") or []
                if len(series) >= 30:
                    return _frame(series)
    finally:
        ws.close()
    raise MarketDataError(f"TradingView chart {symbol} returned no bars")


def _send(ws, method: str, params: list) -> None:
    body = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    ws.send(f"~m~{len(body)}~m~{body}")


def _heartbeat(raw: str) -> bool:
    return "~h~" in raw and '"m":' not in raw


def _messages(raw: str) -> list[dict]:
    found: list[dict] = []
    index = 0
    while True:
        marker = raw.find("~m~", index)
        if marker < 0:
            break
        length_end = raw.find("~m~", marker + 3)
        if length_end < 0:
            break
        try:
            length = int(raw[marker + 3 : length_end])
        except ValueError:
            break
        start = length_end + 3
        packet = raw[start : start + length]
        index = start + length
        if packet.startswith("{"):
            try:
                found.append(json.loads(packet))
            except json.JSONDecodeError:
                continue
    return found


def _frame(series: list[dict]) -> pd.DataFrame:
    rows = []
    for item in series:
        values = item.get("v") or []
        if len(values) < 5:
            continue
        rows.append(
            {
                "ts": datetime.fromtimestamp(values[0], tz=timezone.utc),
                "open": values[1],
                "high": values[2],
                "low": values[3],
                "close": values[4],
                "volume": values[5] if len(values) > 5 else 0,
            }
        )
    frame = pd.DataFrame(rows)
    if len(frame) < 30:
        raise MarketDataError("TradingView chart returned too few bars")
    return frame
