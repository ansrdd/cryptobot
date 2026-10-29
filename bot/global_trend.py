"""Global Trend spec: multi asset ETF trend following. Spec: docs/GLOBAL_TREND_SPEC.md.
Settings: config_global_trend.yaml (locked before any data or backtest).

On each month's last trading day close:
  trend score per risk ETF = share of the 1 / 3 / 12 month total returns that are > 0
  risk weight = (1 / stdev of the last 63 daily returns), normalized over the 8 risk ETFs
  target = risk weight x trend score; the rest goes to the cash ETF (SHY). No leverage, no shorts.
Trades fill at the next trading day's open; trades under 0.5% of equity are skipped.

Benchmarks (SPY, 60/40, equal weight) run through the same engine and fills, so the
comparison is like for like. No lookahead: a decision on day t only uses closes up to t.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import ROOT


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config_global_trend.yaml") as f:
        return yaml.safe_load(f)


def month_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in idx."""
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby(idx.to_period("M")).max().to_numpy())


# ---------------------------------------------------------------- signals
def trend_scores(close: pd.DataFrame, dates: pd.DatetimeIndex, lookbacks: list[int]) -> pd.DataFrame:
    """Per decision date and asset: share of lookbacks (in months) with total return > 0.
    The return over k months is close at the date / close at the month end k months earlier.
    NaN if any lookback lacks history."""
    me = month_ends(close.index)
    out = pd.DataFrame(index=dates, columns=close.columns, dtype=float)
    for d in dates:
        pos = me.get_loc(d)
        votes = []
        for k in lookbacks:
            if pos - k < 0:
                votes = None
                break
            past = close.loc[me[pos - k]]
            votes.append((close.loc[d] / past - 1 > 0).astype(float).where(past.notna() & close.loc[d].notna()))
        if votes is not None:
            out.loc[d] = pd.concat(votes, axis=1).mean(axis=1, skipna=False)
    return out


def risk_weights(close: pd.DataFrame, dates: pd.DatetimeIndex, vol_days: int) -> pd.DataFrame:
    """Inverse volatility weights (sum 1) from the last vol_days daily returns up to each date."""
    ret = close.pct_change()
    vol = ret.rolling(vol_days, min_periods=vol_days).std()
    inv = 1 / vol.loc[dates]
    return inv.div(inv.sum(axis=1, skipna=False), axis=0)


def strategy_targets(close: pd.DataFrame, dates: pd.DatetimeIndex, cfg: dict,
                     lookbacks: list[int] | None = None, equal_weights: bool = False) -> pd.DataFrame:
    u, s = cfg["universe"], cfg["signal"]
    risk = close[u["risk_assets"]]
    score = trend_scores(risk, dates, lookbacks or s["lookback_months"])
    rw = pd.DataFrame(1 / len(u["risk_assets"]), index=dates, columns=risk.columns) if equal_weights \
        else risk_weights(risk, dates, s["vol_days"])
    w = (rw * score).dropna(how="any")
    w = w.clip(lower=0)
    tot = w.sum(axis=1)
    cap = s["max_invested_pct"] / 100
    w = w.mul(np.minimum(1, cap / tot.where(tot > 0, 1)), axis=0)
    w[u["cash_asset"]] = 1 - w.sum(axis=1)
    return w


def fixed_targets(dates: pd.DatetimeIndex, weights: dict) -> pd.DataFrame:
    return pd.DataFrame([weights] * len(dates), index=dates)


# ---------------------------------------------------------------- engine
@dataclass
class Result:
    equity: pd.Series          # daily, at the close
    trades: int
    turnover: float            # total traded notional / average equity
    costs: float


def run(bars: pd.DataFrame, targets: pd.DataFrame, start: str, end: str | None, cost_bps: float,
        min_trade_pct: float, equity0: float) -> Result:
    """bars: (field, symbol) columns, daily. targets: weights per decision date (rows sum to 1).
    Decision on day t fills at the open of the next trading day inside [start, end]."""
    op, cl = bars["open"], bars["close"]
    days = cl.index[(cl.index >= pd.Timestamp(start)) & ((cl.index <= pd.Timestamp(end)) if end else True)]
    # decisions whose fill day is in the window (the last one before start may fill on day 1)
    nxt = pd.Series(cl.index[1:], index=cl.index[:-1])
    dec = [d for d in targets.index if d in nxt.index and nxt[d] in days]
    fills = {nxt[d]: targets.loc[d].dropna() for d in dec}
    if not fills:
        raise ValueError("no rebalance inside the window")
    first = min(fills)
    days = days[days >= first]
    cost = cost_bps / 1e4
    cash, units = equity0, {}
    eq, n_trades, traded, paid = [], 0, 0.0, 0.0
    for d in days:
        if d in fills:
            w = fills[d]
            px = op.loc[d]
            value = cash + sum(u * px[s] for s, u in units.items())
            for s in sorted(set(units) | set(w.index)):
                tgt = w.get(s, 0.0) * value
                cur = units.get(s, 0.0) * px[s]
                delta = tgt - cur
                if abs(delta) < min_trade_pct / 100 * value or np.isnan(px[s]):
                    continue
                units[s] = units.get(s, 0.0) + delta / px[s]
                fee = abs(delta) * cost
                cash -= delta + fee
                n_trades += 1
                traded += abs(delta)
                paid += fee
        eq.append(cash + sum(u * cl.loc[d, s] for s, u in units.items()))
    e = pd.Series(eq, index=days, name="equity")
    return Result(e, n_trades, traded / e.mean(), paid)


