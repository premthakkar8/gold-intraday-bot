"""Paper demo account: orders filled against live 5-minute bars.

Same interface the runner uses for MetaTrader 5. Fills are conservative:
a limit fills only when a bar trades through its price, a bar that touches
both the stop and the target counts as the stop, and every round trip pays
the configured spread.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tbot.broker_mt5 import decide_entry
from tbot.risk import RiskRules


@dataclass
class PaperSettings:
    start_balance: float = 100.0
    spread: float = 0.30


@dataclass
class _Spec:
    symbol: str


class PaperBroker:
    provides_prices = False

    def __init__(self, cfg: PaperSettings, state_path: Path, symbol: str = "GOLD"):
        self.cfg = cfg
        self.state_path = state_path
        self.spec = _Spec(symbol)
        self.state: dict = {}
        self.last_price: float | None = None
        self.account_text = ""

    def connect(self) -> str | None:
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {
                "start_balance": self.cfg.start_balance,
                "balance": self.cfg.start_balance,
                "orders": [],
                "positions": [],
                "closed": [],
            }
        self._refresh_text()
        return None

    def shutdown(self) -> None:
        self._save()

    def can_trade(self) -> str | None:
        return None

    def equity(self) -> float:
        equity = self.state["balance"]
        if self.last_price is not None:
            for pos in self.state["positions"]:
                equity += self._pnl(pos, self.last_price)
        return equity

    def rules(self, base: RiskRules) -> RiskRules:
        return replace(base, account_usd=self.equity())

    def manage(self, now: datetime, horizon_minutes: int, cancel_pending: bool, bars: pd.DataFrame | None = None) -> list[str]:
        notes: list[str] = []
        if bars is None or len(bars) == 0:
            return notes
        bars = bars.sort_values("ts")
        self.last_price = float(bars["close"].iloc[-1])
        limit = timedelta(minutes=horizon_minutes)
        friday_close = now.weekday() == 4 and (now.hour, now.minute) >= (20, 45)

        for order in list(self.state["orders"]):
            placed = datetime.fromisoformat(order["placed_at"])
            window = bars[(bars["ts"] > placed) & (bars["ts"] <= placed + limit)]
            filled_at = None
            for row in window.itertuples(index=False):
                touched = row.high >= order["price"] if order["side"] == "short" else row.low <= order["price"]
                if touched:
                    filled_at = row.ts.to_pydatetime()
                    break
            if filled_at is not None:
                self.state["orders"].remove(order)
                pos = {**order, "entry": order["price"], "opened_at": placed.isoformat(), "filled_at": filled_at.isoformat()}
                self.state["positions"].append(pos)
                notes.append(f"filled {order['side']} limit {order['plan_id']} at {order['price']:.2f}")
            elif now - placed >= limit or cancel_pending or friday_close:
                self.state["orders"].remove(order)
                reason = "US event" if cancel_pending else "weekend" if friday_close else "time window ended"
                notes.append(f"cancelled pending {order['plan_id']} ({reason})")

        for pos in list(self.state["positions"]):
            start = datetime.fromisoformat(pos["filled_at"])
            opened = datetime.fromisoformat(pos["opened_at"])
            after = bars[bars["ts"] >= start]
            exit_price, reason = None, None
            for row in after.itertuples(index=False):
                if pos["side"] == "short":
                    if row.high >= pos["sl"]:
                        exit_price, reason = pos["sl"], "stop loss"
                    elif row.low <= pos["tp"]:
                        exit_price, reason = pos["tp"], "take profit"
                else:
                    if row.low <= pos["sl"]:
                        exit_price, reason = pos["sl"], "stop loss"
                    elif row.high >= pos["tp"]:
                        exit_price, reason = pos["tp"], "take profit"
                if exit_price is not None:
                    break
            if exit_price is None and (now - opened >= limit or friday_close):
                exit_price = self.last_price
                reason = "weekend" if friday_close else "time window ended"
            if exit_price is not None:
                notes.append(self._close(pos, exit_price, reason, now))
        self._refresh_text()
        self._save()
        return notes

    def exposure(self) -> list[str]:
        items = [
            f"pending {o['side']} limit {o['plan_id']} {o['units']:g} oz at {o['price']:.2f}" for o in self.state["orders"]
        ]
        for pos in self.state["positions"]:
            pnl = self._pnl(pos, self.last_price) if self.last_price is not None else 0.0
            items.append(f"open {pos['side']} {pos['plan_id']} {pos['units']:g} oz from {pos['entry']:.2f}, profit {pnl:+.2f}")
        return items

    def place(self, plan) -> str:
        if self.last_price is None:
            return "order not sent: no price yet"
        half = self.cfg.spread / 2
        bid, ask = self.last_price - half, self.last_price + half
        decision = decide_entry(plan.bias, bid, ask, plan.entry_low, plan.entry_high, plan.invalidation)
        if isinstance(decision, str):
            return f"order not sent: {decision}"
        kind, price = decision
        units = float(plan.units)
        loss = abs(price - plan.invalidation) * units + self.cfg.spread * units
        while loss > plan.risk_budget + 0.01 and units > 1:
            units -= 1
            loss = abs(price - plan.invalidation) * units + self.cfg.spread * units
        if loss > plan.risk_budget + 0.01:
            return f"order not sent: 1 oz with spread risks {loss:.2f}, more than {plan.risk_budget:.2f}"
        now = datetime.now(timezone.utc).isoformat()
        order = {
            "id": uuid.uuid4().hex[:8],
            "plan_id": plan.id,
            "side": plan.bias,
            "price": round(price, 2),
            "sl": round(plan.invalidation, 2),
            "tp": round(plan.target, 2),
            "units": units,
            "placed_at": now,
        }
        label = "SELL" if plan.bias == "short" else "BUY"
        if kind == "limit":
            self.state["orders"].append(order)
            what = f"{label} LIMIT {units:g} oz at {price:.2f}"
        else:
            self.state["positions"].append({**order, "entry": order["price"], "opened_at": now, "filled_at": now})
            what = f"{label} {units:g} oz at market {price:.2f}"
        self._save()
        return f"paper order sent: {what}, SL {order['sl']:.2f}, TP {order['tp']:.2f}, risk {loss:.2f} USD"

    def deals_summary(self, days: int = 30) -> list[str]:
        closed = self.state.get("closed", [])
        lines = ["PAPER DEMO ACCOUNT"]
        start = self.state["start_balance"]
        lines.append(
            f"Started with {start:.2f} USD. Balance {self.state['balance']:.2f}, equity {self.equity():.2f} "
            f"({self.equity() - start:+.2f})."
        )
        if not closed:
            lines.append("No closed trades yet.")
            return lines
        wins = sum(1 for trade in closed if trade["pnl"] > 0)
        lines.append(f"{len(closed)} closed trades, {wins} winners.")
        for trade in closed[-10:]:
            lines.append(
                f"{trade['closed_at'][:16]}  {trade['side']:<5} {trade['units']:g} oz  "
                f"{trade['entry']:.2f} -> {trade['exit']:.2f}  {trade['reason']:<17} {trade['pnl']:+.2f}"
            )
        return lines

    def _close(self, pos: dict, price: float, reason: str, now: datetime) -> str:
        pnl = self._pnl(pos, price)
        self.state["positions"].remove(pos)
        self.state["balance"] = round(self.state["balance"] + pnl, 2)
        self.state["closed"].append(
            {
                "plan_id": pos["plan_id"],
                "side": pos["side"],
                "units": pos["units"],
                "entry": pos["entry"],
                "exit": round(price, 2),
                "reason": reason,
                "pnl": round(pnl, 2),
                "closed_at": now.isoformat(),
            }
        )
        return f"closed {pos['side']} {pos['plan_id']} at {price:.2f} ({reason}), {pnl:+.2f} USD"

    def _pnl(self, pos: dict, price: float) -> float:
        move = (pos["entry"] - price) if pos["side"] == "short" else (price - pos["entry"])
        return (move - self.cfg.spread) * pos["units"]

    def _refresh_text(self) -> None:
        self.account_text = (
            f"PAPER demo account: balance {self.state['balance']:.2f} USD, equity {self.equity():.2f} USD "
            f"(started {self.state['start_balance']:.2f})"
        )

    def _save(self) -> None:
        if not self.state:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
