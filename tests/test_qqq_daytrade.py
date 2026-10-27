"""QuantFlow clone on QQQ 5m: no lookahead, shorts, stop and reverse, session rules."""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from bot import qqq_daytrade as q, stocks_data

CFG = q.load_config()
COST = (CFG["costs"]["fee_bps"] + CFG["costs"]["slippage_bps"]) / 10_000
NY = "America/New_York"


def test_signals_no_lookahead():
    b = q.synthetic(60)
    full = q.signals(b, CFG)
    for k in (500, 2345, 4000):
        part = q.signals(b.iloc[:k], CFG)
        cols = [c for c in full.columns]
        pd.testing.assert_frame_equal(full.iloc[:k][cols], part[cols], check_freq=False)


def test_engine_no_lookahead():
    b = q.synthetic(200)
    k = 78 * 120 + 30                     # mid session
    fut = b.copy()
    fut.iloc[k:, :4] *= 1.7
    a, c = q.run(CFG, b).trades_df, q.run(CFG, fut).trades_df
    cut = b.index[k - 1]
    a, c = a[a.exit_ts < cut].reset_index(drop=True), c[c.exit_ts < cut].reset_index(drop=True)
    assert len(a) > 50
    pd.testing.assert_frame_equal(a, c)


def test_never_overnight_and_no_late_entries():
    t = q.run(CFG, q.synthetic(150)).trades_df
    e, x = t.entry_ts.dt.tz_convert(NY), t.exit_ts.dt.tz_convert(NY)
    assert (e.dt.date == x.dt.date).all()
    assert ((e.dt.hour * 60 + e.dt.minute) < 15 * 60 + 30).all()
    assert set(t.side) == {"long", "short"}


# ---- controlled scenarios: one session of hand made bars + a hand made signal frame

def _session(rows, start="2024-03-05 09:30"):
    idx = pd.date_range(pd.Timestamp(start, tz=NY), periods=len(rows), freq="5min").tz_convert("UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    return df


def _run(monkeypatch, df, sig_at, atr=1.0, cfg=CFG, **kw):
    """sig_at: {bar_index: +1 | -1}. atr 1 at price 100: 1R = 1.5."""
    sig = pd.DataFrame({"signal": 0, "source": "test", "atr": atr, "paused": False}, index=df.index)
    for i, v in sig_at.items():
        sig.iloc[i, 0] = v
    monkeypatch.setattr(q, "signals", lambda d, c: sig)
    return q.run(cfg, df, **kw).trades_df


F = (100, 100.2, 99.8, 100)


def test_short_fill_target_and_costs(monkeypatch):
    # short at 100 → stop 101.5, target 97
    t = _run(monkeypatch, _session([F, F, (100, 100.1, 96.8, 97), F]), {0: -1}).iloc[0]
    assert t.side == "short" and t.entry_price == pytest.approx(100 * (1 - COST))
    assert t.reason == "target" and t.exit_price == pytest.approx(97 * (1 + COST))
    assert t.pnl > 0


def test_short_stop_wins_when_both_touched(monkeypatch):
    t = _run(monkeypatch, _session([F, F, (100, 102, 96, 100), F]), {0: -1}).iloc[0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(101.5 * (1 + COST))


def test_short_breakeven_next_bar(monkeypatch):
    rows = [F, (100, 100.5, 98.4, 99), (99, 100.2, 98.9, 100), F]   # +1R (98.5) touched, then back to entry
    t = _run(monkeypatch, _session(rows), {0: -1}).iloc[0]
    assert t.reason == "breakeven" and t.exit_price == pytest.approx(100 * (1 + COST))


def test_stop_and_reverse(monkeypatch):
    rows = [F, F, F, (100.4, 100.6, 100.2, 100.5), F, F]
    t = _run(monkeypatch, _session(rows), {0: 1, 2: -1})
    assert list(t.side) == ["long", "short"]
    assert t.iloc[0].reason == "reverse" and t.iloc[0].exit_ts == t.iloc[1].entry_ts
    assert t.iloc[1].entry_price == pytest.approx(100.4 * (1 - COST))


def test_flat_at_session_close(monkeypatch):
    rows = [F] * 78
    rows[-1] = (100, 100.3, 99.9, 100.2)
    t = _run(monkeypatch, _session(rows), {10: 1}).iloc[0]
    assert t.reason == "close" and t.exit_price == pytest.approx(100.2 * (1 - COST))
    assert t.exit_ts.tz_convert(NY).strftime("%H:%M") == "15:55"


def test_no_entry_filled_at_or_after_1530(monkeypatch):
    rows = [F] * 78
    i_1520, i_1525 = 70, 71                      # 09:30 + 5 min * i
    assert len(_run(monkeypatch, _session(rows), {i_1520: 1})) == 1    # fills 15:25
    assert len(_run(monkeypatch, _session(rows), {i_1525: 1})) == 0    # would fill 15:30


def test_exit_signal_after_cutoff_still_exits(monkeypatch):
    rows = [F] * 78
    t = _run(monkeypatch, _session(rows), {10: 1, 72: -1})
    assert len(t) == 1 and t.iloc[0].reason == "signal"


def test_long_only_ignores_sells(monkeypatch):
    t = _run(monkeypatch, _session([F] * 10), {0: -1, 3: 1, 5: -1}, long_short=False)
    assert list(t.side) == ["long"] and t.iloc[0].reason == "signal"


def test_size_capped_at_equity(monkeypatch):
    # atr 1 → 1R 1.5: 1% risk = 666.7 shares = $66.7k, under the cap
    t = _run(monkeypatch, _session([F] * 5), {0: 1}).iloc[0]
    assert t.units == pytest.approx(1000 / 1.5)
    # atr 0.2 → 1R 0.3: 1% risk wants 3,333 shares = $333k → capped at 100% of equity
    t = _run(monkeypatch, _session([F] * 5), {0: 1}, atr=0.2).iloc[0]
    assert t.units * 100 == pytest.approx(CFG["account"]["starting_equity"])


def test_gap_signal_definition():
    day1 = _session([F] * 78, "2024-03-04 09:30")
    day2 = _session([(99.7, 99.8, 99.6, 99.7)] + [F] * 77, "2024-03-05 09:30")     # opens 0.3% below 100
    day3 = _session([(100.2, 100.3, 100.1, 100.2)] + [F] * 77, "2024-03-06 09:30")  # opens 0.2% above: too small
    s = q.signals(pd.concat([day1, day2, day3]), CFG)
    assert s.buy_gap_fill.iloc[78] and s.buy_gap_fill.sum() == 1 and not s.sell_gap_fill.any()


def test_regular_hours_filter_uses_calendar():
    cal = pd.DataFrame({"date": [pd.Timestamp("2024-11-29")],
                        "open": [pd.Timestamp("2024-11-29 09:30", tz=NY)], "close": [pd.Timestamp("2024-11-29 13:00", tz=NY)]})
    idx = pd.date_range(pd.Timestamp("2024-11-29 08:00", tz=NY), pd.Timestamp("2024-11-29 17:00", tz=NY), freq="5min").tz_convert("UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    out = stocks_data.regular_hours(df, cal).index.tz_convert(NY)
    assert out[0].strftime("%H:%M") == "09:30" and out[-1].strftime("%H:%M") == "12:55" and len(out) == 42
