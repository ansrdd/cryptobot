"""Historical + incremental hourly bar data with a local parquet cache.

Sources:
  - binance: free public klines, no API key, hourly history back to 2017.
             Used for backtests and strategy warm-up.
  - alpaca:  used for the *latest* bars during paper runs so signals line up
             with the venue we actually trade on (optional; falls back to binance).

Cache layout: data/<SYMBOL>_1h.parquet  (UTC index, columns open high low close volume)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

BINANCE_URL = "https://api.binance.com/api/v3/klines"
BINANCE_MAX = 1000  # bars per request

TF_TO_BINANCE = {"1h": "1h", "4h": "4h", "1d": "1d"}
TF_TO_TIMEDELTA = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}


def _binance_symbol(sym: str) -> str:
    # "BTC/USD" -> "BTCUSDT" (USDT is the liquid quote; price ≈ USD)
    base, _ = sym.split("/")
    return f"{base}USDT"


def _cache_path(cache_dir: Path, sym: str, tf: str) -> Path:
    return cache_dir / f"{sym.replace('/', '')}_{tf}.parquet"


def _fetch_binance(sym: str, tf: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
    end = end or datetime.now(timezone.utc)
    bsym = _binance_symbol(sym)
    interval = TF_TO_BINANCE[tf]
    rows: list[list] = []
    cur = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    sess = requests.Session()
    while cur < end_ms:
        params = {"symbol": bsym, "interval": interval, "startTime": cur, "limit": BINANCE_MAX}
        r = sess.get(BINANCE_URL, params=params, timeout=30)
        if r.status_code == 429:
            log.warning("binance rate limit, sleeping")
            time.sleep(10)
            continue
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + 1
        if len(batch) < BINANCE_MAX:
            break
        time.sleep(0.15)  # be polite; ~1200 weight/min limit is generous
    if not rows:
        return _empty()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df


COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
COINBASE_MAX = 300
TF_TO_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400}


def _coinbase_product(sym: str) -> str:
    return sym.replace("/", "-")  # "BTC/USD" -> "BTC-USD"


def _fetch_coinbase(sym: str, tf: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
    """Coinbase Exchange public candles. Works from US IPs (GitHub runners). 300 bars/request."""
    end = end or datetime.now(timezone.utc)
    gran = TF_TO_SECONDS[tf]
    step = timedelta(seconds=gran * COINBASE_MAX)
    sess = requests.Session()
    sess.headers["User-Agent"] = "cryptobot-research/0.1"
    rows: list[list] = []
    cur = start
    while cur < end:
        chunk_end = min(cur + step, end)
        params = {"granularity": gran, "start": cur.isoformat(), "end": chunk_end.isoformat()}
        r = sess.get(COINBASE_URL.format(product=_coinbase_product(sym)), params=params, timeout=30)
        if r.status_code == 429:
            log.warning("coinbase rate limit, sleeping")
            time.sleep(2)
            continue
        r.raise_for_status()
        batch = r.json()
        if isinstance(batch, list):
            rows.extend(batch)
        cur = chunk_end
        time.sleep(0.12)  # ~8 req/s, under the 10/s public limit
    if not rows:
        return _empty()
    # coinbase: [time, low, high, open, close, volume]
    df = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
    return df[["open", "high", "low", "close", "volume"]]


FETCHERS = {"coinbase": _fetch_coinbase, "binance": _fetch_binance}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                        index=pd.DatetimeIndex([], tz="UTC", name="ts"))


def load_cached(cache_dir: Path, sym: str, tf: str) -> pd.DataFrame:
    p = _cache_path(cache_dir, sym, tf)
    if p.exists():
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return _empty()


def update(cache_dir: str | Path, sym: str, tf: str = "1h",
           history_start: str = "2019-01-01", source: str = "binance") -> pd.DataFrame:
    """Return full cached history for sym, fetching anything missing first.

    Drops the still-forming final bar so strategies never act on a partial candle.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    df = load_cached(cache_dir, sym, tf)
    start = (df.index[-1] + TF_TO_TIMEDELTA[tf]) if len(df) else pd.Timestamp(history_start, tz="UTC")
    now = datetime.now(timezone.utc)
    if start < now:
        log.info("fetching %s %s from %s via %s", sym, tf, start, source)
        order = [source] + [s for s in FETCHERS if s != source]
        new, last_err = _empty(), None
        for src in order:
            try:
                new = FETCHERS[src](sym, tf, start.to_pydatetime(), now)
                if len(new):
                    if src != source:
                        log.warning("%s failed, used %s instead", source, src)
                    break
            except Exception as e:  # noqa: BLE001 — try the next source
                last_err = e
                log.warning("%s fetch failed for %s: %s", src, sym, e)
        if not len(new) and not len(df):
            raise RuntimeError(f"no data source worked for {sym}: {last_err}")
        if len(new):
            df = pd.concat([df, new])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.to_parquet(_cache_path(cache_dir, sym, tf))
    # drop incomplete current bar
    if len(df):
        bar_end = df.index[-1] + TF_TO_TIMEDELTA[tf]
        if bar_end > pd.Timestamp(now):
            df = df.iloc[:-1]
    return df


def load_universe(cfg) -> dict[str, pd.DataFrame]:
    import os
    if os.environ.get("CRYPTOBOT_SYNTHETIC"):  # offline testing only
        return {s: synthetic(s, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000)
                for i, s in enumerate(cfg.universe.symbols)}
    out = {}
    for sym in cfg.universe.symbols:
        out[sym] = update(cfg.data.cache_dir, sym, cfg.universe.timeframe,
                          cfg.data.history_start, cfg.data.source)
    return out


def is_stale(df: pd.DataFrame, tf: str, tolerance_bars: int = 3) -> bool:
    if not len(df):
        return True
    age = datetime.now(timezone.utc) - df.index[-1].to_pydatetime()
    return age > TF_TO_TIMEDELTA[tf] * tolerance_bars


# ---------------------------------------------------------------- synthetic
def synthetic(sym: str = "BTC/USD", bars: int = 24 * 365 * 3, seed: int = 7,
              start_price: float = 30000.0) -> pd.DataFrame:
    """Regime-switching GBM with vol clustering. For tests only — NOT for conclusions.

    Alternates trending / ranging regimes so both strategies get exercised.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0),
                        periods=bars, freq="1h", tz="UTC")
    drift = np.zeros(bars)
    vol = np.zeros(bars)
    i = 0
    while i < bars:
        length = int(rng.integers(24 * 10, 24 * 60))
        regime = rng.choice(["up", "down", "range"], p=[0.35, 0.25, 0.40])
        mu = {"up": 0.00035, "down": -0.00025, "range": 0.0}[regime]
        sig = {"up": 0.0075, "down": 0.0105, "range": 0.0055}[regime]
        drift[i:i + length] = mu
        vol[i:i + length] = sig
        i += length
    # GARCH-ish vol clustering
    eps = rng.standard_normal(bars)
    h = vol.copy()
    for t in range(1, bars):
        h[t] = 0.85 * h[t - 1] + 0.15 * vol[t] * (1 + 0.5 * abs(eps[t - 1]))
    ret = drift + h * eps
    close = start_price * np.exp(np.cumsum(ret))
    o = np.r_[start_price, close[:-1]]
    spread = np.abs(rng.standard_normal(bars)) * h * close * 0.6
    hi = np.maximum(o, close) + spread
    lo = np.minimum(o, close) - spread
    v = rng.lognormal(mean=6, sigma=0.5, size=bars) * (1 + 3 * np.abs(ret) / (h + 1e-9))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": close, "volume": v}, index=idx)
