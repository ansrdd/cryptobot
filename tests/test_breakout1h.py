"""1h volatility breakout: no lookahead + the trade rules from docs/BREAKOUT1H_SPEC.md."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pandas as pd
import pytest
from bot import breakout1h, data

CFG = breakout1h.load_config()
COST = (CFG["costs"]["fee_bps"] + CFG["costs"]["slippage_bps"]) / 10_000


def _bars(n=3000, seed=3):
    df = data.synthetic("BTC/USD", bars=n, seed=seed)
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def test_signals_no_lookahead():
    df = _bars()
    full = breakout1h.signals(df, CFG)
    for k in (300, 1500, 2700):
        pd.testing.assert_frame_equal(full.iloc[:k], breakout1h.signals(df.iloc[:k], CFG), check_freq=False)


def test_engine_no_lookahead():
    df = _bars()
    k = 2000
    fut = df.copy()
    fut.iloc[k:, :4] = fut.iloc[k:, :4] * 1.7
    a = breakout1h.run(CFG, {"BTC/USD": df}).trades_df
    b = breakout1h.run(CFG, {"BTC/USD": fut}).trades_df
    cut = df.index[k - 1]
    a, b = a[a.exit_ts < cut].reset_index(drop=True), b[b.exit_ts < cut].reset_index(drop=True)
    assert len(a) > 5
    pd.testing.assert_frame_equal(a, b)


def test_no_trades_before_window_start():
    df = _bars()
    start = df.index[1500]
    t = breakout1h.run(CFG, {"BTC/USD": df}, start=str(start)).trades_df
    assert len(t) and (t.entry_ts > start).all()


# ---- controlled scenarios: hand made bars + a hand made signal frame

def _scenario(monkeypatch, rows, buy_at=(0,), atr=2.0):
    """rows: (open, high, low, close). atr 2 at price 100 → 1R = 4, 3R = 12% (passes the 3% fee filter)."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    sig = pd.DataFrame({"buy": False, "atr": atr}, index=idx)
    for b in buy_at:
        sig.iloc[b, 0] = True
    monkeypatch.setattr(breakout1h, "signals", lambda d, c: sig)
    return breakout1h.run(CFG, {"BTC/USD": df}).trades_df


FLAT = (100, 100.5, 99.5, 100)


def test_fill_next_open_and_target(monkeypatch):
    t = _scenario(monkeypatch, [FLAT, FLAT, (100, 112.5, 99, 112)]).iloc[0]
    assert t.entry_ts == pd.Timestamp("2024-01-01 01:00", tz="UTC")
    assert t.entry_price == pytest.approx(100 * (1 + COST))
    assert t.reason == "target" and t.exit_price == pytest.approx(112 * (1 - COST))


def test_stop_wins_when_both_touched(monkeypatch):
    t = _scenario(monkeypatch, [FLAT, FLAT, (100, 113, 95, 101)]).iloc[0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(96 * (1 - COST))


def test_gap_below_stop_fills_at_open(monkeypatch):
    t = _scenario(monkeypatch, [FLAT, FLAT, (90, 91, 89, 90)]).iloc[0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(90 * (1 - COST))


def test_time_stop_after_48_bars(monkeypatch):
    n = CFG["trade"]["time_stop_bars"]
    t = _scenario(monkeypatch, [FLAT] * (n + 5)).iloc[0]
    assert t.reason == "time"
    # entered at bar 1, held bars 1..48, out at bar 49's open
    assert t.exit_ts == pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=n + 1)


def test_fee_filter_skips_small_moves(monkeypatch):
    # atr 0.4 → 3R = 2.4% of price, under 5 x 0.6% = 3% → no trade
    assert len(_scenario(monkeypatch, [FLAT] * 5, atr=0.4)) == 0
    # atr 0.6 → 3R = 3.6% → trade
    assert len(_scenario(monkeypatch, [FLAT] * 5, atr=0.6)) == 1


def test_no_reentry_on_exit_bar(monkeypatch):
    # stopped out on bar 2, buy signal on bar 2's close too → ignored; bar 3 signal → new trade
    rows = [FLAT, FLAT, (100, 100, 95, 97), (97, 97.5, 96.5, 97), (97, 97.5, 96.5, 97)]
    t = _scenario(monkeypatch, rows, buy_at=(0, 2))
    assert len(t) == 1
    t = _scenario(monkeypatch, rows, buy_at=(0, 2, 3))
    assert len(t) == 2 and t.iloc[1].entry_ts == pd.Timestamp("2024-01-01 04:00", tz="UTC")


def test_breakout_signal_definition():
    idx = pd.date_range("2024-01-01", periods=260, freq="1h", tz="UTC")
    close = pd.Series(100.0, index=idx)
    close.iloc[-1] = 105  # breaks the 24 bar high, above a flat EMA 200
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    s = breakout1h.signals(df, CFG)
    assert s.buy.iloc[-1] and not s.buy.iloc[:-1].any()  # equal to the prior high is not a break
