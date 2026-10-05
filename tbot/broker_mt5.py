"""MetaTrader 5 connection: broker prices, demo orders, and order clean-up.

Orders carry a magic number and the plan id in the comment, so only this
bot's orders are ever cancelled or closed. Real-money accounts are refused
unless demo_only is turned off in config.yaml.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tbot.risk import RiskRules

MAGIC = 26100501
DONE = (10008, 10009)  # placed, done
INVALID_FILL = 10030
AUTOTRADING_OFF = (10026, 10027)


@dataclass
class BrokerSettings:
    enabled: bool = False
    platform: str = "paper"
    symbol: str = "XAUUSD"
    terminal_path: str = ""
    demo_only: bool = True
    deviation_points: int = 50
    login: int | None = None
    password: str | None = None
    server: str | None = None


@dataclass
class Spec:
    symbol: str
    digits: int
    point: float
    volume_min: float
    volume_step: float
    volume_max: float
    money_per_point_lot: float
    stops_distance: float
    filling_mode: int


def decide_entry(
    bias: str,
    bid: float,
    ask: float,
    entry_low: float,
    entry_high: float,
    invalidation: float,
) -> tuple[str, float] | str:
    """Limit at the near edge of the zone, market if price is already inside, nothing past the stop."""
    if bias == "short":
        if bid < entry_low:
            return "limit", entry_low
        if bid < invalidation:
            return "market", bid
        return f"price {bid:.2f} is already through the invalidation {invalidation:.2f}"
    if ask > entry_high:
        return "limit", entry_high
    if ask > invalidation:
        return "market", ask
    return f"price {ask:.2f} is already through the invalidation {invalidation:.2f}"


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines into the environment, without overriding values already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class MT5Broker:
    provides_prices = True

    def __init__(self, cfg: BrokerSettings, state_path: Path):
        self.cfg = cfg
        self.state_path = state_path
        self.mt5 = None
        self.spec: Spec | None = None
        self.offset_seconds = 0
        self.demo = False
        self.account_text = ""

    # connection

    def connect(self) -> str | None:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return "MetaTrader5 package missing. Run: pip install MetaTrader5"
        self.mt5 = mt5
        kwargs = {}
        if self.cfg.login and self.cfg.password and self.cfg.server:
            kwargs = {"login": int(self.cfg.login), "password": self.cfg.password, "server": self.cfg.server}
        ok = mt5.initialize(self.cfg.terminal_path, **kwargs) if self.cfg.terminal_path else mt5.initialize(**kwargs)
        if not ok:
            return f"MetaTrader 5 did not connect: {mt5.last_error()}. Open MT5 and log in to the demo account."
        account = mt5.account_info()
        if account is None:
            return "MetaTrader 5 is open but not logged in. Log in to the demo account."
        self.demo = account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        kind = "DEMO" if self.demo else "REAL"
        self.account_text = (
            f"{kind} account {account.login} on {account.server}: balance {account.balance:.2f} {account.currency}, "
            f"equity {account.equity:.2f}, leverage 1:{account.leverage}"
        )
        symbol = self._find_symbol(self.cfg.symbol)
        if symbol is None:
            return f"No gold symbol like {self.cfg.symbol} on this server."
        self.spec = self._spec(symbol)
        self.offset_seconds = self._server_offset()
        return None

    def shutdown(self) -> None:
        if self.mt5 is not None:
            self.mt5.shutdown()

    def can_trade(self) -> str | None:
        if not self.demo and self.cfg.demo_only:
            return "this is a real-money account and demo_only is on, so no orders are sent"
        terminal = self.mt5.terminal_info()
        if terminal is not None and not terminal.trade_allowed:
            return "Algo Trading is switched off in MetaTrader 5. Click the 'Algo Trading' button in the toolbar"
        return None

    def equity(self) -> float:
        account = self.mt5.account_info()
        return float(account.equity) if account is not None else 0.0

    def rules(self, base: RiskRules) -> RiskRules:
        spec = self.spec
        steps_min = max(1.0, round(spec.volume_min / spec.volume_step))
        return replace(
            base,
            account_usd=self.equity() or base.account_usd,
            point_value_usd=spec.money_per_point_lot * spec.volume_step,
            min_units=steps_min,
            unit_step=1.0,
        )

    # prices

    def rates(self, count: int = 1500) -> pd.DataFrame:
        mt5 = self.mt5
        raw = mt5.copy_rates_from_pos(self.spec.symbol, mt5.TIMEFRAME_M5, 0, count)
        if raw is None or len(raw) == 0:
            raise RuntimeError(f"no {self.spec.symbol} bars from MetaTrader 5: {mt5.last_error()}")
        frame = pd.DataFrame(raw)
        frame["ts"] = pd.to_datetime(frame["time"] - self.offset_seconds, unit="s", utc=True)
        frame["volume"] = frame["tick_volume"]
        return frame[["ts", "open", "high", "low", "close", "volume"]]

    # orders

    def exposure(self) -> list[str]:
        mt5 = self.mt5
        items = []
        for order in mt5.orders_get(symbol=self.spec.symbol) or ():
            if order.magic == MAGIC:
                items.append(f"pending order {order.ticket} {order.comment} at {order.price_open:.2f}")
        for position in mt5.positions_get(symbol=self.spec.symbol) or ():
            if position.magic == MAGIC:
                side = "long" if position.type == mt5.POSITION_TYPE_BUY else "short"
                items.append(
                    f"open {side} {position.volume:g} lot {position.comment} from {position.price_open:.2f}, "
                    f"profit {position.profit:+.2f}"
                )
        return items

    def manage(self, now: datetime, horizon_minutes: int, cancel_pending: bool, bars=None) -> list[str]:
        """Cancel pending orders past their window (or during an event), close positions past their window."""
        mt5 = self.mt5
        notes: list[str] = []
        limit = timedelta(minutes=horizon_minutes)
        friday_close = now.weekday() == 4 and (now.hour, now.minute) >= (20, 45)
        for order in mt5.orders_get(symbol=self.spec.symbol) or ():
            if order.magic != MAGIC:
                continue
            age = now - self._utc(order.time_setup)
            if age >= limit or cancel_pending or friday_close:
                result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})
                reason = "US event" if cancel_pending else "weekend" if friday_close else "time window ended"
                notes.append(f"cancelled pending {order.comment} ({reason}): {self._result_text(result)}")
        for position in mt5.positions_get(symbol=self.spec.symbol) or ():
            if position.magic != MAGIC:
                continue
            age = now - self._utc(position.time)
            if age >= limit or friday_close:
                reason = "weekend" if friday_close else "time window ended"
                notes.append(f"closed {position.comment} ({reason}): {self._close(position)}")
        return notes

    def place(self, plan) -> str:
        mt5 = self.mt5
        spec = self.spec
        tick = mt5.symbol_info_tick(spec.symbol)
        if tick is None:
            return "no live quote, order not sent"
        decision = decide_entry(plan.bias, tick.bid, tick.ask, plan.entry_low, plan.entry_high, plan.invalidation)
        if isinstance(decision, str):
            return f"order not sent: {decision}"
        kind, price = decision
        price = round(price, spec.digits)
        sl = round(plan.invalidation, spec.digits)
        tp = round(plan.target, spec.digits)
        gap = spec.stops_distance
        if abs(price - sl) < gap or abs(tp - price) < gap:
            return f"order not sent: stop or target is inside the broker's minimum distance ({gap:.2f})"
        if kind == "limit":
            current = tick.bid if plan.bias == "short" else tick.ask
            if abs(price - current) < gap:
                kind, price = "market", current

        volume = self._volume(plan.units * spec.volume_step)
        calc_type = mt5.ORDER_TYPE_SELL if plan.bias == "short" else mt5.ORDER_TYPE_BUY
        while volume >= spec.volume_min:
            loss = mt5.order_calc_profit(calc_type, spec.symbol, volume, price, sl)
            if loss is None or -loss <= plan.risk_budget + 0.01:
                break
            volume = self._volume(volume - spec.volume_step)
        else:
            return f"order not sent: the minimum volume risks more than {plan.risk_budget:.2f}"
        loss = mt5.order_calc_profit(calc_type, spec.symbol, volume, price, sl)
        if loss is not None and -loss > plan.risk_budget + 0.01:
            return f"order not sent: the minimum volume risks {-loss:.2f}, more than {plan.risk_budget:.2f}"

        if kind == "limit":
            order_type = mt5.ORDER_TYPE_SELL_LIMIT if plan.bias == "short" else mt5.ORDER_TYPE_BUY_LIMIT
            action = mt5.TRADE_ACTION_PENDING
        else:
            order_type = mt5.ORDER_TYPE_SELL if plan.bias == "short" else mt5.ORDER_TYPE_BUY
            action = mt5.TRADE_ACTION_DEAL
        request = {
            "action": action,
            "symbol": spec.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.cfg.deviation_points,
            "magic": MAGIC,
            "comment": f"tbot {plan.id}",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = self._send(request, pending=kind == "limit")
        risk = f"{-loss:.2f}" if loss is not None else "unknown"
        label = "SELL" if plan.bias == "short" else "BUY"
        what = f"{label} {'LIMIT ' if kind == 'limit' else ''}{volume:g} lot {spec.symbol} at {price:.2f}, SL {sl:.2f}, TP {tp:.2f}, risk {risk} USD"
        if result is not None and result.retcode in DONE:
            return f"sent {what} (ticket {result.order})"
        return f"order rejected: {what}: {self._result_text(result)}"

    def deals_summary(self, days: int = 30) -> list[str]:
        mt5 = self.mt5
        end = datetime.now(timezone.utc) + timedelta(days=1)
        start = end - timedelta(days=days + 1)
        deals = mt5.history_deals_get(start, end) or ()
        ours = [deal for deal in deals if deal.magic == MAGIC and deal.entry == mt5.DEAL_ENTRY_OUT]
        lines = [f"MT5 demo results, last {days} days"]
        if not ours:
            lines.append("No closed trades yet.")
        else:
            net = sum(deal.profit + deal.commission + deal.swap for deal in ours)
            wins = sum(1 for deal in ours if deal.profit > 0)
            lines.append(f"{len(ours)} closed trades, {wins} winners, net {net:+.2f}.")
            for deal in ours[-10:]:
                stamp = self._utc(deal.time).strftime("%m-%d %H:%M UTC")
                lines.append(f"{stamp}  {deal.comment:<16} {deal.volume:g} lot  {deal.profit + deal.commission + deal.swap:+.2f}")
        lines.append(self.account_text)
        return lines

    # helpers

    def _find_symbol(self, wanted: str) -> str | None:
        mt5 = self.mt5
        if mt5.symbol_info(wanted) is not None:
            mt5.symbol_select(wanted, True)
            return wanted
        for item in mt5.symbols_get("*XAU*USD*") or ():
            if mt5.symbol_select(item.name, True):
                return item.name
        return None

    def _spec(self, symbol: str) -> Spec:
        info = self.mt5.symbol_info(symbol)
        tick_size = info.trade_tick_size or info.point
        money = (info.trade_tick_value / tick_size) if tick_size else info.trade_contract_size
        return Spec(
            symbol=symbol,
            digits=info.digits,
            point=info.point,
            volume_min=info.volume_min,
            volume_step=info.volume_step,
            volume_max=info.volume_max,
            money_per_point_lot=money,
            stops_distance=max(info.trade_stops_level, info.trade_freeze_level) * info.point,
            filling_mode=info.filling_mode,
        )

    def _server_offset(self) -> int:
        """Server clock minus UTC, in seconds. MT5 timestamps are server wall-clock time."""
        state = self._read_state()
        tick = self.mt5.symbol_info_tick(self.spec.symbol)
        if tick is not None:
            diff = tick.time - time.time()
            if abs(diff - round(diff / 1800) * 1800) < 300:
                offset = int(round(diff / 1800) * 1800)
                state["offset_seconds"] = offset
                self._write_state(state)
                return offset
        return int(state.get("offset_seconds", 0))

    def _utc(self, server_seconds: int) -> datetime:
        return datetime.fromtimestamp(server_seconds - self.offset_seconds, tz=timezone.utc)

    def _volume(self, volume: float) -> float:
        spec = self.spec
        steps = int(volume / spec.volume_step + 1e-9)
        value = round(steps * spec.volume_step, 8)
        return min(value, spec.volume_max)

    def _fillings(self, pending: bool) -> list[int]:
        mt5 = self.mt5
        modes = []
        if pending:
            modes.append(mt5.ORDER_FILLING_RETURN)
        if self.spec.filling_mode & 1:
            modes.append(mt5.ORDER_FILLING_FOK)
        if self.spec.filling_mode & 2:
            modes.append(mt5.ORDER_FILLING_IOC)
        if mt5.ORDER_FILLING_RETURN not in modes:
            modes.append(mt5.ORDER_FILLING_RETURN)
        return modes

    def _send(self, request: dict, pending: bool):
        result = None
        for mode in self._fillings(pending):
            result = self.mt5.order_send({**request, "type_filling": mode})
            if result is None or result.retcode != INVALID_FILL:
                return result
        return result

    def _close(self, position) -> str:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            return "no quote"
        buy = position.type == mt5.POSITION_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": tick.bid if buy else tick.ask,
            "deviation": self.cfg.deviation_points,
            "magic": MAGIC,
            "comment": position.comment,
        }
        return self._result_text(self._send(request, pending=False))

    def _result_text(self, result) -> str:
        if result is None:
            return f"no response ({self.mt5.last_error()})"
        if result.retcode in DONE:
            return "ok"
        if result.retcode in AUTOTRADING_OFF:
            return "Algo Trading is off in MetaTrader 5"
        return f"retcode {result.retcode} {result.comment}"

    def _read_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