# ---------------------------------------------------------------- metrics
def metrics(e: pd.Series) -> dict:
    m = e.groupby(e.index.to_period("M")).last()
    mret = pd.concat([pd.Series([e.iloc[0]]), m.reset_index(drop=True)]).pct_change().dropna()
    y = e.groupby(e.index.year).last()
    yret = pd.concat([pd.Series([e.iloc[0]]), y.reset_index(drop=True)]).pct_change().dropna()
    yret.index = y.index
    dd = e / e.cummax() - 1
    years = (e.index[-1] - e.index[0]).days / 365.25
    total = e.iloc[-1] / e.iloc[0] - 1
    return {
        "total_return_pct": 100 * total,
        "cagr_pct": 100 * ((1 + total) ** (1 / years) - 1) if years > 0 else float("nan"),
        "sharpe": float(mret.mean() / mret.std() * np.sqrt(12)) if mret.std() > 0 else float("nan"),
        "vol_pct": float(mret.std() * np.sqrt(12) * 100),
        "max_drawdown_pct": 100 * float(dd.min()),
        "pct_years_profitable": 100 * float((yret > 0).mean()),
        "years": yret,
        "worst_dd": _worst_drawdowns(e, 3),
    }


def _worst_drawdowns(e: pd.Series, n: int) -> list[tuple]:
    dd = e / e.cummax() - 1
    out, seen = [], set()
    for t in dd.sort_values().index:
        peak = e.loc[:t].idxmax()
        if peak in seen:
            continue
        seen.add(peak)
        out.append((peak.date(), t.date(), round(100 * dd[t], 1)))
        if len(out) == n:
            break
    return out


def verdict(m: dict, m6040: dict, mspy: dict, bar: dict) -> dict[str, bool]:
    return {
        f"Sharpe >= {bar['min_sharpe']}": m["sharpe"] >= bar["min_sharpe"],
        "Sharpe >= 60/40 Sharpe": m["sharpe"] >= m6040["sharpe"],
        f"Max drawdown <= {bar['max_dd_vs_spy_ratio']:.0%} of SPY's": abs(m["max_drawdown_pct"]) <= bar["max_dd_vs_spy_ratio"] * abs(mspy["max_drawdown_pct"]),
        f"Total return > {bar['min_total_return_pct']}%": m["total_return_pct"] > bar["min_total_return_pct"],
        f"Years profitable >= {bar['min_pct_years_profitable']}%": m["pct_years_profitable"] >= bar["min_pct_years_profitable"],
    }


