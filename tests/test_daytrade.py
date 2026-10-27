"""Day trading mode (QuantFlow clone): no lookahead + trade management rules from the spec."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from bot import data, daytrade, quantflow

CFG = daytrade.load_config()
COST = (CFG["costs"]["fee_bps"] + CFG["costs"]["slippage_bps"]) / 10_000


def _bars(n=3000, seed=3):
    df = data.synthetic("BTC/USD", bars=n, seed=seed)
    df.index = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return df


def test_signals_no_lookahead():
    """Every signal at bar t must be identical whether or not later bars exist."""
    df = _bars()
    full = quantflow.signals(df, CFG)
    for k in (400, 1234, 2500):
        part = quantflow.signals(df.iloc[:k], CFG)
        pd.testing.assert_frame_equal(full.iloc[:k], part, check_freq=False)


def test_engine_no_lookahead():
    """Changing the future must not change any trade that already closed."""
    df = _bars()
    k = 2000
    fut = df.copy()
    fut.iloc[k:, :4] = fut.iloc[k:, :4] * 1.7  # wild future
    a = daytrade.run(CFG, {"BTC/USD": df}).trades_df
    b = daytrade.run(CFG, {"BTC/USD": fut}).trades_df
    cut = df.index[k - 1]
    a, b = a[a.exit_ts < cut].reset_index(drop=True), b[b.exit_ts < cut].reset_index(drop=True)
    assert len(a) > 5
    pd.testing.assert_frame_equal(a, b)


# ---- controlled scenarios: flat bars + a hand made signal frame

def _scenario(monkeypatch, rows, buy_at=0, sell_at=None, atr=1.0):
    """rows: list of (open, high, low, close). Buy signal on bar `buy_at` (fills next open)."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    sig = pd.DataFrame({"buy": False, "sell": False, "paused": False, "atr": atr, "source": "test"}, index=idx)
    sig.iloc[buy_at, sig.columns.get_loc("buy")] = True
    if sell_at is not None:
        sig.iloc[sell_at, sig.columns.get_loc("sell")] = True
    monkeypatch.setattr(daytrade.quantflow, "signals", lambda d, c: sig)
    return daytrade.run(CFG, {"BTC/USD": df})


def test_fill_next_open_with_costs_and_target(monkeypatch):
    # atr 1 → 1R = 1.5, entry open 100 → stop 98.5, target 103
    res = _scenario(monkeypatch, [(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 103.2, 99.9, 103)])
    t = res.trades_df.iloc[0]
    assert t.entry_ts == res.equity.index[1]
    assert t.entry_price == pytest.approx(100 * (1 + COST))
    assert t.reason == "target" and t.exit_price == pytest.approx(103 * (1 - COST))


def test_stop_wins_when_both_touched(monkeypatch):
    res = _scenario(monkeypatch, [(100, 100, 100, 100), (100, 100, 100, 100), (100, 104, 98, 101)])
    t = res.trades_df.iloc[0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(98.5 * (1 - COST))


def test_breakeven_takes_effect_next_candle(monkeypatch):
    rows = [(100, 100, 100, 100),
            (100, 101.6, 99.0, 101),   # +1R (101.5) touched, low 99 is above the 98.5 stop
            (101, 101, 99.5, 99.8)]    # next bar: stop is now 100 → hit
    t = _scenario(monkeypatch, rows).trades_df.iloc[0]
    assert t.reason == "breakeven" and t.exit_price == pytest.approx(100 * (1 - COST))


def test_breakeven_not_same_candle(monkeypatch):
    # +1R and a dip below entry in the SAME bar: stop is still 98.5 there, so no exit
    rows = [(100, 100, 100, 100), (100, 101.6, 99.0, 100.5), (100.5, 100.8, 100.2, 100.6)]
    t = _scenario(monkeypatch, rows).trades_df.iloc[0]
    assert t.reason == "end"


def test_sell_signal_exits_next_open(monkeypatch):
    rows = [(100, 100, 100, 100), (100, 100.5, 99.5, 100.2), (100.4, 100.6, 100, 100.3), (100.1, 100.5, 99.9, 100)]
    t = _scenario(monkeypatch, rows, sell_at=2).trades_df.iloc[0]
    assert t.reason == "signal" and t.exit_ts == pd.Timestamp("2024-01-01 00:15", tz="UTC")
    assert t.exit_price == pytest.approx(100.1 * (1 - COST))


def test_position_size_respects_risk_and_cap(monkeypatch):
    t = _scenario(monkeypatch, [(100, 100, 100, 100), (100, 100.5, 99.5, 100)], atr=1.0).trades_df.iloc[0]
    eq0 = CFG["account"]["starting_equity"]
    # 1% risk / 1.5 stop = 666 units = $66.6k notional, capped at 30% → $30k
    assert t.units * 100 == pytest.approx(eq0 * CFG["risk"]["max_position_pct"] / 100)


def test_gap_fill_refused():
    cfg = daytrade.load_config()
    cfg["sources"]["gap_fill"]["enabled"] = True
    with pytest.raises(NotImplementedError):
        quantflow.signals(_bars(300), cfg)


def test_rsi_matches_hand_calc():
    s = pd.Series([1.0, 2.0, 1.0, 3.0])
    r = quantflow.rsi(s, 2)
    # Wilder alpha 1/2: up avg after bar 2 = 0.5, down = 0.5 → 50; bar 3: up 1.25, down 0.25 → 83.33
    assert r.iloc[2] == pytest.approx(50) and r.iloc[3] == pytest.approx(100 - 100 / 6)
