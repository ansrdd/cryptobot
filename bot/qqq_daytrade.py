"""QuantFlow clone on QQQ 5m, regular hours, long AND short. Spec: docs/QQQ_DAYTRADE_SPEC.md.

Differences from the crypto clone (bot/quantflow.py + bot/daytrade.py):
  - every source has a mirrored SELL that can open a short (stop and reverse)
  - gap fill source: first bar of the session vs the prior session's close
  - no new entries if the fill bar starts at/after 15:30 New York; flat at the
    close of each session's last bar (half days included)
  - outsized candle pause is 90 minutes of clock time, so it never carries overnight

Signals on a closed bar fill at the next bar's open. Bars are regular hours only
(bot/stocks_data.py); session first/last bars come from the calendar filtered data.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import ROOT
from .daytrade import Result, metrics, verdict
from .indicators import atr, ema
from .quantflow import _cross_down, _cross_up, rsi, sma

SOURCES = ["ribbon_flip", "macd", "rsi2", "band_reentry", "gap_fill"]
NY = "America/New_York"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config_qqq_daytrade.yaml") as f:
        return yaml.safe_load(f)


@dataclass
class Trade:
    symbol: str
    side: str            # long | short
    source: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float   # fill incl. costs
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    reason: str          # target | stop | breakeven | reverse | close | end
    bars_held: int


def session_info(df: pd.DataFrame) -> pd.DataFrame:
    """Per bar: New York session date, first / last bar of its session, New York clock time."""
    ny = df.index.tz_convert(NY)
    d = pd.Series(ny.date, index=df.index)
    out = pd.DataFrame(index=df.index)
    out["date"] = d
    out["first"] = d.ne(d.shift(1))
    out["last"] = d.ne(d.shift(-1))
    out["minute"] = ny.hour * 60 + ny.minute
    return out


def signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """+1 buy / -1 sell / 0 per bar, plus source, atr, paused. Row t uses only bars <= t."""
    c = df["close"]
    src = cfg["sources"]
    out = pd.DataFrame(index=df.index)
    sess = session_info(df)

    rf = src["ribbon_flip"]
    fast, slow = ema(c, rf["fast_ema"]), ema(c, rf["slow_ema"])
    up = (fast > slow) & fast.notna() & slow.notna()
    down = (fast < slow) & fast.notna() & slow.notna()
    buys, sells = {}, {}
    if rf["enabled"]:
        buys["ribbon_flip"], sells["ribbon_flip"] = _cross_up(fast, slow), _cross_down(fast, slow)
    m = src["macd"]
    if m["enabled"]:
        line = ema(c, m["fast"]) - ema(c, m["slow"])
        sig = ema(line, m["signal"])
        buys["macd"], sells["macd"] = _cross_up(line, sig) & up, _cross_down(line, sig) & down
    r = src["rsi2"]
    if r["enabled"]:
        rv, trend = rsi(c, r["period"]), sma(c, r["trend_sma"])
        buys["rsi2"] = (rv < r["buy_below"]) & (c > trend)
        sells["rsi2"] = (rv > r["sell_above"]) & (c < trend)
    b = src["band_reentry"]
    if b["enabled"]:
        mid = sma(c, b["period"])
        sd = c.rolling(b["period"], min_periods=b["period"]).std(ddof=0)
        lower, upper = mid - b["stdev"] * sd, mid + b["stdev"] * sd
        trend = sma(c, b["trend_sma"])
        buys["band_reentry"] = (c.shift(1) < lower.shift(1)) & (c > lower) & (c > trend)
        sells["band_reentry"] = (c.shift(1) > upper.shift(1)) & (c < upper) & (c < trend)
    g = src["gap_fill"]
    if g["enabled"]:
        # prior session's close = close of the bar before a session's first bar
        gap = df["open"] / c.shift(1) - 1
        first = sess["first"] & c.shift(1).notna()
        buys["gap_fill"] = first & (gap <= -g["min_gap_pct"] / 100)
        sells["gap_fill"] = first & (gap >= g["min_gap_pct"] / 100)

    false = pd.Series(False, index=df.index)
    for n in SOURCES:
        out[f"buy_{n}"] = buys.get(n, false).fillna(False).astype(bool)
        out[f"sell_{n}"] = sells.get(n, false).fillna(False).astype(bool)
    anyb = out[[f"buy_{n}" for n in SOURCES]].any(axis=1)
    anys = out[[f"sell_{n}" for n in SOURCES]].any(axis=1)
    out["signal"] = np.where(anyb & ~anys, 1, np.where(anys & ~anyb, -1, 0))  # both = cancel
    src_name = np.full(len(df), "", dtype=object)
    for n in reversed(SOURCES):
        src_name[(out[f"buy_{n}"] & (out["signal"] == 1)).to_numpy()] = n
        src_name[(out[f"sell_{n}"] & (out["signal"] == -1)).to_numpy()] = n
    out["source"] = src_name

    t = cfg["trade"]
    out["atr"] = atr(df, t["atr_period"])
    f = cfg["filters"]["outsized_candle"]
    if f["enabled"]:
        outsized = (df["high"] - df["low"]) > f["range_atr_mult"] * out["atr"].shift(1)
        last_big = pd.Series(df.index.where(outsized.to_numpy()), index=df.index).ffill()
        out["paused"] = (df.index.to_series() - last_big) <= pd.Timedelta(minutes=5 * f["pause_bars"])
    else:
        out["paused"] = False
    return out


def run(cfg: dict, bars: pd.DataFrame, start: str | None = None, end: str | None = None,
        fee_bps: float | None = None, slippage_bps: float | None = None,
        long_short: bool | None = None, symbol: str = "QQQ") -> Result:
    fee = cfg["costs"]["fee_bps"] if fee_bps is None else fee_bps
    slip = cfg["costs"]["slippage_bps"] if slippage_bps is None else slippage_bps
    cost = (fee + slip) / 10_000
    t, rk = cfg["trade"], cfg["risk"]
    shorts = t["long_short"] if long_short is None else long_short
    stop_mult, target_r, be_r = t["stop_atr_mult"], t["target_r"], t["breakeven_at_r"]
    hh, mm = map(int, t["no_entries_after"].split(":"))
    cutoff_min = hh * 60 + mm
    risk_frac, max_pos = rk["risk_per_trade_pct"] / 100, rk["max_position_pct"] / 100
    day_limit, kill_dd = rk["daily_loss_limit_pct"] / 100, rk["max_drawdown_kill_pct"] / 100

    sig = signals(bars, cfg)
    sess = session_info(bars)
    idx = bars.index
    lo = np.searchsorted(idx, pd.Timestamp(start, tz="UTC")) if start else 0
    hi = np.searchsorted(idx, pd.Timestamp(end, tz="UTC")) if end else len(idx)
    if hi - lo < 2:
        raise ValueError("not enough bars in the window")
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    s_sig, s_src = sig["signal"].to_numpy(int), sig["source"].to_numpy(object)
    s_atr, s_pause = sig["atr"].to_numpy(float), sig["paused"].to_numpy(bool)
    first, last = sess["first"].to_numpy(bool), sess["last"].to_numpy(bool)
    minute, dates = sess["minute"].to_numpy(int), sess["date"].to_numpy(object)

    equity0 = float(cfg["account"]["starting_equity"])
    cash = equity = peak = day_start_eq = equity0
    pos = None          # dict(side=+1/-1, units, fill, entry, stop, target, be_level, be, ts, i, source)
    pending = None      # dict(exit=bool, enter=+1/-1/0, atr, equity, source)
    trades: list[Trade] = []
    eq = np.empty(hi - lo)
    killed_at, day, halted, halt_days = None, None, False, 0

    def close_pos(i: int, raw_px: float, reason: str):
        nonlocal cash, pos
        p = pos
        if p["side"] > 0:
            px = raw_px * (1 - cost)
            cash += p["units"] * px
            pnl = p["units"] * (px - p["fill"])
        else:
            px = raw_px * (1 + cost)
            cash -= p["units"] * px
            pnl = p["units"] * (p["fill"] - px)
        trades.append(Trade(symbol, "long" if p["side"] > 0 else "short", p["source"], p["ts"], idx[i], p["fill"], px,
                            p["units"], pnl, 100 * pnl / (p["units"] * p["fill"]), reason, i - p["i"] + 1))
        pos = None

    for k, i in enumerate(range(lo, hi)):
        if dates[i] != day:
            day, day_start_eq, halted = dates[i], equity, False

        # 1. fill pending at this bar's open (pending never crosses a session: cleared at the close)
        if pending is not None:
            if pending["exit"] and pos is not None:
                close_pos(i, o[i], "reverse" if pending["enter"] else "signal")
            side = pending["enter"]
            if side and pos is None:
                r = stop_mult * pending["atr"]
                units = min(pending["equity"] * risk_frac / r, pending["equity"] * max_pos / o[i])
                if units * o[i] >= 10:
                    fill = o[i] * (1 + cost) if side > 0 else o[i] * (1 - cost)
                    cash += -units * fill if side > 0 else units * fill
                    pos = {"side": side, "units": units, "fill": fill, "entry": o[i], "ts": idx[i], "i": i,
                           "source": pending["source"], "stop": o[i] - side * r, "target": o[i] + side * target_r * r,
                           "be_level": o[i] + side * be_r * r, "be": False}
            pending = None

        # 2. stop / target inside the bar (stop first if both touched; gaps fill at the open)
        if pos is not None:
            p = pos
            if p["side"] > 0:
                if l[i] <= p["stop"]:
                    close_pos(i, min(p["stop"], o[i]), "breakeven" if p["be"] else "stop")
                elif h[i] >= p["target"]:
                    close_pos(i, max(p["target"], o[i]), "target")
                elif h[i] >= p["be_level"] and not p["be"]:
                    p["stop"], p["be"] = max(p["stop"], p["entry"]), True
            else:
                if h[i] >= p["stop"]:
                    close_pos(i, max(p["stop"], o[i]), "breakeven" if p["be"] else "stop")
                elif l[i] <= p["target"]:
                    close_pos(i, min(p["target"], o[i]), "target")
                elif l[i] <= p["be_level"] and not p["be"]:
                    p["stop"], p["be"] = min(p["stop"], p["entry"]), True

        # 3. session close: flat at this bar's close, no signals
        if last[i]:
            if pos is not None:
                close_pos(i, c[i], "close")
        # 4. mark to market, risk state
        equity = cash + (pos["side"] * pos["units"] * c[i] if pos is not None else 0.0)
        eq[k] = equity
        peak = max(peak, equity)
        if killed_at is None and equity <= peak * (1 - kill_dd):
            killed_at = idx[i]
        if not halted and equity <= day_start_eq * (1 - day_limit):
            halted, halt_days = True, halt_days + 1
        if last[i]:
            continue

        # 5. this closed bar -> orders for the next open
        sgn = s_sig[i]
        if not sgn:
            continue
        can_enter = (sgn > 0 or shorts) and not halted and not s_pause[i] \
            and minute[i] + 5 < cutoff_min and np.isfinite(s_atr[i]) and s_atr[i] > 0
        if pos is not None:
            if sgn == -pos["side"]:
                pending = {"exit": True, "enter": sgn if can_enter else 0, "atr": s_atr[i],
                           "equity": equity, "source": s_src[i]}
        elif can_enter:
            pending = {"exit": False, "enter": sgn, "atr": s_atr[i], "equity": equity, "source": s_src[i]}

    if pos is not None:
        close_pos(hi - 1, c[hi - 1], "end")
        eq[-1] = cash
    return Result(pd.Series(eq, index=idx[lo:hi], name="equity"), trades, killed_at, halt_days)


def breakdown(tdf: pd.DataFrame, by) -> pd.DataFrame:
    if not len(tdf):
        return pd.DataFrame()
    def pf(x):
        g, lo = x[x > 0].sum(), -x[x <= 0].sum()
        return g / lo if lo else float("inf")
    g = tdf.groupby(by)["pnl"]
    return pd.DataFrame({"trades": g.size(), "pnl": g.sum(), "win_rate_pct": g.apply(lambda x: 100 * (x > 0).mean()),
                         "profit_factor": g.apply(pf)})


def report(cfg: dict, bars: pd.DataFrame, period: str, note: str) -> str:
    pr = cfg["periods"]
    if period == "dev":
        start, end = pr["dev_start"], str((pd.Timestamp(pr["dev_end"]) + pd.Timedelta(days=1)).date())
    else:
        start, end = pr["holdout_start"], None
    eq0 = cfg["account"]["starting_equity"]
    ppy = 252
    res = run(cfg, bars, start, end)
    m = metrics(res, eq0, ppy)
    checks = verdict(m, cfg["passing_bar"])
    passed = all(checks.values())
    c = cfg["costs"]
    title = "DEV CHECK (not the verdict)" if period == "dev" else "HOLDOUT VERDICT"
    w = bars[(bars.index >= res.equity.index[0]) & (bars.index <= res.equity.index[-1])]
    lines = [f"# QuantFlow clone on QQQ 5m: {title}", "",
             f"{note} Spec: docs/QQQ_DAYTRADE_SPEC.md. Settings: config_qqq_daytrade.yaml.", "",
             f"Window: {res.equity.index[0].tz_convert(NY):%Y-%m-%d %H:%M} to {res.equity.index[-1].tz_convert(NY):%Y-%m-%d %H:%M} "
             f"New York, {len(w):,} bars, {w.index.tz_convert(NY).normalize().nunique():,} sessions", "",
             f"## Passing bar at real costs ({c['fee_bps'] + c['slippage_bps']} bps per side): "
             f"**{'PASS' if passed else 'FAIL'}**" + (" (dev period, for reference only)" if period == "dev" else ""), "",
             "| Check | Result | |", "|---|---|---|"]
    vals = [m["sharpe"], m["profit_factor"], m["total_return_pct"], m["n_trades"], m["pct_quarters_profitable"]]
    for (k, ok), v in zip(checks.items(), vals):
        lines.append(f"| {k} | {v:,.2f} | {'pass' if ok else 'FAIL'} |" if isinstance(v, float)
                     else f"| {k} | {v} | {'pass' if ok else 'FAIL'} |")
    lines += ["", f"Max drawdown {m['max_drawdown_pct']:.1f}%, win rate {m['win_rate_pct']:.1f}%, "
              f"daily loss halts {m['daily_halts']}, kill switch (20% DD, recorded only): {m['kill_switch_at'] or 'never'}", "",
              "## Diagnostics (NOT the verdict)", "", "| Version | Return % | Sharpe | PF | Trades |", "|---|---|---|---|---|",
              f"| long + short, real ({c['fee_bps'] + c['slippage_bps']} bps) | {m['total_return_pct']:.2f} | {m['sharpe']:.2f} | "
              f"{m['profit_factor']:.2f} | {m['n_trades']} |"]
    for name, d in c["diagnostics"].items():
        dm = metrics(run(cfg, bars, start, end, d["fee_bps"], d["slippage_bps"]), eq0, ppy)
        lines.append(f"| long + short, {name} ({d['fee_bps'] + d['slippage_bps']} bps) | {dm['total_return_pct']:.2f} | "
                     f"{dm['sharpe']:.2f} | {dm['profit_factor']:.2f} | {dm['n_trades']} |")
    lm = metrics(run(cfg, bars, start, end, long_short=False), eq0, ppy)
    lines.append(f"| long only, real | {lm['total_return_pct']:.2f} | {lm['sharpe']:.2f} | {lm['profit_factor']:.2f} | {lm['n_trades']} |")
    lines += ["", f"Buy and hold QQQ over the same window: {100 * (w['close'].iloc[-1] / w['open'].iloc[0] - 1):+.1f}%", "",
              "Per source, side and exit reason. Picking the best row after seeing this is curve fitting; "
              "any follow up needs its own locked spec.", ""]
    tdf = res.trades_df
    for by in ("source", "side", "reason"):
        b = breakdown(tdf, by)
        if len(b):
            lines += ["```", b.round(2).to_string(), "```", ""]
    if len(tdf):
        lines += [f"Average trade {tdf.pnl_pct.mean():+.3f}% (after costs), median hold {tdf.bars_held.median():.0f} bars", ""]
    lines += ["Quarterly returns %:", "", "```", (100 * m["quarters"]).round(2).to_string(), "```", ""]
    return "\n".join(lines)


def synthetic(days: int = 600, seed: int = 7) -> pd.DataFrame:
    """Regular hours shaped fake bars for smoke tests only."""
    from . import data
    sess = pd.bdate_range("2021-01-04", periods=days)
    times = []
    for d in sess:
        o = pd.Timestamp(f"{d.date()} 09:30", tz=NY)
        times.extend(o + pd.Timedelta(minutes=5 * j) for j in range(78))
    df = data.synthetic("BTC/USD", bars=len(times), seed=seed, start_price=300)
    df.index = pd.DatetimeIndex(times).tz_convert("UTC")
    return df


def cfg_copy(cfg: dict) -> dict:
    return copy.deepcopy(cfg)
