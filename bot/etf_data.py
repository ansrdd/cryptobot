"""Daily ETF bars for the Global Trend spec (docs/GLOBAL_TREND_SPEC.md). Research only.

Yahoo Finance (free, full history, split + dividend adjusted OHLC) is the backtest source.
Alpaca (SIP, 2016+, Adjustment.ALL) is only used to sanity check Yahoo's returns.
Cache: data/research/etf_daily_<source>.parquet (gitignored).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from .stocks_data import CACHE, _keys

log = logging.getLogger(__name__)


def _stack(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """{sym: OHLC frame} → one frame with (field, sym) columns, index = session date (naive)."""
    return pd.concat(frames, axis=1).swaplevel(0, 1, axis=1).sort_index(axis=1)


def load_yahoo(symbols: list[str], refresh: bool = False) -> pd.DataFrame:
    p = CACHE / "etf_daily_yahoo.parquet"
    if p.exists() and not refresh:
        df = pd.read_parquet(p)
        if set(symbols) <= set(df.columns.get_level_values(1)) and \
                df.index[-1] >= pd.Timestamp.now().normalize() - pd.offsets.BDay(3):
            return df
    import yfinance as yf
    out = {}
    for s in symbols:
        d = yf.Ticker(s).history(period="max", auto_adjust=True, actions=False)
        d.index = pd.DatetimeIndex(d.index.tz_localize(None).normalize())
        out[s] = d[["Open", "High", "Low", "Close"]].rename(columns=str.lower)
        log.info("yahoo %s: %d days from %s", s, len(d), d.index[0].date())
    df = _stack(out)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)
    return df


def load_alpaca(symbols: list[str], start: str = "2016-01-01") -> pd.DataFrame:
    p = CACHE / "etf_daily_alpaca.parquet"
    if p.exists():
        df = pd.read_parquet(p)
        if set(symbols) <= set(df.columns.get_level_values(1)):
            return df
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    k, s = _keys()
    c = StockHistoricalDataClient(k, s)
    out = {}
    for sym in symbols:
        d = c.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                                              start=pd.Timestamp(start, tz="UTC").to_pydatetime(),
                                              end=datetime.now(timezone.utc) - timedelta(minutes=20), feed=DataFeed.SIP,
                                              adjustment=Adjustment.ALL)).df.reset_index(level=0, drop=True)
        d.index = pd.DatetimeIndex(d.index.tz_convert("America/New_York").tz_localize(None).normalize())
        out[sym] = d[["open", "high", "low", "close"]]
    df = _stack(out)
    df.to_parquet(p)
    return df


def compare(yahoo: pd.DataFrame, alpaca: pd.DataFrame, max_diff_pct: float, max_days_pct: float) -> pd.DataFrame:
    """Per symbol: share of common days where daily close to close returns differ by more than max_diff_pct."""
    rows = []
    for s in sorted(set(alpaca.columns.get_level_values(1))):
        y, a = yahoo["close"][s].dropna(), alpaca["close"][s].dropna()
        idx = y.index.intersection(a.index)
        ry, ra = y.loc[idx].pct_change().dropna(), a.loc[idx].pct_change().dropna()
        diff = (ry - ra).abs() * 100
        bad = 100 * (diff > max_diff_pct).mean()
        rows.append({"symbol": s, "days": len(diff), "pct_days_over": round(bad, 3),
                     "max_diff_pct": round(diff.max(), 3), "ok": bad <= max_days_pct})
    return pd.DataFrame(rows).set_index("symbol")
