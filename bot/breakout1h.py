"""1h volatility breakout ("slow day trading"), long only.

Rules come from docs/BREAKOUT1H_SPEC.md (locked before any backtest):
  - BUY when the close breaks the previous 24 bars' high while above EMA 200
  - fill at the NEXT bar's open, fee + slippage on every fill
  - stop 2 x ATR below entry, target 3R, stop wins if both touched in one bar
  - fee filter: skip if the 3R target is under 5 x the round trip cost
  - time stop: exit at the next open once 48 bars have been held
  - after an exit, a new entry needs a signal on a later bar
  - 1% risk per trade, max 30% of equity per coin, 3% daily loss halts new entries
  - 20% drawdown kill switch is recorded only, the test keeps going

Indicators use all history (they only look back), but trades and scores only
count inside [start, end). Separate from the live bot and from bot/daytrade.py.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import data
from .config import ROOT
from .daytrade import Result, Trade, breakdown, metrics, verdict
from .indicators import atr, ema

RESEARCH_DIR = ROOT / "data" / "research"  # gitignored copy; the live bot commits data/*_1h.parquet


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config_breakout1h.yaml") as f:
        return yaml.safe_load(f)


def load_bars(cfg: dict) -> dict[str, pd.DataFrame]:
    """Hourly bars from a research copy of the cache, refreshed to now. Never touches tracked files."""
    u = cfg["universe"]
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for s in u["symbols"]:
        name = f"{s.replace('/', '')}_{u['timeframe']}.parquet"
        if not (RESEARCH_DIR / name).exists() and (ROOT / "data" / name).exists():
            shutil.copy(ROOT / "data" / name, RESEARCH_DIR / name)
        out[s] = data.update(RESEARCH_DIR, s, u["timeframe"], u["history_start"], "coinbase")
    return out


def signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Per bar: buy (breakout above trend), atr. Row t uses only bars <= t."""
    e, t = cfg["entry"], cfg["trade"]
    c = df["close"]
    trend = ema(c, e["trend_ema"])
    prior_high = df["high"].rolling(e["breakout_bars"], min_periods=e["breakout_bars"]).max().shift(1)
    out = pd.DataFrame(index=df.index)
    out["buy"] = ((c > prior_high) & (c > trend)).fillna(False).astype(bool)
    out["atr"] = atr(df, t["atr_period"])
    return out


