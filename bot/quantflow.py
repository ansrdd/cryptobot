"""QuantFlow clone: signal sources for the 5m day trading mode.

Open, textbook versions of what thequantflow.com says its indicator does.
Every value at row t uses only bars <= t (no lookahead), and the engine fills
at bar t+1's open. Settings come from config_daytrade.yaml, locked in
docs/DAYTRADE_SPEC.md before any backtest.

SELL is an exit only (long only, Alpaca has no crypto margin), so exits carry
no trend filter. The SMA 200 filters in the spec are entry filters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, ema

# priority order when several sources fire on the same bar (used for attribution only)
SOURCES = ["ribbon_flip", "macd", "rsi2", "band_reentry"]


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    """Wilder's RSI."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn)
    out[(dn == 0) & up.notna()] = 100.0
    return out


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    prev_ok = a.shift(1).notna() & b.shift(1).notna()
    return prev_ok & (a > b) & (a.shift(1) <= b.shift(1))


def _cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    prev_ok = a.shift(1).notna() & b.shift(1).notna()
    return prev_ok & (a < b) & (a.shift(1) >= b.shift(1))


def signals(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Per bar signals. Columns: buy, sell, source (first source that bought),
    buy_<src> / sell_<src> per source, ribbon_up, atr, paused (outsized candle filter)."""
    c = df["close"]
    src = cfg["sources"]
    out = pd.DataFrame(index=df.index)

    rf = src["ribbon_flip"]
    fast, slow = ema(c, rf["fast_ema"]), ema(c, rf["slow_ema"])
    ribbon_up = (fast > slow) & fast.notna() & slow.notna()
    out["ribbon_up"] = ribbon_up

    buys: dict[str, pd.Series] = {}
    sells: dict[str, pd.Series] = {}

    if rf["enabled"]:
        buys["ribbon_flip"] = _cross_up(fast, slow)
        sells["ribbon_flip"] = _cross_down(fast, slow)

    m = src["macd"]
    if m["enabled"]:
        line = ema(c, m["fast"]) - ema(c, m["slow"])
        sig = ema(line, m["signal"])
        buys["macd"] = _cross_up(line, sig) & ribbon_up
        sells["macd"] = _cross_down(line, sig)

    r = src["rsi2"]
    if r["enabled"]:
        rv = rsi(c, r["period"])
        trend = sma(c, r["trend_sma"])
        buys["rsi2"] = (rv < r["buy_below"]) & (c > trend)
        sells["rsi2"] = rv > r["sell_above"]

    b = src["band_reentry"]
    if b["enabled"]:
        mid = sma(c, b["period"])
        sd = c.rolling(b["period"], min_periods=b["period"]).std(ddof=0)
        lower, upper = mid - b["stdev"] * sd, mid + b["stdev"] * sd
        trend = sma(c, b["trend_sma"])
        buys["band_reentry"] = (c.shift(1) < lower.shift(1)) & (c > lower) & (c > trend)
        sells["band_reentry"] = (c.shift(1) > upper.shift(1)) & (c < upper)

    if src.get("gap_fill", {}).get("enabled"):
        raise NotImplementedError("gap_fill: crypto trades 24/7, there are no gaps (spec says off)")

    false = pd.Series(False, index=df.index)
    for name in SOURCES:
        out[f"buy_{name}"] = buys.get(name, false).fillna(False).astype(bool)
        out[f"sell_{name}"] = sells.get(name, false).fillna(False).astype(bool)
    out["buy"] = out[[f"buy_{n}" for n in SOURCES]].any(axis=1)
    out["sell"] = out[[f"sell_{n}" for n in SOURCES]].any(axis=1)
    first = np.full(len(df), "", dtype=object)
    for name in reversed(SOURCES):  # reversed so the highest priority wins
        first[out[f"buy_{name}"].to_numpy()] = name
    out["source"] = first

    t = cfg["trade"]
    out["atr"] = atr(df, t["atr_period"])

    f = cfg["filters"]["outsized_candle"]
    if f["enabled"]:
        # compare to the ATR *before* this bar so the news candle doesn't dampen its own test
        outsized = (df["high"] - df["low"]) > f["range_atr_mult"] * out["atr"].shift(1)
        # paused on the outsized bar and for pause_bars after it
        out["paused"] = outsized.astype(int).rolling(f["pause_bars"] + 1, min_periods=1).max().astype(bool)
    else:
        out["paused"] = False
    return out
