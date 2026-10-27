"""US stock bars from Alpaca market data (SIP feed), regular hours only, cached locally.

Research only (QQQ day trading test). Keys come from the local .env (gitignored),
never from the repo. Cache: data/research/<SYMBOL>_5m_rth.parquet + calendar.parquet,
both gitignored. Prices are split + dividend adjusted so ex-dividend days don't look
like gaps.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import ROOT

log = logging.getLogger(__name__)
CACHE = ROOT / "data" / "research"
NY = "America/New_York"


def _keys() -> tuple[str, str]:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY missing (put them in .env)")
    return k, s


def calendar(start: str, end: str | None = None) -> pd.DataFrame:
    """Market sessions (date, open, close as New York timestamps), incl. half days. Cached."""
    p = CACHE / "calendar.parquet"
    end = end or str((datetime.now(timezone.utc) + timedelta(days=1)).date())
    if p.exists():
        cal = pd.read_parquet(p)
        if str(cal["date"].min().date()) <= start and str(cal["date"].max().date()) >= str(pd.Timestamp(end).date() - timedelta(days=4)):
            return cal
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest
    k, s = _keys()
    rows = TradingClient(k, s, paper=True).get_calendar(
        GetCalendarRequest(start=pd.Timestamp(start).date(), end=pd.Timestamp(end).date()))
    cal = pd.DataFrame({"date": [pd.Timestamp(r.date) for r in rows],
                        "open": [pd.Timestamp(r.open).tz_localize(NY) if pd.Timestamp(r.open).tzinfo is None
                                 else pd.Timestamp(r.open).tz_convert(NY) for r in rows],
                        "close": [pd.Timestamp(r.close).tz_localize(NY) if pd.Timestamp(r.close).tzinfo is None
                                  else pd.Timestamp(r.close).tz_convert(NY) for r in rows]})
    CACHE.mkdir(parents=True, exist_ok=True)
    cal.to_parquet(p)
    return cal


def _fetch(sym: str, start: datetime, end: datetime) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    k, s = _keys()
    c = StockHistoricalDataClient(k, s)
    out = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=31), end)
        r = c.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                                              start=cur, end=nxt, feed=DataFeed.SIP, adjustment=Adjustment.ALL))
        df = r.df
        if len(df):
            df = df.reset_index(level=0, drop=True)[["open", "high", "low", "close", "volume"]]
            out.append(df)
        log.info("%s %s → %s: %d bars", sym, cur.date(), nxt.date(), len(df))
        cur = nxt
    if not out:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.concat(out)
    return df[~df.index.duplicated(keep="last")].sort_index()


def regular_hours(df: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    """Keep bars that START inside a session (open <= start < close). Index stays UTC."""
    if not len(df):
        return df
    ny = df.index.tz_convert(NY)
    sess = cal.set_index(cal["date"].dt.date)
    d = pd.Series(ny.date, index=df.index)
    o = d.map(sess["open"])
    c = d.map(sess["close"])
    keep = o.notna() & (pd.Series(ny, index=df.index) >= o) & (pd.Series(ny, index=df.index) < c)
    return df[keep.to_numpy()]


def load_5m(sym: str, history_start: str) -> pd.DataFrame:
    """Regular hours 5m SIP bars from history_start to ~now (free data needs 15+ min delay)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{sym}_5m_rth.parquet"
    df = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    start = (df.index[-1] + timedelta(minutes=5)).to_pydatetime() if len(df) \
        else pd.Timestamp(history_start, tz="UTC").to_pydatetime()
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    if start < end:
        cal = calendar(history_start)
        new = regular_hours(_fetch(sym, start, end), cal)
        if len(new):
            df = pd.concat([df, new]) if len(df) else new
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.to_parquet(p)
    # drop a session that hasn't closed yet, so its last bar is never mistaken for the close
    if len(df):
        cal = calendar(history_start)
        last_day = df.index[-1].tz_convert(NY).date()
        close = cal.set_index(cal["date"].dt.date)["close"].get(last_day)
        if close is not None and close > pd.Timestamp.now(tz=NY) - timedelta(minutes=20):
            df = df[df.index.tz_convert(NY).date != last_day]
    return df
