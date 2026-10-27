"""Event-driven backtester. Bar-by-bar, same RiskManager as the live runner.

Fills happen at the *next* bar's open (you can't trade on a close you just saw),
with fee + slippage applied both sides. Stops are checked against the bar's low.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk import RiskManager
from .strategies import Strategy, build, regime_series

log = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    strategy: str
    units: float
    entry_price: float
    entry_ts: pd.Timestamp
    stop: float
    trail_mult: float
    atr: float
    high_water: float
    max_hold: int | None
    bars_held: int = 0


@dataclass
class Trade:
    symbol: str
    strategy: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    fees: float
    reason: str
    bars_held: int


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade]
    benchmark: pd.Series
    decisions: int = 0
    killed_at: pd.Timestamp | None = None
    kill_reason: str = ""

    @property
    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f.name for f in Trade.__dataclass_fields__.values()])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _cost(cfg) -> float:
    """Round-trip-per-side cost as a fraction."""
    return (cfg.costs.taker_fee_bps + cfg.costs.slippage_bps) / 10_000


def run(cfg, data: dict[str, pd.DataFrame], start: str | None = None, end: str | None = None,
        strategies: list[Strategy] | None = None, starting_equity: float | None = None) -> Result:
    strategies = strategies or build(cfg)
    equity0 = starting_equity or cfg.account.starting_equity
    cost = _cost(cfg)
    syms = list(data)

    # ---- precompute signals & regime per symbol
    sig: dict[str, dict[str, pd.DataFrame]] = {s: {} for s in syms}
    regime: dict[str, pd.Series] = {}
    for s in syms:
        df = data[s]
        for st in strategies:
            sig[s][st.name] = st.signals(df)
        regime[s] = regime_series(df, cfg)

    # common index
    idx = data[syms[0]].index
    for s in syms[1:]:
        idx = idx.intersection(data[s].index)
    warm = max(st.warmup() for st in strategies)
    idx = idx[warm:]
    if start:
        idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    if end:
        idx = idx[idx < pd.Timestamp(end, tz="UTC")]
    if len(idx) < 10:
        raise ValueError("not enough bars after warmup/date filter")

    cash = equity0
    positions: dict[tuple[str, str], Position] = {}
    trades: list[Trade] = []
    eq_curve = np.empty(len(idx))
    risk = RiskManager(cfg, equity0)
    weights = dict(cfg.regime.weights) if cfg.regime.enabled else None
    killed_at, kill_reason = None, ""
    decisions = 0

    # pending orders to fill at next open: list of (sym, strat, 'buy'|'sell', units, reason)
    pending: list[tuple] = []

    def mark(ts_i: int) -> float:
        total = cash
        for (s, _), p in positions.items():
            total += p.units * data[s]["close"].iat[data[s].index.get_loc(idx[ts_i])]
        return total

    for i, ts in enumerate(idx):
        # ---------- 1. fill pending orders at this bar's OPEN
        for (s, stname, side, units, reason) in pending:
            o = data[s].at[ts, "open"]
            if side == "buy":
                px = o * (1 + cost)
                notional = units * px
                if notional > cash:
                    units = cash / px * 0.999
                    notional = units * px
                if units <= 0:
                    continue
                cash -= notional
                st_sig = sig[s][stname].loc[ts]
                positions[(s, stname)] = Position(
                    symbol=s, strategy=stname, units=units, entry_price=px, entry_ts=ts,
                    stop=float(st_sig["stop"]), trail_mult=float(st_sig["trail_mult"]),
                    atr=float(st_sig["atr"]), high_water=o,
                    max_hold=int(st_sig["max_hold"]) if "max_hold" in st_sig and not np.isnan(st_sig["max_hold"]) else None,
                )
            else:
                p = positions.pop((s, stname), None)
                if p is None:
                    continue
                px = o * (1 - cost)
                cash += p.units * px
                fees = p.units * (p.entry_price + px) * cost / (1 + cost)  # approx
                pnl = p.units * (px - p.entry_price)
                trades.append(Trade(s, stname, p.entry_ts, ts, p.entry_price, px, p.units,
                                    pnl, 100 * (px / p.entry_price - 1), fees, reason, p.bars_held))
                risk.on_trade_closed(pnl)
        pending = []

        # ---------- 2. intrabar stop checks (against LOW), fill at stop w/ slippage
        for key, p in list(positions.items()):
            s = key[0]
            row = data[s].loc[ts]
            p.bars_held += 1
            # update trailing stop on highest close
            if p.trail_mult > 0:
                p.high_water = max(p.high_water, row["close"])
                p.stop = max(p.stop, p.high_water - p.trail_mult * float(sig[s][p.strategy].at[ts, "atr"]))
            if row["low"] <= p.stop:
                px = min(p.stop, row["open"]) * (1 - cost)  # gap-through handled
                cash += p.units * px
                pnl = p.units * (px - p.entry_price)
                trades.append(Trade(s, p.strategy, p.entry_ts, ts, p.entry_price, px, p.units,
                                    pnl, 100 * (px / p.entry_price - 1), p.units * px * cost, "stop", p.bars_held))
                risk.on_trade_closed(pnl)
                del positions[key]

        # ---------- 3. mark-to-market, risk gates
        equity = cash + sum(p.units * data[k[0]].at[ts, "close"] for k, p in positions.items())
        eq_curve[i] = equity
        risk.on_bar(ts, equity)
        if risk.state.killed and killed_at is None:
            killed_at, kill_reason = ts, risk.state.kill_reason
            log.warning("KILL SWITCH at %s: %s", ts, kill_reason)

        if risk.must_flatten():
            for (s, stname) in list(positions):
                pending.append((s, stname, "sell", 0, "risk_flatten"))
            continue

        # ---------- 4. evaluate signals, queue orders for next open
        gross = sum(p.units * data[k[0]].at[ts, "close"] for k, p in positions.items())
        for s in syms:
            reg = regime[s].at[ts]
            for st in strategies:
                decisions += 1
                row = sig[s][st.name].loc[ts]
                key = (s, st.name)
                if key in positions:
                    p = positions[key]
                    time_out = p.max_hold is not None and p.bars_held >= p.max_hold
                    if bool(row["exit"]) or time_out:
                        pending.append((s, st.name, "sell", 0, "time_stop" if time_out else "signal"))
                    continue
                if not bool(row["entry"]) or np.isnan(row["stop"]) or np.isnan(row["atr"]):
                    continue
                ok, _why = risk.can_open(len(positions) + sum(1 for q in pending if q[2] == "buy"), gross, equity)
                if not ok:
                    continue
                w = weights[reg][st.name] if weights else 1.0
                px = data[s].at[ts, "close"]
                units = risk.size(equity, px, float(row["stop"]), weight=w, gross_exposure=gross)
                if units * px < 10:  # dust
                    continue
                gross += units * px
                pending.append((s, st.name, "buy", units, "signal"))

    equity_s = pd.Series(eq_curve, index=idx, name="equity")
    bench_sym = cfg.backtest.benchmark if cfg.backtest.benchmark in data else syms[0]
    bc = data[bench_sym]["close"].reindex(idx)
    benchmark = (equity0 * bc / bc.iloc[0]).rename("benchmark")
    return Result(equity_s, trades, benchmark, decisions, killed_at, kill_reason)


# ---------------------------------------------------------------- metrics
def _bar_hours(idx: pd.DatetimeIndex) -> float:
    """Median spacing of the series in hours (1 for hourly, 24 for daily)."""
    if len(idx) < 3:
        return 1.0
    return float(pd.Series(idx).diff().dropna().median() / pd.Timedelta(hours=1))


def metrics(res: Result, bars_per_year: float | None = None) -> dict:
    eq = res.equity
    bar_h = _bar_hours(eq.index)
    bars_per_year = bars_per_year or 24 * 365 / bar_h
    ret = eq.pct_change().dropna()
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = len(eq) / bars_per_year
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else float("nan")
    vol = ret.std() * np.sqrt(bars_per_year)
    sharpe = (ret.mean() * bars_per_year) / vol if vol > 0 else float("nan")
    dd = eq / eq.cummax() - 1
    maxdd = dd.min()
    calmar = cagr / abs(maxdd) if maxdd < 0 else float("nan")
    tdf = res.trades_df
    wins = tdf[tdf.pnl > 0] if len(tdf) else tdf
    losses = tdf[tdf.pnl <= 0] if len(tdf) else tdf
    pf = wins.pnl.sum() / abs(losses.pnl.sum()) if len(losses) and losses.pnl.sum() != 0 else float("inf") if len(wins) else float("nan")
    bench_total = res.benchmark.iloc[-1] / res.benchmark.iloc[0] - 1
    bdd = (res.benchmark / res.benchmark.cummax() - 1).min()
    # longest underwater stretch in days
    uw = (dd < 0).astype(int)
    longest = 0; cur = 0
    for v in uw.values:
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    return {
        "total_return_pct": 100 * total,
        "cagr_pct": 100 * cagr,
        "sharpe": sharpe,
        "max_drawdown_pct": 100 * maxdd,
        "calmar": calmar,
        "profit_factor": pf,
        "n_trades": int(len(tdf)),
        "win_rate_pct": 100 * len(wins) / len(tdf) if len(tdf) else float("nan"),
        "avg_win": wins.pnl.mean() if len(wins) else 0.0,
        "avg_loss": losses.pnl.mean() if len(losses) else 0.0,
        "longest_underwater_days": longest * bar_h / 24,
        "benchmark_return_pct": 100 * bench_total,
        "benchmark_max_dd_pct": 100 * bdd,
        "bars": int(len(eq)),
        "killed": res.killed_at is not None,
    }


def walk_forward(cfg, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rolling out-of-sample test. We don't re-optimize params (that's the trap);
    we just report how each OOS window did so you can see consistency.

    A strategy that only works in 2 of 8 windows is a regime bet, not an edge.
    """
    tm, sm = cfg.backtest.walk_forward.train_months, cfg.backtest.walk_forward.test_months
    idx = data[cfg.universe.symbols[0]].index
    first = idx[0] + pd.DateOffset(months=tm)
    rows = []
    cur = first
    while cur + pd.DateOffset(months=sm) <= idx[-1]:
        stop = cur + pd.DateOffset(months=sm)
        try:
            # feed the engine from `train_months` earlier so indicators are warm,
            # but only *score* the test window
            r = run(cfg, data, start=str(cur.date()), end=str(stop.date()))
            m = metrics(r)
            rows.append({"window_start": cur.date(), "window_end": stop.date(),
                         "return_pct": m["total_return_pct"], "bench_pct": m["benchmark_return_pct"],
                         "sharpe": m["sharpe"], "max_dd_pct": m["max_drawdown_pct"],
                         "trades": m["n_trades"], "beat_bench": m["total_return_pct"] > m["benchmark_return_pct"]})
        except ValueError:
            pass
        cur = stop
    return pd.DataFrame(rows)
