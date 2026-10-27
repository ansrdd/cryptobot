"""1h breakout + learning trade filter (meta labeling). Spec: docs/BREAKOUT1H_LEARN_SPEC.md.

The locked breakout (bot/breakout1h.py) says WHEN a trade is possible. A logistic
regression, retrained monthly on past breakouts that already exited, says WHETHER
to take it: only if P(net win) clears the PF 1.3 breakeven for that trade's cost/R.

No lookahead, twice over:
  - features at a signal bar use only bars <= that bar
  - a candidate's label is only used for training once its trade has exited
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import breakout1h
from .config import ROOT
from .daytrade import breakdown, metrics, verdict
from .indicators import atr, ema

FEATURES = ["atr_pct", "atr_ratio", "trend_dist", "ema_slope", "brk_strength", "log_vol_ratio",
            "hour_sin", "hour_cos", "weekend", "ret_short", "ret_long", "btc_trend"]


def load_config(path: str | Path | None = None) -> tuple[dict, dict]:
    """Returns (base breakout config, learning config)."""
    with open(path or ROOT / "config_breakout1h_learn.yaml") as f:
        learn = yaml.safe_load(f)
    return breakout1h.load_config(ROOT / learn["base_config"]), learn


# ---------------------------------------------------------------- features
def features(bars: dict[str, pd.DataFrame], learn: dict) -> dict[str, pd.DataFrame]:
    f = learn["features"]
    mkt = bars[f["market_symbol"]]
    mkt_trend = mkt["close"] / ema(mkt["close"], f["trend_ema"]) - 1
    out = {}
    for s, df in bars.items():
        c, v = df["close"], df["volume"]
        a = atr(df, f["atr_period"])
        e = ema(c, f["trend_ema"])
        prior_high = df["high"].rolling(f["breakout_bars"], min_periods=f["breakout_bars"]).max().shift(1)
        hour = df.index.hour
        x = pd.DataFrame(index=df.index)
        x["atr_pct"] = a / c
        x["atr_ratio"] = a / a.rolling(f["atr_avg_bars"], min_periods=f["atr_avg_bars"]).mean()
        x["trend_dist"] = (c - e) / a
        x["ema_slope"] = (e - e.shift(f["ema_slope_bars"])) / c
        x["brk_strength"] = (c - prior_high) / a
        vm = v.rolling(f["volume_avg_bars"], min_periods=f["volume_avg_bars"]).mean()
        x["log_vol_ratio"] = np.log((v + 1e-9) / (vm + 1e-9))
        x["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        x["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        x["weekend"] = (df.index.day_of_week >= 5).astype(float)
        x["ret_short"] = c / c.shift(f["ret_short_bars"]) - 1
        x["ret_long"] = c / c.shift(f["ret_long_bars"]) - 1
        # market trend at or before this bar (ffill only carries the past forward)
        x["btc_trend"] = mkt_trend.reindex(mkt_trend.index.union(df.index)).ffill().reindex(df.index)
        out[s] = x.replace([np.inf, -np.inf], np.nan)
    return out


# ---------------------------------------------------------------- candidates + labels
def candidates(bars: dict[str, pd.DataFrame], base: dict) -> pd.DataFrame:
    """Every breakout the plain strategy would take per coin (one at a time, after the fee
    filter), ignoring portfolio limits, simulated with the exact trade rules.
    One row per candidate: signal_ts, entry/exit, net return after real costs, label, k."""
    t = base["trade"]
    c = base["costs"]
    cost = (c["fee_bps"] + c["slippage_bps"]) / 10_000
    rt = 2 * cost
    stop_mult, target_r, time_stop = t["stop_atr_mult"], t["target_r"], t["time_stop_bars"]
    min_target = t["min_target_cost_mult"] * rt
    rows = []
    for s, df in bars.items():
        sig = breakout1h.signals(df, base)
        o, h, l, cl = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        buy, at = sig["buy"].to_numpy(bool), sig["atr"].to_numpy(float)
        idx, n = df.index, len(df)
        i = 0
        while i < n - 1:
            if not buy[i] or not (np.isfinite(at[i]) and at[i] > 0) \
                    or target_r * stop_mult * at[i] / cl[i] < min_target:
                i += 1
                continue
            e = i + 1                              # fill at the next open
            r = stop_mult * at[i]
            stop, target = o[e] - r, o[e] + target_r * r
            j, exit_i, px, reason = e, None, None, None
            while j < n:
                if l[j] <= stop:
                    exit_i, px, reason = j, min(stop, o[j]), "stop"
                    break
                if h[j] >= target:
                    exit_i, px, reason = j, max(target, o[j]), "target"
                    break
                if j - e + 1 >= time_stop and j + 1 < n:
                    exit_i, px, reason = j + 1, o[j + 1], "time"
                    break
                j += 1
            if exit_i is None:
                break                              # still open at the end of the data: no label yet
            fill, out_px = o[e] * (1 + cost), px * (1 - cost)
            net = out_px / fill - 1
            rows.append({"symbol": s, "signal_ts": idx[i], "entry_ts": idx[e], "exit_ts": idx[exit_i],
                         "reason": reason, "net_ret": net, "label": int(net > 0),
                         "k": rt / (r / o[e])})
            i = exit_i + 1                         # a new entry needs a signal on a later bar
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- model
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    """L2 logistic regression by Newton's method. Intercept (first column of ones added here) not penalized.
    Minimizes sum(logloss) + l2/2 * ||w||^2, same as sklearn with C = 1/l2."""
    Xb = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(Xb.shape[1])
    pen = np.full(Xb.shape[1], l2)
    pen[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) + pen * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(pen)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-(w[0] + X @ w[1:])))


def p_needed(k: np.ndarray | float, target_r: float, pf: float) -> np.ndarray | float:
    """Win probability needed for profit factor `pf` when wins net (target_r - k) R and losses cost (1 + k) R."""
    return pf * (1 + k) / ((target_r - k) + pf * (1 + k))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank based AUC (0.5 = coin flip)."""
    y, p = np.asarray(y), np.asarray(p)
    pos, neg = y.sum(), len(y) - y.sum()
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = pd.Series(p).rank().to_numpy()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


