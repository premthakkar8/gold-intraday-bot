"""Turn a trade idea into a size that fits the account rules.

Risk is measured from the worst fill in the entry zone, so the cash lost at
invalidation never exceeds the budget. If the stop beyond structure is too
wide for the smallest tradable size, the zone is tightened toward the
invalidation. If that still does not fit, there is no trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NORMAL = "normal"
HIGH = "high"


@dataclass
class RiskRules:
    account_usd: float = 100.0
    max_risk_usd: float = 5.0
    max_reward_ratio: float = 4.0
    high_confidence: float = 0.90
    high_confidence_risk_percent: float = 15.0
    point_value_usd: float = 1.0
    min_units: float = 1.0
    unit_step: float = 1.0

    def budget(self, tier: str) -> float:
        if tier == HIGH:
            return self.account_usd * self.high_confidence_risk_percent / 100.0
        return min(self.max_risk_usd, self.account_usd)


@dataclass
class Sizing:
    tier: str
    risk_budget: float
    units: float
    risk_usd: float
    stop_distance: float
    entry_low: float
    entry_high: float
    target: float
    target_far: float
    reward_ratio: float
    far_ratio: float
    tightened: bool


def tier_for(confidence: float, rules: RiskRules) -> str:
    return HIGH if confidence > rules.high_confidence else NORMAL


def size_plan(
    *,
    bias: str,
    entry_low: float,
    entry_high: float,
    invalidation: float,
    target: float,
    target_far: float,
    atr: float,
    confidence: float,
    rules: RiskRules,
) -> Sizing | str:
    """Return a Sizing, or a plain-language reason the idea cannot be traded."""
    tier = tier_for(confidence, rules)
    budget = rules.budget(tier)
    point = rules.point_value_usd if rules.point_value_usd > 0 else 1.0
    min_units = max(rules.min_units, rules.unit_step, 1e-9)
    max_stop = budget / (min_units * point)
    min_stop = 0.25 * max(atr, 0.01)
    tightened = False

    if bias == "short":
        stop = invalidation - entry_low
        if stop > max_stop:
            entry_low = invalidation - max_stop
            tightened = True
            if entry_low > entry_high:
                return _too_wide(invalidation - entry_high, budget, min_units, point)
        stop = invalidation - entry_low
    else:
        stop = entry_high - invalidation
        if stop > max_stop:
            entry_high = invalidation + max_stop
            tightened = True
            if entry_high < entry_low:
                return _too_wide(entry_low - invalidation, budget, min_units, point)
        stop = entry_high - invalidation

    if stop < min_stop:
        return (
            f"the stop would be only {stop:.2f} away, inside normal 5-minute noise "
            f"(ATR {atr:.2f})"
        )

    step = rules.unit_step if rules.unit_step > 0 else min_units
    units = math.floor(budget / (stop * point) / step + 1e-9) * step
    if units < min_units:
        return _too_wide(stop, budget, min_units, point)
    risk_usd = units * stop * point

    mid = (entry_low + entry_high) / 2
    risk_mid = abs(mid - invalidation)
    if tier == NORMAL:
        cap = rules.max_reward_ratio * risk_mid
        if bias == "short":
            target = max(target, mid - cap)
            target_far = max(target_far, mid - cap)
        else:
            target = min(target, mid + cap)
            target_far = min(target_far, mid + cap)

    reward_ratio = abs(target - mid) / risk_mid if risk_mid else 0.0
    far_ratio = abs(target_far - mid) / risk_mid if risk_mid else 0.0
    return Sizing(
        tier=tier,
        risk_budget=budget,
        units=units,
        risk_usd=risk_usd,
        stop_distance=stop,
        entry_low=entry_low,
        entry_high=entry_high,
        target=target,
        target_far=target_far,
        reward_ratio=reward_ratio,
        far_ratio=far_ratio,
        tightened=tightened,
    )


def _too_wide(stop: float, budget: float, min_units: float, point: float) -> str:
    smallest = stop * min_units * point
    return (
        f"the smallest position ({min_units:g} unit) would risk {smallest:.2f} USD to the invalidation, "
        f"more than the {budget:.2f} USD allowed"
    )
