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
    rm.on_bar(ts + pd.Timedelta(hours=1), 84_000)  # -16% > 15% kill
    assert rm.state.killed
    assert not rm.can_open(0, 0, 84_000)[0]


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