# ---------------------------------------------------------------- walk forward
def walk_forward(bars: dict[str, pd.DataFrame], base: dict, learn: dict, start: str | None = None,
                 end: str | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Monthly retrain on candidates that already exited; score candidates signalled that month.
    Returns (candidates with prob / p_needed / approve columns for the window, model history)."""
    m, cut = learn["model"], learn["cutoff"]
    cands = candidates(bars, base)
    feats = features(bars, learn)
    X = np.vstack([feats[r.symbol].loc[r.signal_ts, FEATURES].to_numpy(float) for r in cands.itertuples()]) \
        if len(cands) else np.empty((0, len(FEATURES)))
    ok = np.isfinite(X).all(axis=1)
    known_at = cands["exit_ts"] + pd.Timedelta(hours=1) if len(cands) else pd.Series(dtype="datetime64[ns, UTC]")  # exit bar closed

    lo = pd.Timestamp(start, tz="UTC") if start else cands.signal_ts.min()
    hi = pd.Timestamp(end, tz="UTC") if end else max(df.index[-1] for df in bars.values()) + pd.Timedelta(hours=1)
    months = pd.date_range(lo.normalize().replace(day=1), hi, freq="MS", tz="UTC")
    cands = cands.assign(prob=np.nan, p_needed=p_needed(cands["k"], base["trade"]["target_r"], cut["target_pf"])
                         if len(cands) else [], approve=False)
    history = []
    for T, T_next in zip(months, list(months[1:]) + [hi]):
        train = ok & (known_at <= T).to_numpy()
        score = ok & (cands.signal_ts >= max(T, lo)).to_numpy() & (cands.signal_ts < T_next).to_numpy()
        if not score.any():
            continue
        if train.sum() < m["min_train_samples"]:
            history.append({"retrain": T, "n_train": int(train.sum()), "weights": None})
            continue
        Xt, yt = X[train], cands["label"].to_numpy()[train]
        mu, sd = Xt.mean(axis=0), Xt.std(axis=0)
        sd[sd == 0] = 1.0
        w = fit_logistic((Xt - mu) / sd, yt, m["l2_penalty"])
        history.append({"retrain": T, "n_train": int(train.sum()), "weights": w})
        p = predict(w, (X[score] - mu) / sd)
        cands.loc[score, "prob"] = p
        cands.loc[score, "approve"] = p >= cands.loc[score, "p_needed"].to_numpy()
    in_window = (cands.signal_ts >= lo) & (cands.signal_ts < hi)
    return cands[in_window].reset_index(drop=True), history


def buy_filter(scored: pd.DataFrame, bars: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    out = {}
    for s, df in bars.items():
        f = pd.Series(False, index=df.index)
        taken = scored[(scored.symbol == s) & scored.approve].signal_ts
        f.loc[f.index.isin(taken)] = True
        out[s] = f
    return out


def run(bars: dict[str, pd.DataFrame], base: dict, learn: dict, start: str | None = None,
        end: str | None = None, fee_bps: float | None = None, slippage_bps: float | None = None):
    """Approvals always come from the model trained on real cost labels; fee_bps/slippage_bps only
    change execution costs (cost diagnostics)."""
    scored, history = walk_forward(bars, base, learn, start, end)
    res = breakout1h.run(base, bars, start, end, fee_bps, slippage_bps, buy_filter=buy_filter(scored, bars))
    return res, scored, history


# ---------------------------------------------------------------- report
def report(bars: dict[str, pd.DataFrame], base: dict, learn: dict, period: str, note: str) -> str:
    pr = base["periods"]
    if period == "dev":
        start, end = pr["dev_start"], str((pd.Timestamp(pr["dev_end"]) + pd.Timedelta(days=1)).date())
    else:
        start, end = pr["holdout_start"], None
    eq0 = base["account"]["starting_equity"]
    res, scored, history = run(bars, base, learn, start, end)
    m = metrics(res, eq0)
    checks = verdict(m, base["passing_bar"])
    passed = all(checks.values())
    c = base["costs"]
    title = "DEV CHECK (not the verdict)" if period == "dev" else "HOLDOUT VERDICT"

    lines = [f"# 1h breakout + learning filter: {title}", "",
             f"{note} Spec: docs/BREAKOUT1H_LEARN_SPEC.md. Settings: config_breakout1h_learn.yaml + config_breakout1h.yaml.", "",
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
              "## Diagnostics (NOT the verdict)", "", "### Did it learn anything", ""]
    s = scored.dropna(subset=["prob"])
    taken, skipped = s[s.approve], s[~s.approve]
    lines += [f"- Candidates in window: {len(scored)}, scored by a trained model: {len(s)} "
              f"(the rest came before {learn['model']['min_train_samples']} labeled trades existed, or had missing inputs)",
              f"- Out of sample AUC: {auc(s.label.to_numpy(), s.prob.to_numpy()):.3f} (0.5 = coin flip)",
              f"- Taken: {len(taken)}, net win rate {100 * taken.label.mean():.1f}%, avg net return {100 * taken.net_ret.mean():+.3f}%"
              if len(taken) else "- Taken: 0",
              f"- Skipped: {len(skipped)}, net win rate {100 * skipped.label.mean():.1f}%, avg net return {100 * skipped.net_ret.mean():+.3f}%"
              if len(skipped) else "- Skipped: 0",
              f"- Typical win rate needed (median p_needed): {100 * s.p_needed.median():.1f}%" if len(s) else "", ""]
    plain = breakout1h.run(base, bars, start, end)
    pm = metrics(plain, eq0)
    lines += ["### Plain breakout, same window (no filter)", "",
              f"Return {pm['total_return_pct']:.2f}%, Sharpe {pm['sharpe']:.2f}, PF {pm['profit_factor']:.2f}, {pm['n_trades']} trades", "",
              "### Cost sensitivity (approvals fixed from the real cost model; only execution costs change)", "",
              "| Costs | Return % | Sharpe | PF | Trades |", "|---|---|---|---|---|",
              f"| real ({c['fee_bps']}+{c['slippage_bps']} bps) | {m['total_return_pct']:.2f} | {m['sharpe']:.2f} | "
              f"{m['profit_factor']:.2f} | {m['n_trades']} |"]
    filt = buy_filter(scored, bars)
    for name, d in c["diagnostics"].items():
        dm = metrics(breakout1h.run(base, bars, start, end, d["fee_bps"], d["slippage_bps"], buy_filter=filt), eq0)
        lines.append(f"| {name} ({d['fee_bps']}+{d['slippage_bps']} bps) | {dm['total_return_pct']:.2f} | "
                     f"{dm['sharpe']:.2f} | {dm['profit_factor']:.2f} | {dm['n_trades']} |")
    lines += ["", "### Model weights over time (standardized inputs; + means more likely to win)", ""]
    fitted = [h for h in history if h["weights"] is not None]
    if fitted:
        pick = [fitted[0], fitted[len(fitted) // 2], fitted[-1]]
        wt = pd.DataFrame({f"{h['retrain']:%Y-%m} (n={h['n_train']})": h["weights"][1:] for h in pick}, index=FEATURES)
        signs = np.sign(np.vstack([h["weights"][1:] for h in fitted]))
        wt["same sign % of months"] = 100 * np.abs(signs.mean(axis=0))
        lines += ["```", wt.round(3).to_string(), "```", ""]
    tdf = res.trades_df
    lines += ["Per coin and per exit reason. Picking the best row after seeing this is curve fitting; "
              "any follow up needs its own locked spec.", ""]
    for by in ("symbol", "reason"):
        b = breakdown(tdf, by)
        if len(b):
            lines += ["```", b.round(2).to_string(), "```", ""]
    lines += ["Quarterly returns %:", "", "```", (100 * m["quarters"]).round(2).to_string(), "```", ""]
    return "\n".join(lines)


def with_min_train(learn: dict, n: int) -> dict:
    out = copy.deepcopy(learn)
    out["model"]["min_train_samples"] = n
    return out
