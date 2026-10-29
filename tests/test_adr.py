"""ADR spec engine (bot/adr.py): one hand built long setup, its mirror, and the checks that block it."""
import numpy as np
import pandas as pd
import pytest

from bot import adr

NY = "America/New_York"
CFG = adr.load_config()

# day 2 regular hours bars for the traded symbol (o, h, l, c), starting 9:30
LONG_DAY = [
    (102.8, 103.0, 102.5, 102.9),   # b0
    (102.9, 103.5, 102.7, 103.2),   # b1
    (103.2, 105.0, 103.0, 104.5),   # b2 swing high 105 (confirmed at b4)
    (104.0, 104.0, 103.2, 103.6),   # b3
    (103.6, 103.5, 102.9, 103.0),   # b4
    (103.5, 103.6, 101.5, 101.6),   # b5
    (101.6, 101.7, 100.6, 100.7),   # b6
    (100.7, 100.8, 99.9, 100.3),    # b7 sweep: low under PDL 100, close back above
    (100.3, 100.6, 100.2, 100.5),   # b8
    (101.0, 105.5, 100.9, 105.4),   # b9 shift: displacement close above 105, FVG 100.8 to 100.9
    (105.0, 105.0, 100.85, 101.0),  # b10 trades below 100.9: filled
    (101.0, 104.5, 100.95, 104.4),  # b11 trades through 104 (premarket high): target
    (104.4, 104.6, 104.2, 104.5),
    (104.5, 104.7, 104.3, 104.6),   # last bar of the day
]


def _bars(day2, prev=(100.0, 110.0), pre=(102.0, 104.0)):
    """Prev session 2 bars (low, high), premarket 2 bars (low, high) on day 2, then day 2 bars."""
    rows = []
    d1, d2 = "2024-03-04", "2024-03-05"
    lo, hi = prev
    rows += [(pd.Timestamp(f"{d1} 09:30", tz=NY), (lo + hi) / 2, hi, (lo + hi) / 2, (lo + hi) / 2, True),
             (pd.Timestamp(f"{d1} 09:35", tz=NY), (lo + hi) / 2, (lo + hi) / 2, lo, (lo + hi) / 2, True)]
    plo, phi = pre
    rows += [(pd.Timestamp(f"{d2} 08:00", tz=NY), plo, phi, plo, phi, False),
             (pd.Timestamp(f"{d2} 08:05", tz=NY), phi, phi, plo, plo, False)]
    t0 = pd.Timestamp(f"{d2} 09:30", tz=NY)
    for k, (o, h, l, c) in enumerate(day2):
        rows.append((t0 + pd.Timedelta(minutes=5 * k), o, h, l, c, True))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "rth"]).set_index("ts")
    df.index = df.index.tz_convert("UTC")
    df["volume"] = 1000.0
    return df


def _flat(n, px=200.0):
    return [(px, px + 0.1, px - 0.1, px)] * n


def _run(qqq_day, spy_day=None, bias=1, cfg=CFG, spy_prev=(190.0, 210.0), spy_pre=(199.0, 201.0), **kw):
    bars = {"QQQ": _bars(qqq_day, **kw), "SPY": _bars(spy_day or _flat(len(qqq_day)), prev=spy_prev, pre=spy_pre)}
    prep = {s: adr.prepare(bars[s], cfg) for s in bars}
    for s in prep:
        prep[s]["day"]["bias"] = bias
    return adr.run(cfg, bars, prepared=prep)


def _mirror(day):
    return [(400 - o, 400 - l, 400 - h, 400 - c) for o, h, l, c in day]


def test_long_setup_end_to_end():
    res = _run(LONG_DAY)
    t = res.trades_df
    assert len(t) == 1
    tr = t.iloc[0]
    assert (tr.symbol, tr.side, tr.level, tr.reason) == ("QQQ", "long", "prev_day", "target")
    assert tr.entry_price == pytest.approx(100.9 * 1.0002)
    assert tr.exit_price == pytest.approx(104.0 * 0.9998)
    assert tr.units == np.floor(min(1000 / (100.9 - 99.89), 50000 / 100.9))
    assert tr.r > 2.5


