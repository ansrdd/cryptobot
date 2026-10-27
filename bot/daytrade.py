"""Day trading backtest engine for the QuantFlow clone (5m bars, long only).

Rules come from docs/DAYTRADE_SPEC.md (locked before any backtest):
  - signals on a closed bar, filled at the NEXT bar's open
  - fee + slippage on every fill
  - stop 1.5 x ATR below entry, target 2R, stop moves to entry the bar AFTER +1R is touched
  - stop and target both touched in one bar -> the stop wins
  - one position per coin, an opposite (SELL) signal exits at the next open
  - 1% risk per trade, max 30% of equity per coin, 3% daily loss halts new entries
  - 20% drawdown kill switch is recorded only, the test keeps going

Separate from bot/backtest.py on purpose: the live daily bot doesn't touch this.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import quantflow
from .config import ROOT


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config_daytrade.yaml") as f:
        return yaml.safe_load(f)


@dataclass
class Trade:
    symbol: str
    source: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float   # fill incl. costs
    exit_price: float    # fill incl. costs
    units: float
    pnl: float
    pnl_pct: float
    reason: str          # target | stop | breakeven | signal | end
    bars_held: int


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade]
    killed_at: pd.Timestamp | None = None
    halt_days: int = 0

    @property
    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=list(Trade.__dataclass_fields__))
        return pd.DataFrame([t.__dict__ for t in self.trades])


def run(cfg: dict, bars: dict[str, pd.DataFrame], fee_bps: float | None = None,
        slippage_bps: float | None = None) -> Result:
    fee = cfg["costs"]["fee_bps"] if fee_bps is None else fee_bps
    slip = cfg["costs"]["slippage_bps"] if slippage_bps is None else slippage_bps
    cost = (fee + slip) / 10_000
    t, rk = cfg["trade"], cfg["risk"]
    stop_mult, target_r, be_r = t["stop_atr_mult"], t["target_r"], t["breakeven_at_r"]
    risk_frac = rk["risk_per_trade_pct"] / 100
    max_pos = rk["max_position_pct"] / 100
    day_limit = rk["daily_loss_limit_pct"] / 100
    kill_dd = rk["max_drawdown_kill_pct"] / 100
    exit_on_sell = t["exit_on_opposite_signal"]

    syms = list(bars)
    idx = bars[syms[0]].index
    for s in syms[1:]:
        idx = idx.union(bars[s].index)

    # numpy arrays on the shared index; NaN where a coin has no bar (Coinbase skips empty candles)
    A: dict[str, dict[str, np.ndarray]] = {}
    for s in syms:
        df = bars[s].reindex(idx)
        sig = quantflow.signals(bars[s], cfg).reindex(idx)
        A[s] = {
            "o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float),
            "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float),
            "mark": df["close"].ffill().to_numpy(float),
            "buy": sig["buy"].fillna(False).to_numpy(bool),
            "sell": sig["sell"].fillna(False).to_numpy(bool),
            "paused": sig["paused"].fillna(False).to_numpy(bool),
            "atr": sig["atr"].to_numpy(float),
            "source": sig["source"].fillna("").to_numpy(object),
        }

    equity0 = float(cfg["account"]["starting_equity"])
    cash = equity0
    pos: dict[str, dict] = {}            # open position per coin
    pending: dict[str, dict] = {}        # order to fill at the coin's next open
    trades: list[Trade] = []
    eq = np.empty(len(idx))
    peak = equity = equity0
    killed_at = None
    day = None
    day_start_eq = equity0
    halted = False
    halt_days = 0
    days = idx.normalize()

    def close(s: str, p: dict, i: int, raw_px: float, reason: str):
        nonlocal cash
        px = raw_px * (1 - cost)
        cash += p["units"] * px
        pnl = p["units"] * (px - p["fill"])
        trades.append(Trade(s, p["source"], p["ts"], idx[i], p["fill"], px, p["units"], pnl,
                            100 * (px / p["fill"] - 1), reason, i - p["i"] + 1))

    for i in range(len(idx)):
        # new UTC day: reset the daily loss halt, measured from yesterday's last equity
        if days[i] != day:
            day, day_start_eq, halted = days[i], equity, False

        for s in syms:
            a = A[s]
            if np.isnan(a["o"][i]):
                continue  # no bar for this coin; pending orders wait for its next bar
            o, h, l = a["o"][i], a["h"][i], a["l"][i]

            # 1. fill pending order at this bar's open
            order = pending.pop(s, None)
            if order and order["side"] == "sell" and s in pos:
                close(s, pos.pop(s), i, o, "signal")
            elif order and order["side"] == "buy" and s not in pos:
                r = stop_mult * order["atr"]                     # 1R in price
                units = min(order["equity"] * risk_frac / r,     # 1% risk
                            order["equity"] * max_pos / o)       # 30% cap
                fill = o * (1 + cost)
                units = min(units, cash / fill)
                if units * fill >= 10:  # skip dust
                    cash -= units * fill
                    pos[s] = {"units": units, "fill": fill, "entry": o, "ts": idx[i], "i": i,
                              "source": order["source"], "stop": o - r, "target": o + target_r * r,
                              "be_level": o + be_r * r, "be": False}

            # 2. stop / target inside this bar (stop first if both touched)
            p = pos.get(s)
            if p is not None:
                if l <= p["stop"]:
                    close(s, pos.pop(s), i, min(p["stop"], o), "breakeven" if p["be"] else "stop")
                elif h >= p["target"]:
                    close(s, pos.pop(s), i, max(p["target"], o), "target")
                elif h >= p["be_level"] and not p["be"]:
                    # takes effect from the next bar on
                    p["stop"], p["be"] = max(p["stop"], p["entry"]), True

        # 3. mark to market, risk state
        equity = cash + sum(p["units"] * A[s]["mark"][i] for s, p in pos.items())
        eq[i] = equity
        peak = max(peak, equity)
        if killed_at is None and equity <= peak * (1 - kill_dd):
            killed_at = idx[i]  # recorded, not enforced (spec)
        if not halted and equity <= day_start_eq * (1 - day_limit):
            halted = True
            halt_days += 1

        # 4. signals on this closed bar -> orders for the next open
        for s in syms:
            a = A[s]
            if np.isnan(a["c"][i]) or s in pending:
                continue
            if s in pos:
                if exit_on_sell and a["sell"][i]:
                    pending[s] = {"side": "sell"}
            elif (a["buy"][i] and not a["sell"][i] and not a["paused"][i] and not halted
                  and np.isfinite(a["atr"][i]) and a["atr"][i] > 0):
                pending[s] = {"side": "buy", "atr": a["atr"][i], "equity": equity,
                              "source": a["source"][i]}

    # close anything still open at the last bar's close so every trade is counted
    for s, p in list(pos.items()):
        close(s, pos.pop(s), len(idx) - 1, A[s]["mark"][-1], "end")
    eq[-1] = cash
    return Result(pd.Series(eq, index=idx, name="equity"), trades, killed_at, halt_days)


# ---------------------------------------------------------------- metrics
def _pf(pnl: pd.Series) -> float:
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return gains / losses


def metrics(res: Result, equity0: float, periods_per_year: int = 365) -> dict:
    """periods_per_year: 365 for 24/7 crypto, 252 for stocks (Sharpe from daily returns)."""
    daily = res.equity.resample("1D").last().dropna()
    dret = pd.concat([pd.Series([equity0]), daily.reset_index(drop=True)]).pct_change().dropna()
    sharpe = dret.mean() / dret.std() * np.sqrt(periods_per_year) if dret.std() > 0 else float("nan")
    q_end = res.equity.resample("QE").last().dropna()
    q_ret = pd.concat([pd.Series([equity0]), q_end.reset_index(drop=True)]).pct_change().dropna()
    q_ret.index = q_end.index.tz_localize(None).to_period("Q")
    tdf = res.trades_df
    dd = res.equity / res.equity.cummax() - 1
    return {
        "total_return_pct": 100 * (res.equity.iloc[-1] / equity0 - 1),
        "sharpe": float(sharpe),
        "profit_factor": float(_pf(tdf.pnl)) if len(tdf) else float("nan"),
        "n_trades": int(len(tdf)),
        "win_rate_pct": 100 * float((tdf.pnl > 0).mean()) if len(tdf) else float("nan"),
        "max_drawdown_pct": 100 * float(dd.min()),
        "pct_quarters_profitable": 100 * float((q_ret > 0).mean()) if len(q_ret) else float("nan"),
        "n_quarters": int(len(q_ret)),
        "quarters": q_ret,
        "kill_switch_at": res.killed_at,
        "daily_halts": res.halt_days,
    }


def verdict(m: dict, bar: dict) -> dict[str, bool]:
    return {
        f"Sharpe >= {bar['min_sharpe']}": m["sharpe"] >= bar["min_sharpe"],
        f"Profit factor >= {bar['min_profit_factor']}": m["profit_factor"] >= bar["min_profit_factor"],
        f"Total return > {bar['min_total_return_pct']}%": m["total_return_pct"] > bar["min_total_return_pct"],
        f"Trades >= {bar['min_trades']}": m["n_trades"] >= bar["min_trades"],
        f"Quarters profitable >= {bar['min_pct_quarters_profitable']}%":
            m["pct_quarters_profitable"] >= bar["min_pct_quarters_profitable"],
    }


def breakdown(tdf: pd.DataFrame, by: str) -> pd.DataFrame:
    """Diagnostics only. Picking the best row after seeing this is curve fitting."""
    if not len(tdf):
        return pd.DataFrame()
    g = tdf.groupby(by)["pnl"]
    return pd.DataFrame({"trades": g.size(), "pnl": g.sum(), "win_rate_pct": g.apply(lambda x: 100 * (x > 0).mean()),
                         "profit_factor": g.apply(_pf)})


def with_costs(cfg: dict, fee_bps: float, slippage_bps: float) -> dict:
    c = copy.deepcopy(cfg)
    c["costs"]["fee_bps"], c["costs"]["slippage_bps"] = fee_bps, slippage_bps
    return c
