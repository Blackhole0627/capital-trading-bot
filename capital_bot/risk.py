"""Position sizing and account-level protection."""
import math
from dataclasses import dataclass
from typing import Optional

from .config import RiskParams


@dataclass
class MarketRules:
    min_size: float = 0.01
    size_step: float = 0.01
    lot_size: float = 1.0           # instrument units per 1.0 of size
    min_stop_pct: Optional[float] = None  # min SL distance in % of price (Capital.com rule)
    max_stop_pct: Optional[float] = None
    decimals: int = 5


def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    n = math.floor(value / step + 1e-9)
    return round(n * step, 8)


def position_size(balance: float, stop_distance: float, risk: RiskParams, rules: MarketRules,
                  value_per_point: Optional[float] = None, price: Optional[float] = None) -> float:
    """Order size according to risk.size_mode.

    notional: position value (price * size * value_per_point) = balance * exposure_pct.
    risk:     (entry -> stop) loses risk_per_trade_pct of balance.
    fixed:    constant fixed_size.
    P&L per unit of size for a 1.0 price move = value_per_point (defaults to lot_size).
    Result is floored to the market's size step, clipped to [min_size, max_size].
    Returns 0.0 if the budget can't afford the minimum size.
    """
    vpp = value_per_point if value_per_point else rules.lot_size
    if risk.size_mode == "fixed":
        size = risk.fixed_size
    elif risk.size_mode == "notional":
        if not price or price <= 0 or vpp <= 0 or balance <= 0:
            return 0.0
        size = balance * risk.exposure_pct / 100.0 / (price * vpp)
    else:
        if stop_distance <= 0 or vpp <= 0 or balance <= 0:
            return 0.0
        risk_amount = balance * risk.risk_per_trade_pct / 100.0
        size = risk_amount / (stop_distance * vpp)
    size = round_step(size, rules.size_step)
    size = min(size, risk.max_size)
    if size < rules.min_size:
        return 0.0
    return size


def clamp_stop_distance(price: float, distance: float, rules: MarketRules) -> float:
    """Widen/narrow the SL/TP distance to fit the broker's min/max % rules."""
    if rules.min_stop_pct:
        distance = max(distance, price * rules.min_stop_pct / 100.0)
    if rules.max_stop_pct:
        distance = min(distance, price * rules.max_stop_pct / 100.0)
    return distance


class AccountGuard:
    """Daily loss limit and max drawdown kill switch."""

    def __init__(self, risk: RiskParams, day_start_balance: float, peak_balance: float):
        self.risk = risk
        self.day_start_balance = day_start_balance
        self.peak_balance = peak_balance
        self.halted_reason: Optional[str] = None

    def new_day(self, balance: float):
        self.day_start_balance = balance
        if self.halted_reason and self.halted_reason.startswith("daily"):
            self.halted_reason = None

    def check(self, balance: float) -> Optional[str]:
        self.peak_balance = max(self.peak_balance, balance)
        if self.day_start_balance > 0:
            day_loss_pct = (self.day_start_balance - balance) / self.day_start_balance * 100.0
            if day_loss_pct >= self.risk.daily_loss_limit_pct:
                self.halted_reason = f"daily loss limit hit ({day_loss_pct:.2f}%)"
        if self.peak_balance > 0:
            dd_pct = (self.peak_balance - balance) / self.peak_balance * 100.0
            if dd_pct >= self.risk.max_drawdown_pct:
                self.halted_reason = f"max drawdown hit ({dd_pct:.2f}%)"
        return self.halted_reason
