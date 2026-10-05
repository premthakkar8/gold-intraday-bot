"""Load paths, symbols, and risk settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tbot.broker_mt5 import BrokerSettings, load_env
from tbot.paper import PaperSettings
from tbot.risk import RiskRules

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    gold_symbol: str = "XAUUSD=X"
    dxy_symbol: str = "DX-Y.NYB"
    oil_symbol: str = "CL=F"
    interval: str = "5m"
    period: str = "5d"
    horizon_minutes: int = 120
    risk: RiskRules = field(default_factory=RiskRules)
    news_lookback_hours: int = 18
    half_life_days: float = 5.0
    memory_path: Path = ROOT / "data" / "memory.json"
    report_dir: Path = ROOT / "reports"
    log_path: Path = ROOT / "reports" / "auto.log"
    notify: bool = True
    alerts_only: bool = False
    broker: BrokerSettings = field(default_factory=BrokerSettings)
    paper: PaperSettings = field(default_factory=PaperSettings)
    site_dir: Path = ROOT / "docs"
    calendar_cache: Path = ROOT / "data" / "calendar.json"
    calendar_refresh_minutes: int = 60
    blackout_before_minutes: int = 30
    blackout_after_minutes: int = 15


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_settings(path: Path | None = None) -> Settings:
    file_path = path or (ROOT / "config.yaml")
    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    symbols = raw.get("symbols") or {}
    market = raw.get("market") or {}
    session = raw.get("session") or {}
    risk = raw.get("risk") or {}
    news = raw.get("news") or {}
    journal = raw.get("journal") or {}
    reports = raw.get("reports") or {}
    auto = raw.get("auto") or {}
    broker = raw.get("broker") or {}
    calendar = raw.get("calendar") or {}
    load_env(ROOT / ".env")
    defaults = Settings()
    login = os.environ.get("TBOT_MT5_LOGIN", "").strip()
    paper = raw.get("paper") or {}
    broker_settings = BrokerSettings(
        enabled=bool(broker.get("enabled", False)),
        platform=(os.environ.get("TBOT_BROKER") or str(broker.get("platform", "paper"))).strip().lower(),
        symbol=str(broker.get("symbol", "XAUUSD")),
        terminal_path=str(broker.get("terminal_path", "") or ""),
        demo_only=bool(broker.get("demo_only", True)),
        deviation_points=int(broker.get("deviation_points", 50)),
        login=int(login) if login.isdigit() else None,
        password=os.environ.get("TBOT_MT5_PASSWORD") or None,
        server=os.environ.get("TBOT_MT5_SERVER") or None,
    )
    base = RiskRules()
    rules = RiskRules(
        account_usd=float(risk.get("account_usd", base.account_usd)),
        max_risk_usd=float(risk.get("max_risk_usd", base.max_risk_usd)),
        max_reward_ratio=float(risk.get("max_reward_ratio", base.max_reward_ratio)),
        high_confidence=float(risk.get("high_confidence", base.high_confidence)),
        high_confidence_risk_percent=float(
            risk.get("high_confidence_risk_percent", base.high_confidence_risk_percent)
        ),
        point_value_usd=float(risk.get("point_value_usd", base.point_value_usd)),
        min_units=float(risk.get("min_units", base.min_units)),
        unit_step=float(risk.get("unit_step", base.unit_step)),
    )
    report_dir = _resolve(reports.get("dir", defaults.report_dir))
    return Settings(
        gold_symbol=str(symbols.get("gold", defaults.gold_symbol)),
        dxy_symbol=str(symbols.get("dxy", defaults.dxy_symbol)),
        oil_symbol=str(symbols.get("oil", defaults.oil_symbol)),
        interval=str(market.get("interval", defaults.interval)),
        period=str(market.get("period", defaults.period)),
        horizon_minutes=int(session.get("horizon_minutes", defaults.horizon_minutes)),
        risk=rules,
        news_lookback_hours=int(news.get("lookback_hours", defaults.news_lookback_hours)),
        half_life_days=float(journal.get("half_life_days", defaults.half_life_days)),
        memory_path=_resolve(journal.get("path", defaults.memory_path)),
        report_dir=report_dir,
        log_path=_resolve(auto.get("log", report_dir / "auto.log")),
        notify=bool(auto.get("notify", defaults.notify)),
        alerts_only=(os.environ.get("TBOT_MODE") or str(auto.get("mode", ""))).strip().lower() == "alert",
        broker=broker_settings,
        paper=PaperSettings(
            start_balance=float(paper.get("start_balance", 100.0)),
            spread=float(paper.get("spread", 0.30)),
        ),
        calendar_refresh_minutes=int(calendar.get("refresh_minutes", defaults.calendar_refresh_minutes)),
        blackout_before_minutes=int(calendar.get("blackout_before_minutes", defaults.blackout_before_minutes)),
        blackout_after_minutes=int(calendar.get("blackout_after_minutes", defaults.blackout_after_minutes)),
    )