def test_short_is_the_mirror():
    res = _run(_mirror(LONG_DAY), bias=-1, prev=(290.0, 300.0), pre=(296.0, 298.0))
    t = res.trades_df
    assert len(t) == 1
    tr = t.iloc[0]
    assert (tr.side, tr.reason) == ("short", "target")
    assert tr.entry_price == pytest.approx((400 - 100.9) * 0.9998)
    assert tr.exit_price == pytest.approx((400 - 104.0) * 1.0002)
    assert tr.pnl > 0


def test_wrong_bias_or_no_bias_no_trade():
    assert len(_run(LONG_DAY, bias=-1).trades) == 0
    assert len(_run(LONG_DAY, bias=0).trades) == 0


def test_smt_blocks_when_both_sweep():
    spy = _flat(len(LONG_DAY))
    spy[3] = (200.0, 200.1, 189.0, 200.0)       # SPY breaks its own PDL (190) before QQQ's sweep
    assert len(_run(LONG_DAY, spy_day=spy).trades) == 0
    assert len(_run(LONG_DAY, spy_day=spy).trades) == 0
    # without the SMT check the same day trades
    bars = {"QQQ": _bars(LONG_DAY), "SPY": _bars(spy, prev=(190.0, 210.0), pre=(199.0, 201.0))}
    prep = {s: adr.prepare(bars[s], CFG) for s in bars}
    for s in prep:
        prep[s]["day"]["bias"] = 1
    assert len(adr.run(CFG, bars, prepared=prep, require_smt=False).trades) == 1


def test_not_enough_room_no_trade():
    # premarket high 102 is the nearest target: under 2R from 100.9
    assert len(_run(LONG_DAY, pre=(102.0, 102.0)).trades) == 0


def test_touch_is_not_a_fill():
    day = list(LONG_DAY)
    day[10] = (105.0, 105.0, 100.9, 101.0)      # low equals the limit: no fill
    day[11] = (101.0, 104.5, 100.95, 104.4)
    assert len(_run(day).trades) == 0


def test_stop_wins_when_stop_and_target_share_a_bar():
    day = list(LONG_DAY)
    day[11] = (101.0, 104.5, 99.5, 104.4)
    t = _run(day).trades_df
    assert len(t) == 1 and t.iloc[0].reason == "stop"
    assert t.iloc[0].exit_price == pytest.approx(99.89 * 0.9998)


def test_flat_at_close_when_nothing_hits():
    day = list(LONG_DAY)
    day[11] = (101.0, 101.5, 100.95, 101.2)
    day[12] = (101.2, 101.4, 101.0, 101.3)
    day[13] = (101.3, 101.5, 101.1, 101.4)
    t = _run(day).trades_df
    assert len(t) == 1 and t.iloc[0].reason == "close"
    assert t.iloc[0].exit_price == pytest.approx(101.4 * 0.9998)


def test_no_fvg_no_trade():
    day = list(LONG_DAY)
    day[9] = (101.0, 105.5, 100.7, 105.4)       # shift bar low under b7 high: no gap
    assert len(_run(day).trades) == 0


def test_close_back_below_the_level_kills_the_setup():
    day = list(LONG_DAY)
    day[8] = (100.3, 100.4, 99.5, 99.8)         # closes under PDL 100 after the sweep
    assert len(_run(day).trades) == 0


def test_last_confirmed_swing_waits_two_bars():
    h = np.array([1, 2, 5, 3, 2, 1, 1], float)
    sh, _ = adr.pivots(h, h, 2)
    assert sh.tolist() == [False, False, True, False, False, False, False]
    lc = adr.last_confirmed(h, sh, 2)
    assert np.isnan(lc[:4]).all() and lc[4] == 5


def test_future_bars_do_not_change_the_past():
    full = _run(LONG_DAY).trades_df
    cut = _run(LONG_DAY[:12]).trades_df       # stop the day right after the target bar
    assert len(full) == len(cut) == 1
    assert full.iloc[0].entry_price == cut.iloc[0].entry_price
    assert full.iloc[0].exit_ts == cut.iloc[0].exit_ts


