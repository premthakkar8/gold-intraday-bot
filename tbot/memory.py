"""Journal of graded session plans.

Recent similar days count more. A lesson loses half its pull after the
configured half-life, so an old approach cannot stay in charge forever.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tbot.chart import prepare

REGIME_KEYS = ("dxy", "oil", "structure", "news", "session")
GRADED = ("win", "loss", "scratch")


@dataclass
class TrackRecord:
    multiplier: float
    sample_weight: float
    wins: int
    losses: int
    scratches: int
    note: str


@dataclass
class Observation:
    id: str
    opened_at: str
    resolved_at: str | None
    horizon_minutes: int
    regime: dict
    style: str
    bias: str
    entry_low: float | None
    entry_high: float | None
    invalidation: float | None
    target: float | None
    price_at_plan: float
    atr: float
    outcome: str | None
    r_multiple: float | None
    tier: str | None = None
    units: float | None = None
    risk_usd: float | None = None
    confidence: float | None = None


class Memory:
    def __init__(self, path: Path | None = None, half_life_days: float = 5.0):
        self.path = Path(path) if path is not None else None
        self.half_life_days = half_life_days
        self.observations: list[Observation] = []

    @classmethod
    def load(cls, path: Path, half_life_days: float) -> "Memory":
        memory = cls(path=path, half_life_days=half_life_days)
        if not path.exists():
            return memory
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {item.name for item in fields(Observation)}
        for row in data.get("observations", []):
            memory.observations.append(Observation(**{key: row[key] for key in known if key in row}))
        return memory

    def open_plan_now(self) -> Observation | None:
        for obs in reversed(self.observations):
            if obs.outcome is None:
                return obs
        return None

    def save(self) -> None:
        if self.path is None:
            return
        if len(self.observations) > 500:
            self.observations = self.observations[-500:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "half_life_days": self.half_life_days,
            "observations": [asdict(item) for item in self.observations],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record_for(self, style: str, regime: dict, now: datetime) -> TrackRecord:
        now = _aware(now)
        weights: list[float] = []
        scores: list[float] = []
        wins = losses = scratches = 0
        for obs in self.observations:
            if obs.style != style or obs.outcome not in GRADED:
                continue
            sim = similarity(obs.regime, regime)
            if sim < 0.6:
                continue
            resolved = _parse_time(obs.resolved_at or obs.opened_at)
            age_days = max(0.0, (now - resolved).total_seconds() / 86400)
            decay = 0.5 ** (age_days / max(self.half_life_days, 0.1))
            weights.append(sim * decay)
            scores.append({"win": 1.0, "loss": -1.0, "scratch": 0.0}[obs.outcome])
            if obs.outcome == "win":
                wins += 1
            elif obs.outcome == "loss":
                losses += 1
            else:
                scratches += 1
        if not weights:
            return TrackRecord(
                1.0,
                0.0,
                0,
                0,
                0,
                "No similar graded sessions yet. This choice is provisional.",
            )
        sample = sum(weights)
        average = sum(weight * score for weight, score in zip(weights, scores)) / sample
        shrunk = average if sample >= 1.5 else average * (sample / 1.5)
        multiplier = min(1.55, max(0.45, 1.0 + 0.55 * shrunk))
        note = (
            f"{wins} wins, {losses} losses, {scratches} scratches on similar days "
            f"(effective weight {sample:.1f}). Trust multiplier {multiplier:.2f}."
        )
        return TrackRecord(multiplier, sample, wins, losses, scratches, note)

    def add_result(
        self,
        *,
        regime: dict,
        style: str,
        outcome: str,
        now: datetime,
        bias: str = "short",
        r_multiple: float | None = None,
    ) -> None:
        now = _aware(now)
        if r_multiple is None:
            r_multiple = {"win": 1.0, "loss": -1.0, "scratch": 0.0}[outcome]
        self.observations.append(
            Observation(
                id=uuid.uuid4().hex[:8],
                opened_at=(now - timedelta(hours=3)).isoformat(),
                resolved_at=now.isoformat(),
                horizon_minutes=120,
                regime=dict(regime),
                style=style,
                bias=bias,
                entry_low=None,
                entry_high=None,
                invalidation=None,
                target=None,
                price_at_plan=0.0,
                atr=1.0,
                outcome=outcome,
                r_multiple=r_multiple,
            )
        )

    def resolve(self, gold: pd.DataFrame, now: datetime) -> list[str]:
        """Grade open plans whose stop, target, or time window has completed."""
        now = _aware(now)
        try:
            bars = prepare(gold)
        except ValueError:
            return []
        notes: list[str] = []
        for obs in self.observations:
            if obs.outcome is not None:
                continue
            graded = _grade(obs, bars, now)
            if graded is None:
                continue
            outcome, r_multiple, detail = graded
            obs.outcome = outcome
            obs.r_multiple = r_multiple
            obs.resolved_at = now.isoformat()
            notes.append(detail)
        return notes

    def replace_open(self, now: datetime) -> int:
        now = _aware(now)
        count = 0
        for obs in self.observations:
            if obs.outcome is None:
                obs.outcome = "replaced"
                obs.resolved_at = now.isoformat()
                obs.r_multiple = None
                count += 1
        return count

    def open_plan(self, plan, horizon_minutes: int, now: datetime) -> None:
        now = _aware(now)
        self.observations.append(
            Observation(
                id=plan.id,
                opened_at=now.isoformat(),
                resolved_at=None,
                horizon_minutes=horizon_minutes,
                regime=dict(plan.regime),
                style=plan.style,
                bias=plan.bias,
                entry_low=plan.entry_low,
                entry_high=plan.entry_high,
                invalidation=plan.invalidation,
                target=plan.target,
                price_at_plan=plan.price,
                atr=plan.atr,
                outcome=None,
                r_multiple=None,
                tier=plan.tier if plan.bias != "flat" else None,
                units=plan.units,
                risk_usd=plan.risk_usd if plan.bias != "flat" else None,
                confidence=plan.confidence,
            )
        )

    def summary(self, now: datetime | None = None) -> str:
        now = _aware(now or datetime.now(timezone.utc))
        graded = [obs for obs in self.observations if obs.outcome in GRADED]
        lines = [
            "JOURNAL",
            f"Half-life {self.half_life_days:.0f} days. A lesson from a month ago barely moves the next plan.",
            f"Graded plans: {len(graded)}. Open: {sum(obs.outcome is None for obs in self.observations)}.",
        ]
        if not graded:
            lines.append(
                "No graded plans yet. Run a brief, let the stop, target, or time window finish, then run it again."
            )
            return "\n".join(lines)
        lines.append("")
        styles: dict[str, list[Observation]] = {}
        for obs in graded:
            styles.setdefault(obs.style, []).append(obs)
        for style, rows in sorted(styles.items()):
            wins = sum(item.outcome == "win" for item in rows)
            losses = sum(item.outcome == "loss" for item in rows)
            scratches = sum(item.outcome == "scratch" for item in rows)
            rs = [item.r_multiple for item in rows if item.r_multiple is not None]
            avg = sum(rs) / len(rs) if rs else 0.0
            lines.append(f"{style:<16} {wins}W {losses}L {scratches}S   avg R {avg:+.2f}")
        sized = [
            obs for obs in graded if obs.bias != "flat" and obs.risk_usd is not None and obs.r_multiple is not None
        ]
        if sized:
            paper = sum(obs.r_multiple * obs.risk_usd for obs in sized)
            lines.append("")
            lines.append(
                f"Paper result on {len(sized)} sized trade plans: {paper:+.2f} USD "
                "(R multiple x dollars at risk, assuming the entry zone filled and no slippage)."
            )
        lines.append("")
        lines.append("Latest graded:")
        ordered = sorted(graded, key=lambda item: item.resolved_at or "", reverse=True)[:8]
        for obs in ordered:
            regime = " ".join(f"{key}={obs.regime.get(key)}" for key in REGIME_KEYS)
            r_text = "n/a" if obs.r_multiple is None else f"{obs.r_multiple:+.2f}R"
            lines.append(
                f"{obs.resolved_at}  {obs.bias:<5} {obs.style:<14} {obs.outcome:<8} {r_text:<8} {regime}"
            )
        lines.append("")
        lines.append("Re-running before the window ends replaces the open plan and does not grade it.")
        return "\n".join(lines)


def similarity(left: dict, right: dict) -> float:
    matches = sum(1 for key in REGIME_KEYS if left.get(key) == right.get(key))
    return matches / len(REGIME_KEYS)


def _grade(obs: Observation, bars: pd.DataFrame, now: datetime) -> tuple[str, float, str] | None:
    start = _parse_time(obs.opened_at)
    end = start + timedelta(minutes=obs.horizon_minutes)
    window = bars[(bars["ts"] > start) & (bars["ts"] <= end)]
    if window.empty:
        if now >= end:
            return "scratch", 0.0, f"{obs.id} {obs.style} -> scratch (no bars in the window)"
        return None
    still_open = _window_still_open(bars, end, now)
    if obs.bias == "flat" or obs.style == "stand_aside":
        outcome, r_multiple = _grade_flat(obs, window)
        # A clean trend means standing aside already failed. Quiet tape can still turn, so wait.
        if outcome != "loss" and still_open:
            return None
        return outcome, r_multiple, f"{obs.id} stand aside -> {outcome} ({r_multiple:+.2f}R)"
    if obs.invalidation is None or obs.target is None or obs.entry_low is None or obs.entry_high is None:
        if now >= end:
            return "scratch", 0.0, f"{obs.id} {obs.style} -> scratch (plan had no levels)"
        return None

    entry = (obs.entry_low + obs.entry_high) / 2
    risk = abs(entry - obs.invalidation)
    filled = False
    for row in window.itertuples(index=False):
        if obs.bias == "short":
            if not filled:
                if row.high < obs.entry_low:
                    continue
                filled = True
                if row.high >= obs.invalidation:
                    return "loss", -1.0, f"{obs.id} {obs.bias} {obs.style} -> loss (-1.00R) invalidation hit"
                continue
            stopped = row.high >= obs.invalidation
            targeted = row.low <= obs.target
        else:
            if not filled:
                if row.low > obs.entry_high:
                    continue
                filled = True
                if row.low <= obs.invalidation:
                    return "loss", -1.0, f"{obs.id} {obs.bias} {obs.style} -> loss (-1.00R) invalidation hit"
                continue
            stopped = row.low <= obs.invalidation
            targeted = row.high >= obs.target
        if stopped:
            return "loss", -1.0, f"{obs.id} {obs.bias} {obs.style} -> loss (-1.00R) invalidation hit"
        if targeted:
            reward = abs(entry - obs.target)
            r_multiple = reward / risk if risk else 1.0
            return "win", r_multiple, f"{obs.id} {obs.bias} {obs.style} -> win ({r_multiple:+.2f}R) target hit"

    if not filled:
        if still_open:
            return None
        return "scratch", 0.0, f"{obs.id} {obs.bias} {obs.style} -> scratch (price never reached the entry zone)"

    if still_open:
        return None
    last = float(window.iloc[-1]["close"])
    if risk <= 0:
        return "scratch", 0.0, f"{obs.id} {obs.style} -> scratch"
    r_multiple = (entry - last) / risk if obs.bias == "short" else (last - entry) / risk
    if r_multiple >= 0.35:
        outcome = "win"
    elif r_multiple <= -0.35:
        outcome = "loss"
    else:
        outcome = "scratch"
    return outcome, r_multiple, f"{obs.id} {obs.bias} {obs.style} -> {outcome} ({r_multiple:+.2f}R) at the horizon"


def _window_still_open(bars: pd.DataFrame, end: datetime, now: datetime) -> bool:
    last = bars["ts"].max().to_pydatetime()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    else:
        last = last.astimezone(timezone.utc)
    return now < end and last < end


def _grade_flat(obs: Observation, window: pd.DataFrame) -> tuple[str, float]:
    price = obs.price_at_plan
    atr = obs.atr or 1.0
    up = max(float(window["high"].max()) - price, 0.0)
    down = max(price - float(window["low"].min()), 0.0)
    clean_trend = (up > atr and down < 0.4 * atr) or (down > atr and up < 0.4 * atr)
    quiet = up < 0.45 * atr and down < 0.45 * atr
    chop = up > 0.5 * atr and down > 0.5 * atr
    if clean_trend:
        return "loss", -1.0
    if quiet or chop:
        return "win", 1.0
    return "scratch", 0.0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _aware(parsed)