# ---------------------------------------------------------------- report
def report(cfg: dict, bars: pd.DataFrame, period: str, note: str) -> str:
    pr, u, s, c = cfg["periods"], cfg["universe"], cfg["signal"], cfg["costs"]
    syms = u["risk_assets"] + [u["cash_asset"]]
    bars = bars.loc[:, bars.columns.get_level_values(1).isin(syms)]
    bars = bars.dropna(how="any")                    # common history only (DBC starts 2006-02)
    if period == "dev":
        start, end = pr["dev_start"], pr["dev_end"]
    else:
        start, end = pr["holdout_start"], None
    close = bars["close"]
    dates = month_ends(close.index)
    eq0 = cfg["account"]["starting_equity"]
    real = c["fee_bps"] + c["slippage_bps"]
    go = lambda tg, bps=real, st=None: run(bars, tg, st or start, end, bps, s["min_trade_pct"], eq0)

    strat_t = strategy_targets(close, dates, cfg)
    res = go(strat_t)
    w0, w1 = res.equity.index[0], res.equity.index[-1]
    start = str(w0.date())       # benchmarks and diagnostics start on the strategy's first fill day
    bm = cfg["benchmarks"]
    bench = {
        "SPY buy and hold": go(fixed_targets(dates, bm["spy"])),
        "60/40 SPY/IEF": go(fixed_targets(dates, bm["sixty_forty"])),
        "Equal weight 8 ETFs": go(fixed_targets(dates, {a: 1 / len(u["risk_assets"]) for a in u["risk_assets"]})),
    }
    m = metrics(res.equity)
    mb = {k: metrics(v.equity) for k, v in bench.items()}
    checks = verdict(m, mb["60/40 SPY/IEF"], mb["SPY buy and hold"], cfg["passing_bar"])
    passed = all(checks.values())
    title = "DEV CHECK (not the verdict)" if period == "dev" else "HOLDOUT VERDICT"
    lines = [f"# Global Trend (multi asset ETF trend following): {title}", "",
             f"{note} Spec: docs/GLOBAL_TREND_SPEC.md. Settings: config_global_trend.yaml.", "",
             f"Window: {w0:%Y-%m-%d} to {w1:%Y-%m-%d} ({(w1 - w0).days / 365.25:.1f} years), first fill at the open of {w0:%Y-%m-%d}", "",
             f"## Passing bar at real costs ({real} bps per side): **{'PASS' if passed else 'FAIL'}**"
             + (" (dev period, for reference only)" if period == "dev" else ""), "",
             "| Check | Result | |", "|---|---|---|"]
    vals = [f"{m['sharpe']:.2f}", f"{m['sharpe']:.2f} vs {mb['60/40 SPY/IEF']['sharpe']:.2f}",
            f"{m['max_drawdown_pct']:.1f}% vs SPY {mb['SPY buy and hold']['max_drawdown_pct']:.1f}%",
            f"{m['total_return_pct']:.1f}%", f"{m['pct_years_profitable']:.0f}%"]
    for (k, ok), v in zip(checks.items(), vals):
        lines.append(f"| {k} | {v} | {'pass' if ok else 'FAIL'} |")
    lines += ["", f"Trades {res.trades}, turnover {res.turnover:.1f}x average equity, costs paid ${res.costs:,.0f}", "",
              "## Strategy vs benchmarks (same window, same fills and costs)", "",
              "| | Total % | CAGR % | Sharpe | Vol % | Max DD % | Years up % |", "|---|---|---|---|---|---|---|"]
    def row(name, mm):
        lines.append(f"| {name} | {mm['total_return_pct']:.1f} | {mm['cagr_pct']:.2f} | {mm['sharpe']:.2f} | "
                     f"{mm['vol_pct']:.1f} | {mm['max_drawdown_pct']:.1f} | {mm['pct_years_profitable']:.0f} |")
    row("**Global Trend**", m)
    for k, mm in mb.items():
        row(k, mm)
    lines += ["", "## Diagnostics (NOT the verdict)", "",
              "| Version | Total % | CAGR % | Sharpe | Max DD % |", "|---|---|---|---|---|"]
    def drow(name, r):
        mm = metrics(r.equity)
        lines.append(f"| {name} | {mm['total_return_pct']:.1f} | {mm['cagr_pct']:.2f} | {mm['sharpe']:.2f} | {mm['max_drawdown_pct']:.1f} |")
    drow(f"as specced, real ({real} bps)", res)
    for name, d in c["diagnostics"].items():
        drow(f"{name} ({d['fee_bps'] + d['slippage_bps']} bps)", go(strat_t, d["fee_bps"] + d["slippage_bps"]))
    for k in s["lookback_months"]:
        drow(f"{k} month lookback only", go(strategy_targets(close, dates, cfg, lookbacks=[k])))
    drow("equal weights instead of risk weights", go(strategy_targets(close, dates, cfg, equal_weights=True)))
    inw = strat_t.loc[(strat_t.index >= w0 - pd.Timedelta(days=40)) & (strat_t.index <= w1)]
    lines += ["", f"Average invested (not in {u['cash_asset']}): {100 * (1 - inw[u['cash_asset']].mean()):.0f}%, "
              f"average weight per ETF: " + ", ".join(f"{a} {100 * inw[a].mean():.0f}%" for a in u["risk_assets"]), "",
              "Worst 3 drawdowns (peak, trough, %): Global Trend " + str(m["worst_dd"]) +
              f"; SPY {mb['SPY buy and hold']['worst_dd']}", "", "Calendar year returns %:", "", "```"]
    yr = pd.DataFrame({"Global Trend": 100 * m["years"], **{k: 100 * v["years"] for k, v in mb.items()}}).round(1)
    lines += [yr.to_string(), "```", "",
              "Picking a lookback, weighting or asset after seeing these is curve fitting; any follow up needs its own locked spec.", ""]
    return "\n".join(lines)


def cfg_copy(cfg: dict) -> dict:
    return copy.deepcopy(cfg)