def test_bias_only_uses_closed_sessions():
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2024-01-02", periods=12)
    rows = []
    for d in days:
        t0 = pd.Timestamp(f"{d.date()} 09:30", tz=NY)
        for k in range(78):
            p = 100 + rng.normal() * 2
            rows.append((t0 + pd.Timedelta(minutes=5 * k), p, p + 1, p - 1, p))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    df.index = df.index.tz_convert("UTC")
    df["rth"] = True
    b1 = adr.daily_bias(df, 2)
    last = days[-1].date()
    df2 = df.copy()
    on_last = df2.index.tz_convert(NY).date == last
    df2.loc[on_last, ["high", "low"]] *= 3     # wreck the last session
    b2 = adr.daily_bias(df2, 2)
    assert b1[last] == b2[last]


# ---------------------------------------------------------------- ADR Loose (config_adr_loose.yaml)
LOOSE = adr.load_config(adr.ROOT / "config_adr_loose.yaml")


def test_loose_config_differs_only_where_the_spec_says():
    import copy
    a, b = copy.deepcopy(CFG), copy.deepcopy(LOOSE)
    for k in ("levels", "require_smt", "min_target_r", "target_pick", "fallback_target_r", "targets"):
        a["setup"].pop(k, None), b["setup"].pop(k, None)
    assert a == b
    assert LOOSE["setup"]["require_smt"] is False and LOOSE["setup"]["min_target_r"] == 1.0


def test_loose_trades_without_smt_and_logs_it():
    spy = _flat(len(LONG_DAY))
    spy[3] = (200.0, 200.1, 189.0, 200.0)       # SPY also sweeps: no divergence
    t = _run(LONG_DAY, spy_day=spy, cfg=LOOSE).trades_df
    assert t.smt.tolist() == [False]
    t2 = _run(LONG_DAY, cfg=LOOSE).trades_df
    assert t2.smt.tolist() == [True]


def test_loose_picks_nearest_pool_clearing_1r():
    # premarket high 101.5 is only 0.6 above the 100.9 entry (R = 1.01): skipped, PDH 110 clears 1R
    t = _run(LONG_DAY, cfg=LOOSE, pre=(101.5, 101.5)).trades_df
    assert len(t) == 1
    assert t.iloc[0].target_kind == "prev_day"
    # ADR rejects the same day (nearest pool under 2R)
    assert len(_run(LONG_DAY, pre=(101.5, 101.5)).trades) == 0


def test_loose_falls_back_to_fixed_2r():
    # no pool above entry at all: PDH and premarket high both below the 100.9 entry
    t = _run(LONG_DAY, cfg=LOOSE, prev=(100.0, 100.5), pre=(102.0, 100.6)).trades_df
    assert len(t) == 1 and t.iloc[0].target_kind == "fixed"
    assert t.iloc[0].reason == "target"
    assert t.iloc[0].exit_price == pytest.approx((100.9 + 2 * (100.9 - 99.89)) * 0.9998)


def test_untouched_h1_levels():
    # 1H bars built from 12 x 5m bars each; one clear 1H swing low at hour 2 (low 95)
    lows = [100, 98, 95, 97, 99, 99, 99]
    rows = []
    t0 = pd.Timestamp("2024-03-05 09:30", tz=NY)
    k = 0
    for d in range(2):
        day0 = t0 + pd.Timedelta(days=d)
        for m in range(78):
            hr = k // 12
            lo = lows[hr] if hr < len(lows) else 99
            rows.append((day0 + pd.Timedelta(minutes=5 * m), lo + 1, lo + 2, lo, lo + 1))
            k += 1
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    df.index = df.index.tz_convert("UTC")
    u = adr.untouched_h1(df, 2)
    # confirmed when 1H bar 4 (hour index 4, the 5th hour) closes: 5m bar 60 onward
    assert np.isnan(u["h1_lo_before"].iloc[59]) or u["h1_lo_before"].iloc[59] != 95
    assert u["h1_lo_before"].iloc[60] == 95
    # a later bar trading below 95 removes it
    df2 = df.copy()
    df2.iloc[70, df2.columns.get_loc("low")] = 94
    u2 = adr.untouched_h1(df2, 2)
    assert u2["h1_lo_before"].iloc[70] == 95 and np.isnan(u2["h1_lo_after"].iloc[70])
