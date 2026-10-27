"""Smoke tests: run with  python -m pytest -q"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from bot import config, data, backtest, risk


def _cfg():
    return config.load()


def test_backtest_runs_and_accounts():
    cfg = _cfg()
    d = {"BTC/USD": data.synthetic("BTC/USD", bars=24 * 400, seed=1)}
    res = backtest.run(cfg, d)
    m = backtest.metrics(res)
    assert len(res.equity) > 1000
    assert (res.equity > 0).all()
    assert m["n_trades"] > 0
    # equity change == realized pnl + open unrealized (within rounding)
    open_val = res.equity.iloc[-1] - res.equity.iloc[0] - res.trades_df.pnl.sum()
    assert abs(open_val) < res.equity.iloc[0] * 0.3


def test_drawdown_kill_switch():
    cfg = _cfg()
    rm = risk.RiskManager(cfg, 100_000)
    import pandas as pd
    ts = pd.Timestamp("2025-01-01", tz="UTC")
    rm.on_bar(ts, 100_000)
    kill = cfg.risk.max_drawdown_kill_pct
    rm.on_bar(ts + pd.Timedelta(hours=1), 100_000 * (1 - (kill - 0.5) / 100))  # just under the limit
    assert not rm.state.killed
    low = 100_000 * (1 - (kill + 1) / 100)                                   # just over it
    rm.on_bar(ts + pd.Timedelta(hours=2), low)
    assert rm.state.killed
    assert not rm.can_open(0, 0, low)[0]


def test_daily_loss_halts_then_resets():
    cfg = _cfg()
    rm = risk.RiskManager(cfg, 100_000)
    import pandas as pd
    t0 = pd.Timestamp("2025-01-01 00:00", tz="UTC")
    rm.on_bar(t0, 100_000)
    rm.on_bar(t0 + pd.Timedelta(hours=5), 96_500)   # -3.5% day
    assert rm.state.halted_for_day
    rm.on_bar(t0 + pd.Timedelta(days=1), 96_500)    # new day
    assert not rm.state.halted_for_day


def test_sizing_respects_caps():
    cfg = _cfg()
    rm = risk.RiskManager(cfg, 100_000)
    units = rm.size(equity=100_000, price=50_000, stop=49_900)  # tiny stop → huge size wanted
    assert units * 50_000 <= 100_000 * cfg.risk.max_position_pct / 100 + 1e-6
    assert rm.size(100_000, 50_000, 51_000) == 0  # stop above price → no trade


def test_live_mode_refused(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(open(config.ROOT / "config.yaml").read().replace("mode: paper", "mode: live"))
    import pytest
    with pytest.raises(ValueError):
        config.load(p)


def test_resample_daily_drops_partial_day():
    import pandas as pd
    h = data.synthetic("BTC/USD", bars=24 * 10 + 5, seed=3)   # 10 full days + 5 hours of an 11th... maybe
    d = data.resample(h, "1d")
    assert (d.index.hour == 0).all()
    # every daily bar must be fully covered by hourly data
    assert d.index[-1] + pd.Timedelta(days=1) <= h.index[-1] + pd.Timedelta(hours=1)
    day = d.index[-1]
    chunk = h[(h.index >= day) & (h.index < day + pd.Timedelta(days=1))]
    assert d["high"].iloc[-1] == chunk["high"].max() and d["close"].iloc[-1] == chunk["close"].iloc[-1]


def test_metrics_annualize_by_bar_spacing():
    import numpy as np, pandas as pd
    # same daily returns, expressed daily vs. with hourly flat padding, must give the same CAGR
    idx = pd.date_range("2024-01-01", periods=366, freq="1D", tz="UTC")
    eq = pd.Series(100_000 * np.cumprod(np.r_[1, np.full(365, 1.0003)]), index=idx)
    r = backtest.Result(eq, [], eq.copy())
    m = backtest.metrics(r)
    assert abs(m["cagr_pct"] - 100 * (1.0003 ** 365 - 1)) < 0.5


def test_alert_wording():
    from bot import alerts
    assert alerts.buy_msg("BTC/USD") == "We're buying BTC rn"
    assert alerts.sell_msg("ETH/USD", "stop", -50) == "Just sold ETH bc it hit the stop loss"
    assert alerts.sell_msg("BTC/USD", "signal", 1240.5) == "Just sold BTC bc the trend flipped\nWe just made $1,240.50 bro"
    assert alerts.sell_msg("BTC/USD", "weird", 0) == "Just sold BTC bc weird"
    assert alerts.weekly_msg(-0.035, 96500, 0).endswith("Rough week, we're down")
