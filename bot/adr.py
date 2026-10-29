"""ADR spec: TJR style sweep → structure shift → fair value gap model on QQQ + SPY 5m.
Spec: docs/ADR_SPEC.md. Settings: config_adr.yaml (locked before any backtest).

Per symbol per day (long shown, short is the mirror):
  bias (1H regular hours swings, fixed at 9:30) → sweep of PDL / premarket low that closes back
  above it (bar starts 9:30 to 11:00) → SMT: the other ETF hasn't broken its own same kind of
  level since 9:30 → within 12 bars a displacement close above the last 5m swing high confirmed
  before the sweep → newest bullish FVG from the sweep to the shift bar → nearest buy side level
  (PDH, premarket high, session high so far) at least 2R above the FVG top → limit buy at the
  FVG top, valid 12 bars and never at/after 11:30, filled only if price trades below it.
  Stop 1 cent below the sweep low (fixed). Target filled only if price trades through it.
  Stop first if both touch in a bar. Flat at the close of the session's last bar.

No lookahead: every level and swing used on bar i is known when bar i closes. Swings need 2
bars on each side, so a swing at bar j is only usable from bar j + 2 on.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import ROOT
from .daytrade import Result, metrics

NY = "America/New_York"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config_adr.yaml") as f:
        return yaml.safe_load(f)


@dataclass
class Trade:
    symbol: str
    side: str            # long | short
    level: str           # which level was swept: prev_day | premarket | h1_swing
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float   # fill incl. costs
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    r: float             # pnl / dollars risked at entry
    reason: str          # target | stop | close
    bars_held: int
    smt: object = None   # True = the other ETF held its level (divergence), False = both swept, None = unknown
    target_kind: str = ""  # which level the target was, or "fixed" (fallback R multiple)


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------- swings
def pivots(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Bool arrays: bar j is a swing high (low) if its high (low) strictly beats the n bars on each side."""
    L = len(high)
    sh = np.zeros(L, bool)
    sl = np.zeros(L, bool)
    for j in range(n, L - n):
        h, lo = high[j], low[j]
        if h > high[j - n:j].max() and h > high[j + 1:j + n + 1].max():
            sh[j] = True
        if lo < low[j - n:j].min() and lo < low[j + 1:j + n + 1].min():
            sl[j] = True
    return sh, sl


def last_confirmed(values: np.ndarray, is_pivot: np.ndarray, n: int) -> np.ndarray:
    """At each bar i: value of the most recent pivot j with j + n <= i (NaN if none)."""
    out = np.full(len(values), np.nan)
    idx = np.flatnonzero(is_pivot)
    conf = idx + n
    ok = conf < len(values)
    out[conf[ok]] = values[idx[ok]]
    return pd.Series(out).ffill().to_numpy()


def hourly_rth(rth: pd.DataFrame) -> pd.DataFrame:
    """Regular hours 1H bars anchored at the open (9:30, 10:30, ... last one may be short)."""
    ny = rth.index.tz_convert(NY)
    date = pd.Series(ny.date, index=rth.index)
    mins = (ny.hour * 60 + ny.minute - 570) // 60
    key = date.astype(str) + "_" + pd.Series(mins, index=rth.index).astype(str)
    g = rth.groupby(key.to_numpy(), sort=False)
    first = g.apply(lambda x: x.index[0])
    h = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "date": first.map(lambda t: t.tz_convert(NY).date()),
                      "end": g.apply(lambda x: x.index[-1]) + pd.Timedelta(minutes=5)})
    return h.reset_index(drop=True)


