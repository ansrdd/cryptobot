"""Strategies produce *signals*, not orders. Sizing and vetoes live in risk.py.

Every strategy implements:
    signals(df) -> DataFrame with columns:
        entry   (bool)  : open a long at this bar's close
        exit    (bool)  : close any open long at this bar's close
        stop    (float) : initial stop price for a position opened here
        atr     (float) : current ATR (for sizing)
Long-only by design. Shorting crypto on paper teaches you nothing about funding costs
and adds a whole failure class; revisit only after the long book is proven.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind


class Strategy:
    name = "base"

    def __init__(self, params: dict):
        self.p = params

    def warmup(self) -> int:
        """Bars needed before signals are valid."""
        return 250

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError


class Trend(Strategy):
    """EMA crossover confirmed by a Donchian breakout, ATR trailing stop.

    Entry: fast EMA > slow EMA AND close > N-bar prior high.
    Exit:  close < trailing stop (highest close since entry − k·ATR)
           OR fast EMA crosses back below slow EMA.
    The trailing stop is managed in the backtester / runner since it depends on
    position state; here we emit the *initial* stop and a 'trend_off' flag.
    """
    name = "trend"

    def warmup(self) -> int:
        return max(self.p["slow_ema"], self.p["donchian_lookback"], self.p["atr_period"]) * 4

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        c = df["close"]
        fast, slow = ind.ema(c, p["fast_ema"]), ind.ema(c, p["slow_ema"])
        a = ind.atr(df, p["atr_period"])
        dh = ind.donchian_high(c, p["donchian_lookback"])
        up = fast > slow
        entry = up & (c > dh) & ~(up.shift(1, fill_value=False) & (c.shift(1) > dh.shift(1)))
        exit_ = ~up
        out = pd.DataFrame({
            "entry": entry.fillna(False),
            "exit": exit_.fillna(False),
            "stop": c - p["atr_stop_mult"] * a,
            "atr": a,
            "trail_mult": p["atr_stop_mult"],
        }, index=df.index)
        return out


class MeanRev(Strategy):
    """Buy oversold dips *in an uptrend*, sell on reversion. Time + ATR stop.

    Entry: z-score(close, N) < entry_z AND close > trend_filter EMA.
    Exit:  z-score > exit_z, or max_hold_bars elapsed (handled by engine), or stop.
    """
    name = "meanrev"

    def warmup(self) -> int:
        return max(self.p["trend_filter_ema"], self.p["zscore_lookback"]) * 3

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        c = df["close"]
        z = ind.zscore(c, p["zscore_lookback"])
        tf = ind.ema(c, p["trend_filter_ema"])
        a = ind.atr(df, p["atr_period"])
        entry = (z < p["entry_z"]) & (c > tf) & ~(z.shift(1) < p["entry_z"])
        exit_ = z > p["exit_z"]
        out = pd.DataFrame({
            "entry": entry.fillna(False),
            "exit": exit_.fillna(False),
            "stop": c - p["atr_stop_mult"] * a,
            "atr": a,
            "trail_mult": 0.0,              # fixed stop, no trailing for mean-rev
            "max_hold": p["max_hold_bars"],
        }, index=df.index)
        return out


REGISTRY = {"trend": Trend, "meanrev": MeanRev}


def build(cfg) -> list[Strategy]:
    out = []
    for name, params in dict(cfg.strategies).items():
        if params.get("enabled", True) and name in REGISTRY:
            out.append(REGISTRY[name](dict(params)))
    if not out:
        raise ValueError("no strategies enabled in config")
    return out


def regime_series(df: pd.DataFrame, cfg) -> pd.Series:
    """'trending' | 'ranging' per bar, from ADX."""
    if not cfg.regime.enabled:
        return pd.Series("trending", index=df.index)
    a = ind.adx(df, cfg.regime.adx_period)
    return (a > cfg.regime.trending_above).map({True: "trending", False: "ranging"}).fillna("ranging")
