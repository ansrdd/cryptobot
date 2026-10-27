"""Position sizing and portfolio-level circuit breakers.

This module is deliberately boring. It is the thing standing between a bad week
and a blown account, and it must behave identically in backtest and paper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RiskState:
    peak_equity: float
    day: date | None = None
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    halved_trades_left: int = 0
    killed: bool = False
    halted_for_day: bool = False
    kill_reason: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["day"] = self.day.isoformat() if self.day else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RiskState":
        d = dict(d)
        if d.get("day"):
            d["day"] = date.fromisoformat(d["day"])
        return cls(**d)


class RiskManager:
    def __init__(self, cfg, equity: float, state: RiskState | None = None):
        self.cfg = cfg.risk
        self.state = state or RiskState(peak_equity=equity, day_start_equity=equity)

    # ------------------------------------------------------------- bookkeeping
    def on_bar(self, ts, equity: float) -> None:
        """Call once per bar before evaluating signals."""
        s = self.state
        d = ts.date()
        if s.day != d:
            s.day = d
            s.day_start_equity = equity
            s.halted_for_day = False
        s.peak_equity = max(s.peak_equity, equity)

        dd_pct = 100 * (1 - equity / s.peak_equity) if s.peak_equity > 0 else 0
        if dd_pct >= self.cfg.max_drawdown_kill_pct and not s.killed:
            s.killed = True
            s.kill_reason = f"max drawdown {dd_pct:.1f}% ≥ {self.cfg.max_drawdown_kill_pct}%"

        day_loss_pct = 100 * (1 - equity / s.day_start_equity) if s.day_start_equity > 0 else 0
        if day_loss_pct >= self.cfg.daily_loss_limit_pct:
            s.halted_for_day = True

    def on_trade_closed(self, pnl: float) -> None:
        s = self.state
        if pnl < 0:
            s.consecutive_losses += 1
            if s.consecutive_losses >= self.cfg.loss_streak_halve_after:
                s.halved_trades_left = self.cfg.loss_streak_halve_for
        else:
            s.consecutive_losses = 0
        if s.halved_trades_left > 0 and pnl != 0:
            s.halved_trades_left -= 1

    # ------------------------------------------------------------------ gates
    def can_open(self, n_open: int, gross_exposure: float, equity: float) -> tuple[bool, str]:
        s = self.state
        if s.killed:
            return False, "killed"
        if s.halted_for_day:
            return False, "daily_loss_limit"
        if n_open >= self.cfg.max_concurrent_positions:
            return False, "max_positions"
        if equity > 0 and 100 * gross_exposure / equity >= self.cfg.max_gross_exposure_pct:
            return False, "max_gross_exposure"
        return True, ""

    def must_flatten(self) -> bool:
        return self.state.killed or self.state.halted_for_day

    # ----------------------------------------------------------------- sizing
    def size(self, equity: float, price: float, stop: float, weight: float = 1.0,
             gross_exposure: float = 0.0) -> float:
        """Units to buy so that (price - stop) * units == risk budget.

        weight: regime allocation for this strategy (0..1).
        Returns 0 if the stop is nonsensical.
        """
        dist = price - stop
        if dist <= 0 or price <= 0:
            return 0.0
        risk_pct = self.cfg.risk_per_trade_pct * weight
        if self.state.halved_trades_left > 0:
            risk_pct *= 0.5
        risk_dollars = equity * risk_pct / 100
        units = risk_dollars / dist
        # cap by max position size
        max_notional = equity * self.cfg.max_position_pct / 100
        units = min(units, max_notional / price)
        # cap by remaining gross exposure
        room = equity * self.cfg.max_gross_exposure_pct / 100 - gross_exposure
        units = min(units, max(room, 0) / price)
        return max(units, 0.0)