def untouched_h1(rth: pd.DataFrame, n: int) -> pd.DataFrame:
    """Per 5m bar: the most recent confirmed 1H swing low (high) that price hasn't traded below (above)
    since it was confirmed, as of the bar's open ("_before") and after the bar ("_after").
    A 1H swing at bar j is confirmed when 1H bar j + n closes."""
    h = hourly_rth(rth)
    sh, sl = pivots(h["high"].to_numpy(), h["low"].to_numpy(), n)
    end, H, Lw = h["end"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    ev_lo = [(end[j + n], Lw[j]) for j in np.flatnonzero(sl) if j + n < len(h)]
    ev_hi = [(end[j + n], H[j]) for j in np.flatnonzero(sh) if j + n < len(h)]
    ts, hi, lo = rth.index.to_numpy(), rth["high"].to_numpy(), rth["low"].to_numpy()
    out = np.full((len(rth), 4), np.nan)
    stk_lo, stk_hi = [], []      # untouched levels; lows rise and highs fall toward the top of each stack
    a = b = 0
    for i in range(len(rth)):
        while a < len(ev_lo) and ev_lo[a][0] <= ts[i]:
            stk_lo.append(ev_lo[a][1]); a += 1
        while b < len(ev_hi) and ev_hi[b][0] <= ts[i]:
            stk_hi.append(ev_hi[b][1]); b += 1
        out[i, 0] = stk_lo[-1] if stk_lo else np.nan
        out[i, 1] = stk_hi[-1] if stk_hi else np.nan
        while stk_lo and stk_lo[-1] > lo[i]:
            stk_lo.pop()
        while stk_hi and stk_hi[-1] < hi[i]:
            stk_hi.pop()
        out[i, 2] = stk_lo[-1] if stk_lo else np.nan
        out[i, 3] = stk_hi[-1] if stk_hi else np.nan
    return pd.DataFrame(out, index=rth.index, columns=["h1_lo_before", "h1_hi_before", "h1_lo_after", "h1_hi_after"])


def daily_bias(rth: pd.DataFrame, n: int) -> dict:
    """{session date: +1 bull / -1 bear / 0 none}, from 1H swings confirmed before that session opens."""
    h = hourly_rth(rth)
    sh, sl = pivots(h["high"].to_numpy(), h["low"].to_numpy(), n)
    hi_idx, lo_idx = np.flatnonzero(sh), np.flatnonzero(sl)
    first_bar = h.groupby("date").apply(lambda x: x.index[0])   # 1H bars before this index are closed at the open
    out = {}
    for d, k in first_bar.items():
        hs = hi_idx[hi_idx + n <= k - 1]
        ls = lo_idx[lo_idx + n <= k - 1]
        if len(hs) < 2 or len(ls) < 2:
            out[d] = 0
            continue
        H, Lw = h["high"].to_numpy(), h["low"].to_numpy()
        up = H[hs[-1]] > H[hs[-2]] and Lw[ls[-1]] > Lw[ls[-2]]
        dn = H[hs[-1]] < H[hs[-2]] and Lw[ls[-1]] < Lw[ls[-2]]
        out[d] = 1 if up else -1 if dn else 0
    return out


# ---------------------------------------------------------------- per symbol prep
def prepare(bars: pd.DataFrame, cfg: dict) -> dict:
    """bars: 5m incl. premarket with a bool 'rth' column (bot/stocks_data.load_5m_ext)."""
    n = cfg["setup"]["swing_bars"]
    rth = bars[bars["rth"]].copy()
    pre = bars[~bars["rth"]]
    ny = rth.index.tz_convert(NY)
    rth["date"] = ny.date
    rth["min"] = ny.hour * 60 + ny.minute
    hi, lo = rth["high"].to_numpy(), rth["low"].to_numpy()
    sh, sl = pivots(hi, lo, n)
    rth["swing_hi"] = last_confirmed(hi, sh, n)
    rth["swing_lo"] = last_confirmed(lo, sl, n)
    rth = rth.join(untouched_h1(rth, n))
    day = rth.groupby("date").agg(day_high=("high", "max"), day_low=("low", "min"))
    day["pdh"], day["pdl"] = day["day_high"].shift(1), day["day_low"].shift(1)
    pre_ny = pre.index.tz_convert(NY)
    pm = pre.groupby(pre_ny.date).agg(pm_high=("high", "max"), pm_low=("low", "min"))
    day = day.join(pm)
    day["bias"] = pd.Series(daily_bias(rth, n))
    return {"rth": rth, "day": day}


# ---------------------------------------------------------------- engine
def run(cfg: dict, bars: dict[str, pd.DataFrame], start: str | None = None, end: str | None = None,
        fee_bps: float | None = None, slippage_bps: float | None = None, sides: tuple = ("long", "short"),
        trade_symbols: list[str] | None = None, require_smt: bool | None = None,
        prepared: dict | None = None) -> Result:
    S, T, R = cfg["setup"], cfg["trade"], cfg["risk"]
    c = cfg["costs"]
    cost = ((c["fee_bps"] if fee_bps is None else fee_bps) + (c["slippage_bps"] if slippage_bps is None else slippage_bps)) / 1e4
    smt_on = S["require_smt"] if require_smt is None else require_smt
    syms = list(bars)
    other = {syms[0]: syms[1], syms[1]: syms[0]} if len(syms) == 2 else {}
    trade_symbols = trade_symbols or syms
    P = prepared or {s: prepare(bars[s], cfg) for s in syms}
    w0, w1 = _hm(S["sweep_window"]["start"]), _hm(S["sweep_window"]["end"])
    cutoff = _hm(T["no_entries_after"])
    eq0 = cfg["account"]["starting_equity"]

    days = sorted(set.intersection(*[set(P[s]["rth"]["date"]) for s in syms]))
    if start:
        days = [d for d in days if d >= pd.Timestamp(start).date()]
    if end:
        days = [d for d in days if d < pd.Timestamp(end).date()]

    cash = eq0
    trades: list[Trade] = []
    eq_ts, eq_v = [], []
    peak, killed_at, halt_days = eq0, None, 0
    by_day = {s: dict(tuple(P[s]["rth"].groupby("date"))) for s in syms}

    for d in days:
        day_bars = {s: by_day[s][d] for s in syms}
        info = {s: P[s]["day"].loc[d] for s in syms}
        idx = day_bars[syms[0]].index
        for s in syms[1:]:
            idx = idx.union(day_bars[s].index)
        day_start_eq = cash
        halted = False
        st = {s: {"state": "idle", "done": False} for s in syms}
        # running RTH extremes since 9:30, per symbol, for SMT and session targets
        run_lo = {s: np.inf for s in syms}
        run_hi = {s: -np.inf for s in syms}
        for ts in idx:
            mtm = cash
            for s in syms:
                db = day_bars[s]
                if ts not in db.index:
                    continue
                b = db.loc[ts]
                o, h, l, cl, mn = b["open"], b["high"], b["low"], b["close"], b["min"]
                prev_lo, prev_hi = run_lo[s], run_hi[s]
                run_lo[s], run_hi[s] = min(run_lo[s], l), max(run_hi[s], h)
                x = st[s]
                last_bar = ts == db.index[-1]

                # ---- manage an open position
                if x["state"] == "pos":
                    x["bars"] += 1
                    long = x["side"] == "long"
                    exit_px, reason = None, None
                    if long:
                        if o <= x["stop"]:
                            exit_px, reason = o, "stop"
                        elif l <= x["stop"]:
                            exit_px, reason = x["stop"], "stop"
                        elif h > x["target"]:
                            exit_px, reason = x["target"], "target"
                    else:
                        if o >= x["stop"]:
                            exit_px, reason = o, "stop"
                        elif h >= x["stop"]:
                            exit_px, reason = x["stop"], "stop"
                        elif l < x["target"]:
                            exit_px, reason = x["target"], "target"
                    if exit_px is None and last_bar:
                        exit_px, reason = cl, "close"
                    if exit_px is not None:
                        cash += _close(x, exit_px, cost, ts, reason, trades, s)
                        x["state"], x["done"] = "idle", True
                    continue

                # ---- pending limit order
                if x["state"] == "order":
                    long = x["side"] == "long"
                    expired = x["valid"] <= 0 or mn >= cutoff
                    if not expired:
                        x["valid"] -= 1
                        filled = (l < x["entry"]) if long else (h > x["entry"])
                        if filled:
                            fill = x["entry"] * (1 + cost) if long else x["entry"] * (1 - cost)
                            risk_d = R["risk_per_trade_pct"] / 100 * cash
                            per = abs(x["entry"] - x["stop"])
                            units = np.floor(min(risk_d / per, R["max_position_pct_per_symbol"] / 100 * cash / x["entry"]))
                            if units >= 1:
                                x.update(state="pos", units=units, fill=fill, entry_ts=ts, bars=0,
                                         risk_d=units * per)
                                # same bar: stop can hit after the fill; target can't be counted (order unknown)
                                hit = (l <= x["stop"]) if long else (h >= x["stop"])
                                if hit:
                                    cash += _close(x, x["stop"], cost, ts, "stop", trades, s)
                                    x["state"], x["done"] = "idle", True
                                elif last_bar:
                                    cash += _close(x, cl, cost, ts, "close", trades, s)
                                    x["state"], x["done"] = "idle", True
                                continue
                            expired = True
                        elif (h >= x["target"]) if long else (l <= x["target"]):
                            expired = True   # ran to target without us
                    if expired:
                        x["state"] = "idle"                          # no fill: not a trade, can look again
                    continue

                if x["done"] or s not in trade_symbols or halted:
                    continue
                bias = info[s]["bias"]
                side = "long" if bias == 1 else "short" if bias == -1 else None
                if side is None or side not in sides:
                    continue
                long = side == "long"

                # ---- active setup: look for the structure shift
                if x["state"] == "setup":
                    x["hist"].append((h, l))
                    x["i"] += 1
                    if (long and l < x["sweep_ext"]) or (not long and h > x["sweep_ext"]):
                        if (long and cl > x["L"]) or (not long and cl < x["L"]):
                            x["sweep_ext"] = l if long else h        # closed back inside: still a sweep, extend it
                        else:
                            x["state"] = "idle"                      # closed beyond the level: setup dead
                            continue
                    if x["i"] > S["shift_within_bars"]:
                        x["state"] = "idle"                          # no shift within 12 bars
                        continue
                    rng = h - l
                    disp = rng > 0 and abs(cl - o) / rng * 100 >= S["displacement_min_body_pct"]
                    if disp and ((long and cl > o and cl > x["mss"]) or (not long and cl < o and cl < x["mss"])):
                        _arm(x, long, info[s], b, run_hi[s], run_lo[s], S, T)   # → "order", or "idle" if no FVG / no room
                    continue

                # ---- look for a sweep
                if x["state"] == "idle" and w0 <= mn < w1:
                    swept = []
                    for lvl in ("prev_day", "premarket", "h1_swing"):
                        if lvl not in S["levels"]:
                            continue
                        L = _level(lvl, long, info[s], b)
                        if pd.isna(L):
                            continue
                        if (long and l < L < cl) or (not long and cl < L < h):
                            smt = None
                            o_s = other.get(s)
                            if o_s is not None:
                                ob = day_bars[o_s]
                                upto = ob[ob.index <= ts]
                                OL = _level(lvl, long, info[o_s], ob.iloc[0]) if len(ob) else np.nan
                                if not pd.isna(OL) and len(upto):
                                    smt = not bool((long and upto["low"].min() < OL) or (not long and upto["high"].max() > OL))
                            if smt_on and smt is not True:
                                continue
                            swept.append((lvl, L, smt))
                    mss = b["swing_hi"] if long else b["swing_lo"]
                    if swept and not pd.isna(mss):
                        lvl, L, smt = swept[0]
                        x.update(state="setup", side=side, level=lvl, L=L, sweep_ext=l if long else h,
                                 mss=mss, i=0, hist=[(h, l)], smt=smt)
            # equity mark
            for s in syms:
                x = st[s]
                if x["state"] == "pos" and ts in day_bars[s].index:
                    x["last"] = day_bars[s].loc[ts, "close"]
                if x["state"] == "pos":
                    px = x.get("last", x["entry"])
                    mtm += x["units"] * (px - x["fill"]) if x["side"] == "long" else x["units"] * (x["fill"] - px)
            eq_ts.append(ts)
            eq_v.append(mtm)
            peak = max(peak, mtm)
            if killed_at is None and mtm <= peak * (1 - R["max_drawdown_kill_pct"] / 100):
                killed_at = ts
            if not halted and mtm <= day_start_eq * (1 - R["daily_loss_limit_pct"] / 100):
                halted = True
                halt_days += 1
    eq = pd.Series(eq_v, index=pd.DatetimeIndex(eq_ts), name="equity")
    return Result(eq, trades, killed_at, halt_days)


def _level(kind: str, long: bool, info, bar) -> float:
    """Sell side level for longs / buy side for shorts. h1_swing uses the value as of the bar's open."""
    if kind == "prev_day":
        return info["pdl"] if long else info["pdh"]
    if kind == "premarket":
        return info["pm_low"] if long else info["pm_high"]
    return bar["h1_lo_before"] if long else bar["h1_hi_before"]


def _arm(x: dict, long: bool, info, bar, sess_hi: float, sess_lo: float, S: dict, T: dict):
    """Shift bar just closed: find the newest FVG from the sweep to the shift bar, check room, place the limit."""
    hist = x["hist"]
    fvg = None
    for j in range(len(hist) - 1, 1, -1):
        if long and hist[j][1] > hist[j - 2][0]:
            fvg = (hist[j - 2][0], hist[j][1])       # (bottom, top)
            break
        if not long and hist[j][0] < hist[j - 2][1]:
            fvg = (hist[j][0], hist[j - 2][1])       # (bottom, top)
            break
    x["state"] = "idle"
    if fvg is None:
        return
    off = T["stop_offset"]
    if long:
        entry, stop = fvg[1], x["sweep_ext"] - off
        pools = (("prev_day", info["pdh"]), ("premarket", info["pm_high"]), ("session_extreme", sess_hi),
                 ("h1_swing", bar["h1_hi_after"]))
    else:
        entry, stop = fvg[0], x["sweep_ext"] + off
        pools = (("prev_day", info["pdl"]), ("premarket", info["pm_low"]), ("session_extreme", sess_lo),
                 ("h1_swing", bar["h1_lo_after"]))
    r = abs(entry - stop)
    if r <= 0:
        return
    cands = sorted((abs(v - entry), k, v) for k, v in pools
                   if k in S["targets"] and not pd.isna(v) and (v > entry if long else v < entry))
    if S.get("target_pick", "nearest_then_check") == "nearest_clearing_min":
        cands = [c for c in cands if c[0] >= S["min_target_r"] * r]      # nearest pool that clears the minimum
    elif cands and cands[0][0] < S["min_target_r"] * r:
        cands = []                                                        # ADR: nearest pool must clear it
    if cands:
        kind, tgt = cands[0][1], cands[0][2]
    elif S.get("fallback_target_r"):
        kind, tgt = "fixed", entry + S["fallback_target_r"] * r if long else entry - S["fallback_target_r"] * r
    else:
        return
    x.update(state="order", entry=entry, stop=stop, target=tgt, target_kind=kind, valid=T["entry_valid_bars"])


def _close(x: dict, px: float, cost: float, ts, reason: str, trades: list, sym: str) -> float:
    long = x["side"] == "long"
    out = px * (1 - cost) if long else px * (1 + cost)
    pnl = x["units"] * (out - x["fill"]) if long else x["units"] * (x["fill"] - out)
    trades.append(Trade(sym, x["side"], x["level"], x["entry_ts"], ts, x["fill"], out, x["units"], pnl,
                        100 * pnl / (x["units"] * x["fill"]), pnl / x["risk_d"], reason, x["bars"],
                        x.get("smt"), x.get("target_kind", "")))
    return pnl


# ---------------------------------------------------------------- verdict + report
def win_rate_edge(tdf: pd.DataFrame) -> tuple[float, float]:
    """(win rate %, breakeven win rate %) where breakeven = 1 / (1 + avg win R / avg loss R)."""
    if not len(tdf):
        return float("nan"), float("nan")
    w, lo = tdf[tdf.pnl > 0], tdf[tdf.pnl <= 0]
    wr = 100 * len(w) / len(tdf)
    if not len(w) or not len(lo) or lo.r.mean() == 0:
        return wr, float("nan")
    return wr, 100 / (1 + w.r.mean() / abs(lo.r.mean()))


def verdict(m: dict, wr: float, be: float, bar: dict) -> dict[str, bool]:
    return {
        f"Sharpe >= {bar['min_sharpe']}": m["sharpe"] >= bar["min_sharpe"],
        f"Profit factor >= {bar['min_profit_factor']}": m["profit_factor"] >= bar["min_profit_factor"],
        f"Total return > {bar['min_total_return_pct']}%": m["total_return_pct"] > bar["min_total_return_pct"],
        f"Trades >= {bar['min_trades']}": m["n_trades"] >= bar["min_trades"],
        f"Win rate >= breakeven + {bar['min_win_rate_edge_pts']} pts": bool(wr - be >= bar["min_win_rate_edge_pts"]),
        f"Quarters profitable >= {bar['min_pct_quarters_profitable']}%":
            m["pct_quarters_profitable"] >= bar["min_pct_quarters_profitable"],
    }


def breakdown(tdf: pd.DataFrame, by) -> pd.DataFrame:
    if not len(tdf):
        return pd.DataFrame()
    def pf(x):
        g, lo = x[x > 0].sum(), -x[x <= 0].sum()
        return g / lo if lo else float("inf")
    g = tdf.groupby(by)
    return pd.DataFrame({"trades": g.size(), "pnl": g["pnl"].sum(), "win_rate_pct": g["pnl"].apply(lambda x: 100 * (x > 0).mean()),
                         "avg_r": g["r"].mean(), "profit_factor": g["pnl"].apply(pf)})


def report(cfg: dict, bars: dict[str, pd.DataFrame], period: str, note: str, name: str = "ADR spec (TJR style)",
           spec: str = "docs/ADR_SPEC.md", config: str = "config_adr.yaml") -> str:
    pr = cfg["periods"]
    if period == "dev":
        start, end = pr["dev_start"], str((pd.Timestamp(pr["dev_end"]) + pd.Timedelta(days=1)).date())
    else:
        start, end = pr["holdout_start"], None
    eq0 = cfg["account"]["starting_equity"]
    prep = {s: prepare(bars[s], cfg) for s in bars}
    go = lambda **kw: run(cfg, bars, start, end, prepared=prep, **kw)
    res = go()
    m = metrics(res, eq0, 252)
    tdf = res.trades_df
    wr, be = win_rate_edge(tdf)
    checks = verdict(m, wr, be, cfg["passing_bar"])
    passed = all(checks.values())
    c = cfg["costs"]
    title = "DEV CHECK (not the verdict)" if period == "dev" else "HOLDOUT VERDICT"
    e = res.equity
    lines = [f"# {name} on QQQ + SPY 5m: {title}", "",
             f"{note} Spec: {spec}. Settings: {config}.", "",
             f"Window: {e.index[0].tz_convert(NY):%Y-%m-%d} to {e.index[-1].tz_convert(NY):%Y-%m-%d} New York, "
             f"{e.index.tz_convert(NY).normalize().nunique():,} sessions", "",
             f"## Passing bar at real costs ({c['fee_bps'] + c['slippage_bps']} bps per side): "
             f"**{'PASS' if passed else 'FAIL'}**" + (" (dev period, for reference only)" if period == "dev" else ""), "",
             "| Check | Result | |", "|---|---|---|"]
    vals = [f"{m['sharpe']:.2f}", f"{m['profit_factor']:.2f}", f"{m['total_return_pct']:.2f}%", str(m["n_trades"]),
            f"{wr:.1f}% vs {be:.1f}% breakeven", f"{m['pct_quarters_profitable']:.1f}%"]
    for (k, ok), v in zip(checks.items(), vals):
        lines.append(f"| {k} | {v} | {'pass' if ok else 'FAIL'} |")
    lines += ["", f"Max drawdown {m['max_drawdown_pct']:.1f}%, daily loss halts {m['daily_halts']}, "
              f"kill switch (20% DD, recorded only): {m['kill_switch_at'] or 'never'}", "",
              "## Diagnostics (NOT the verdict)", "", "| Version | Return % | Sharpe | PF | Trades | Win % |", "|---|---|---|---|---|---|"]
    def row(name, r):
        dm = metrics(r, eq0, 252)
        lines.append(f"| {name} | {dm['total_return_pct']:.2f} | {dm['sharpe']:.2f} | {dm['profit_factor']:.2f} | "
                     f"{dm['n_trades']} | {dm['win_rate_pct']:.1f} |")
    row(f"as specced, real ({c['fee_bps'] + c['slippage_bps']} bps)", res)
    for name, d in c["diagnostics"].items():
        row(f"{name} ({d['fee_bps'] + d['slippage_bps']} bps)", go(fee_bps=d["fee_bps"], slippage_bps=d["slippage_bps"]))
    row("long only", go(sides=("long",)))
    row("short only", go(sides=("short",)))
    for s in bars:
        row(f"{s} only", go(trade_symbols=[s]))
    smt_req = cfg["setup"]["require_smt"]
    row("without the SMT check" if smt_req else "with SMT required", go(require_smt=not smt_req))
    lines += [""]
    for s in bars:
        r = prep[s]["rth"]
        w = r[(r.index >= e.index[0]) & (r.index <= e.index[-1])]
        lines.append(f"Buy and hold {s} over the same window: {100 * (w['close'].iloc[-1] / w['open'].iloc[0] - 1):+.1f}%")
    lines += ["", "Per symbol, side, swept level and exit reason. Picking the best row after seeing this is curve fitting; "
              "any follow up needs its own locked spec.", ""]
    for by in ("symbol", "side", "level", "reason", "smt", "target_kind"):
        b = breakdown(tdf, by)
        if len(b):
            lines += ["```", b.round(2).to_string(), "```", ""]
    if len(tdf):
        lines += [f"Average trade {tdf.pnl_pct.mean():+.3f}% / {tdf.r.mean():+.2f}R after costs, "
                  f"median hold {tdf.bars_held.median():.0f} bars, about {len(tdf) / max(1, e.index.tz_convert(NY).normalize().nunique()) * 5:.2f} trades a week", ""]
    lines += ["Quarterly returns %:", "", "```", (100 * m["quarters"]).round(2).to_string(), "```", ""]
    return "\n".join(lines)


def cfg_copy(cfg: dict) -> dict:
    return copy.deepcopy(cfg)