def run(cfg: dict, bars: dict[str, pd.DataFrame], start: str | None = None, end: str | None = None,
        fee_bps: float | None = None, slippage_bps: float | None = None,
        buy_filter: dict[str, pd.Series] | None = None) -> Result:
    """buy_filter (optional): per coin bool Series; a buy also needs True there (used by the learning filter)."""
    fee = cfg["costs"]["fee_bps"] if fee_bps is None else fee_bps
    slip = cfg["costs"]["slippage_bps"] if slippage_bps is None else slippage_bps
    cost = (fee + slip) / 10_000
    t, rk = cfg["trade"], cfg["risk"]
    stop_mult, target_r = t["stop_atr_mult"], t["target_r"]
    min_target = t["min_target_cost_mult"] * 2 * cost  # fraction of price
    time_stop = t["time_stop_bars"]
    risk_frac = rk["risk_per_trade_pct"] / 100
    max_pos = rk["max_position_pct"] / 100
    day_limit = rk["daily_loss_limit_pct"] / 100
    kill_dd = rk["max_drawdown_kill_pct"] / 100

    syms = list(bars)
    full = bars[syms[0]].index
    for s in syms[1:]:
        full = full.union(bars[s].index)
    idx = full
    if start:
        idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    if end:
        idx = idx[idx < pd.Timestamp(end, tz="UTC")]
    if len(idx) < 2:
        raise ValueError("not enough bars in the window")

    # signals on the FULL history (warm indicators), then cut to the window
    A: dict[str, dict[str, np.ndarray]] = {}
    for s in syms:
        df = bars[s].reindex(full)
        sig = signals(bars[s], cfg).reindex(full)
        if buy_filter is not None:
            sig["buy"] = sig["buy"].fillna(False).astype(bool) & \
                buy_filter.get(s, pd.Series(dtype=bool)).reindex(full).fillna(False).astype(bool)
        mark = df["close"].ffill()
        df, sig, mark = df.reindex(idx), sig.reindex(idx), mark.reindex(idx)
        A[s] = {"o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float),
                "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float),
                "mark": mark.to_numpy(float),
                "buy": sig["buy"].fillna(False).to_numpy(bool), "atr": sig["atr"].to_numpy(float)}

    equity0 = float(cfg["account"]["starting_equity"])
    cash = equity = peak = day_start_eq = equity0
    pos: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    last_exit: dict[str, int] = {}
    trades: list[Trade] = []
    eq = np.empty(len(idx))
    killed_at, day, halted, halt_days = None, None, False, 0
    days = idx.normalize()

    def close(s: str, p: dict, i: int, raw_px: float, reason: str):
        nonlocal cash
        px = raw_px * (1 - cost)
        cash += p["units"] * px
        pnl = p["units"] * (px - p["fill"])
        trades.append(Trade(s, "breakout", p["ts"], idx[i], p["fill"], px, p["units"], pnl,
                            100 * (px / p["fill"] - 1), reason, i - p["i"] + 1))
        last_exit[s] = i

    for i in range(len(idx)):
        if days[i] != day:
            day, day_start_eq, halted = days[i], equity, False

        for s in syms:
            a = A[s]
            if np.isnan(a["o"][i]):
                continue
            o, h, l = a["o"][i], a["h"][i], a["l"][i]

            # 1. fill pending order at this bar's open
            order = pending.pop(s, None)
            if order and order["side"] == "sell" and s in pos:
                close(s, pos.pop(s), i, o, order["reason"])
            elif order and order["side"] == "buy" and s not in pos:
                r = stop_mult * order["atr"]
                units = min(order["equity"] * risk_frac / r, order["equity"] * max_pos / o)
                fill = o * (1 + cost)
                units = min(units, cash / fill)
                if units * fill >= 10:
                    cash -= units * fill
                    pos[s] = {"units": units, "fill": fill, "ts": idx[i], "i": i,
                              "stop": o - r, "target": o + target_r * r}

            # 2. stop / target inside this bar (stop first if both touched; gaps fill at the open)
            p = pos.get(s)
            if p is not None:
                if l <= p["stop"]:
                    close(s, pos.pop(s), i, min(p["stop"], o), "stop")
                elif h >= p["target"]:
                    close(s, pos.pop(s), i, max(p["target"], o), "target")

        # 3. mark to market, risk state
        equity = cash + sum(p["units"] * A[s]["mark"][i] for s, p in pos.items())
        eq[i] = equity
        peak = max(peak, equity)
        if killed_at is None and equity <= peak * (1 - kill_dd):
            killed_at = idx[i]
        if not halted and equity <= day_start_eq * (1 - day_limit):
            halted, halt_days = True, halt_days + 1

        # 4. this closed bar -> orders for the next open
        for s in syms:
            a = A[s]
            if np.isnan(a["c"][i]) or s in pending:
                continue
            if s in pos:
                if i - pos[s]["i"] + 1 >= time_stop:
                    pending[s] = {"side": "sell", "reason": "time"}
                continue
            if not a["buy"][i] or halted or last_exit.get(s, -1) >= i:
                continue
            at = a["atr"][i]
            if not (np.isfinite(at) and at > 0):
                continue
            if target_r * stop_mult * at / a["c"][i] < min_target:
                continue  # fee filter: the move can't be big enough to beat costs
            pending[s] = {"side": "buy", "atr": at, "equity": equity}

    for s, p in list(pos.items()):
        close(s, pos.pop(s), len(idx) - 1, A[s]["mark"][-1], "end")
    eq[-1] = cash
    return Result(pd.Series(eq, index=idx, name="equity"), trades, killed_at, halt_days)


