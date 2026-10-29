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
    level: str           # which level was swept: prev_day | premarket
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
    h = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "date": g.apply(lambda x: x.index[0]).map(
        lambda t: t.tz_convert(NY).date())})
    return h.reset_index(drop=True)


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
                        _arm(x, long, info[s], run_hi[s], run_lo[s], S, T)   # → "order", or "idle" if no FVG / no room
                    continue

                # ---- look for a sweep
                if x["state"] == "idle" and w0 <= mn < w1:
                    swept = []
                    for lvl, (lo_k, hi_k) in {"prev_day": ("pdl", "pdh"), "premarket": ("pm_low", "pm_high")}.items():
                        if lvl not in S["levels"]:
                            continue
                        L = info[s][lo_k] if long else info[s][hi_k]
                        if pd.isna(L):
                            continue
                        if (long and l < L < cl) or (not long and cl < L < h):
                            if smt_on:
                                o_s = other.get(s)
                                if o_s is None:
                                    continue
                                OL = info[o_s][lo_k] if long else info[o_s][hi_k]
                                ob = day_bars[o_s]
                                upto = ob[ob.index <= ts]
                                if pd.isna(OL) or not len(upto):
                                    continue
                                if (long and upto["low"].min() < OL) or (not long and upto["high"].max() > OL):
                                    continue
                            swept.append((lvl, L))
                    mss = b["swing_hi"] if long else b["swing_lo"]
                    if swept and not pd.isna(mss):
                        lvl, L = swept[0]
                        x.update(state="setup", side=side, level=lvl, L=L, sweep_ext=l if long else h,
                                 mss=mss, i=0, hist=[(h, l)])
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


def _arm(x: dict, long: bool, info, sess_hi: float, sess_lo: float, S: dict, T: dict):
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
        cands = [v for k, v in (("prev_day", info["pdh"]), ("premarket", info["pm_high"]),
                                ("session_extreme", sess_hi)) if k in S["targets"] and not pd.isna(v) and v > entry]
        tgt = min(cands) if cands else None
    else:
        entry, stop = fvg[0], x["sweep_ext"] + off
        cands = [v for k, v in (("prev_day", info["pdl"]), ("premarket", info["pm_low"]),
                                ("session_extreme", sess_lo)) if k in S["targets"] and not pd.isna(v) and v < entry]
        tgt = max(cands) if cands else None
    r = abs(entry - stop)
    if tgt is None or r <= 0 or abs(tgt - entry) < S["min_target_r"] * r:
        return
    x.update(state="order", entry=entry, stop=stop, target=tgt, valid=T["entry_valid_bars"])


def _close(x: dict, px: float, cost: float, ts, reason: str, trades: list, sym: str) -> float:
    long = x["side"] == "long"
    out = px * (1 - cost) if long else px * (1 + cost)
    pnl = x["units"] * (out - x["fill"]) if long else x["units"] * (x["fill"] - out)
    trades.append(Trade(sym, x["side"], x["level"], x["entry_ts"], ts, x["fill"], out, x["units"], pnl,
                        100 * pnl / (x["units"] * x["fill"]), pnl / x["risk_d"], reason, x["bars"]))
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


def report(cfg: dict, bars: dict[str, pd.DataFrame], period: str, note: str) -> str:
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
    lines = [f"# ADR spec (TJR style) on QQQ + SPY 5m: {title}", "",
             f"{note} Spec: docs/ADR_SPEC.md. Settings: config_adr.yaml.", "",
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
    row("without the SMT check", go(require_smt=False))
    lines += [""]
    for s in bars:
        r = prep[s]["rth"]
        w = r[(r.index >= e.index[0]) & (r.index <= e.index[-1])]
        lines.append(f"Buy and hold {s} over the same window: {100 * (w['close'].iloc[-1] / w['open'].iloc[0] - 1):+.1f}%")
    lines += ["", "Per symbol, side, swept level and exit reason. Picking the best row after seeing this is curve fitting; "
              "any follow up needs its own locked spec.", ""]
    for by in ("symbol", "side", "level", "reason"):
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
