"""Global Trend engine (bot/global_trend.py): signals, weights, fills, costs, no lookahead."""
import numpy as np
import pandas as pd
import pytest

from bot import global_trend as gt

CFG = gt.load_config()
RISK = CFG["universe"]["risk_assets"]
CASH = CFG["universe"]["cash_asset"]


def _bars(drift: dict | None = None, days: int = 800, seed: int = 3, vol: dict | None = None) -> pd.DataFrame:
    """Daily bars for the 9 ETFs. drift: daily log drift per symbol (default 0). Open = previous close."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-04", periods=days)
    frames = {}
    for s in RISK + [CASH]:
        mu = (drift or {}).get(s, 0.0)
        sd = (vol or {}).get(s, 0.01)
        r = rng.normal(mu, sd, days)
        c = 100 * np.exp(np.cumsum(r))
        o = np.r_[100, c[:-1]] * (1 + rng.normal(0, 0.001, days))
        frames[s] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c}, index=idx)
    return pd.concat(frames, axis=1).swaplevel(0, 1, axis=1).sort_index(axis=1)


def test_month_ends_are_last_trading_days():
    idx = pd.bdate_range("2024-01-01", "2024-03-31")
    me = gt.month_ends(idx)
    assert list(me.strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-29", "2024-03-29"]


def test_trend_score_up_and_down():
    b = _bars({"SPY": 0.003, "TLT": -0.003}, vol={"SPY": 0.001, "TLT": 0.001})
    close = b["close"]
    d = gt.month_ends(close.index)[-1:]
    sc = gt.trend_scores(close[RISK], d, [1, 3, 12])
    assert sc.loc[d[0], "SPY"] == 1.0 and sc.loc[d[0], "TLT"] == 0.0


def test_trend_score_needs_full_history():
    close = _bars(days=200)["close"]
    me = gt.month_ends(close.index)
    sc = gt.trend_scores(close[RISK], me, [1, 3, 12])
    assert sc.iloc[:12].isna().all().all()


def test_weights_never_leverage_and_cash_takes_the_rest():
    b = _bars({"SPY": 0.002, "GLD": 0.002})
    close = b["close"]
    w = gt.strategy_targets(close, gt.month_ends(close.index), CFG)
    assert np.allclose(w.sum(axis=1), 1.0)
    assert (w[RISK] >= 0).all().all() and (w[CASH] >= -1e-12).all()


def test_risk_weights_favor_calmer_assets():
    b = _bars(vol={"SPY": 0.02, "IEF": 0.005})
    close = b["close"]
    d = gt.month_ends(close.index)[-1:]
    rw = gt.risk_weights(close[RISK], d, 63)
    assert rw.loc[d[0], "IEF"] > 3 * rw.loc[d[0], "SPY"]
    assert rw.loc[d[0]].sum() == pytest.approx(1.0)


def test_decision_ignores_prices_after_it():
    b = _bars({"SPY": 0.001})
    close = b["close"]
    me = gt.month_ends(close.index)
    d = me[20]
    w1 = gt.strategy_targets(close, me, CFG).loc[d]
    b2 = b.copy()
    after = b2.index > d
    b2.loc[after, ("close", "SPY")] *= 5
    w2 = gt.strategy_targets(b2["close"], me, CFG).loc[d]
    pd.testing.assert_series_equal(w1, w2)


def test_fills_at_next_open_and_costs():
    b = _bars()
    close = b["close"]
    me = gt.month_ends(close.index)
    tg = gt.fixed_targets(me, {"SPY": 1.0})
    res = gt.run(b, tg, str(me[5].date()), None, 2, 0.5, 100000)
    first = res.equity.index[0]
    assert first == close.index[close.index.get_loc(me[4]) + 1] or first == close.index[close.index.get_loc(me[5]) + 1]
    # $100k of SPY bought at that open, the 2 bps fee ($20) paid from cash
    o, c = b["open"]["SPY"].loc[first], close["SPY"].loc[first]
    assert res.equity.iloc[0] == pytest.approx(-20 + 100000 / o * c)
    assert res.costs == pytest.approx(20)
    assert res.trades == 1                      # later months: already 100% SPY, drift under 0.5% skipped or tiny


def test_zero_cost_beats_real_cost():
    b = _bars({"SPY": 0.001, "EEM": -0.001})
    close = b["close"]
    me = gt.month_ends(close.index)
    tg = gt.strategy_targets(close, me, CFG)
    st = str(me[14].date())
    r0 = gt.run(b, tg, st, None, 0, 0.5, 100000)
    r2 = gt.run(b, tg, st, None, 2, 0.5, 100000)
    assert r0.equity.iloc[-1] > r2.equity.iloc[-1] and r0.costs == 0 and r2.costs > 0


def test_min_trade_skips_small_changes():
    b = _bars()
    me = gt.month_ends(b.index)
    st = str(me[2].date())
    few = gt.run(b, gt.fixed_targets(me, {"SPY": 0.5, "IEF": 0.5}), st, None, 2, 50, 100000).trades
    many = gt.run(b, gt.fixed_targets(me, {"SPY": 0.5, "IEF": 0.5}), st, None, 2, 0.0, 100000).trades
    assert few < many


def test_metrics_sharpe_and_drawdown():
    idx = pd.bdate_range("2020-01-01", periods=600)
    e = pd.Series(100 * np.cumprod(np.r_[1, np.full(599, 1.001)]), index=idx)
    e.iloc[300:320] *= 0.8
    m = gt.metrics(e)
    assert m["max_drawdown_pct"] == pytest.approx(-20, abs=0.5)
    assert m["total_return_pct"] > 0