def report(cfg: dict, bars: dict[str, pd.DataFrame], period: str, note: str) -> str:
    """Run the locked test on one period and return a markdown report."""
    pr = cfg["periods"]
    start, end = (pr["dev_start"], pd.Timestamp(pr["dev_end"]) + pd.Timedelta(days=1)) if period == "dev" \
        else (pr["holdout_start"], None)
    end = str(end.date()) if end is not None else None
    eq0 = cfg["account"]["starting_equity"]
    res = run(cfg, bars, start, end)
    m = metrics(res, eq0)
    checks = verdict(m, cfg["passing_bar"])
    passed = all(checks.values())
    tdf = res.trades_df
    c = cfg["costs"]
    title = "DEV CHECK (not the verdict)" if period == "dev" else "HOLDOUT VERDICT"

    lines = [f"# 1h volatility breakout: {title}", "",
             f"{note} Spec: docs/BREAKOUT1H_SPEC.md. Settings: config_breakout1h.yaml.", "",
             f"Window: {res.equity.index[0]:%Y-%m-%d %H:%M} to {res.equity.index[-1]:%Y-%m-%d %H:%M} UTC", "",
             f"## Passing bar at real costs ({c['fee_bps']} bps fee + {c['slippage_bps']} bps slippage per side): "
             f"**{'PASS' if passed else 'FAIL'}**" + (" (dev period, for reference only)" if period == "dev" else ""), "",
             "| Check | Result | |", "|---|---|---|"]
    vals = [m["sharpe"], m["profit_factor"], m["total_return_pct"], m["n_trades"], m["pct_quarters_profitable"]]
    for (k, ok), v in zip(checks.items(), vals):
        lines.append(f"| {k} | {v:,.2f} | {'pass' if ok else 'FAIL'} |" if isinstance(v, float)
                     else f"| {k} | {v} | {'pass' if ok else 'FAIL'} |")
    lines += ["", f"Max drawdown {m['max_drawdown_pct']:.1f}%, win rate {m['win_rate_pct']:.1f}%, "
              f"daily loss halts {m['daily_halts']}, kill switch (20% DD, recorded only): {m['kill_switch_at'] or 'never'}", "",
              "## Diagnostics (NOT the verdict)", "", "Cost sensitivity (the fee filter scales with cost, so it loosens at lower cost):", "",
              "| Costs | Return % | Sharpe | PF | Trades |", "|---|---|---|---|---|",
              f"| real ({c['fee_bps']}+{c['slippage_bps']} bps) | {m['total_return_pct']:.2f} | {m['sharpe']:.2f} | "
              f"{m['profit_factor']:.2f} | {m['n_trades']} |"]
    for name, d in c["diagnostics"].items():
        dm = metrics(run(cfg, bars, start, end, d["fee_bps"], d["slippage_bps"]), eq0)
        lines.append(f"| {name} ({d['fee_bps']}+{d['slippage_bps']} bps) | {dm['total_return_pct']:.2f} | "
                     f"{dm['sharpe']:.2f} | {dm['profit_factor']:.2f} | {dm['n_trades']} |")
    lines += ["", "Buy and hold over the same window, for context:", ""]
    for s, b in bars.items():
        w = b["close"][(b.index >= res.equity.index[0]) & (b.index <= res.equity.index[-1])]
        lines.append(f"- {s}: {100 * (w.iloc[-1] / w.iloc[0] - 1):+.1f}%")
    lines += ["", "Per coin and per exit reason. Picking the best row after seeing this is curve fitting; "
              "any follow up needs its own locked spec.", ""]
    for by in ("symbol", "reason"):
        b = breakdown(tdf, by)
        if len(b):
            lines += ["```", b.round(2).to_string(), "```", ""]
    lines += ["Quarterly returns %:", "", "```", (100 * m["quarters"]).round(2).to_string(), "```", ""]
    return "\n".join(lines)
