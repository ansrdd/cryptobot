"""1h breakout + learning filter: no lookahead in features AND labels, and the model pieces."""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from bot import breakout1h, breakout1h_learn as L, data

BASE, LEARN = L.load_config()
LEARN_FAST = L.with_min_train(LEARN, 50)


def _bars(n=24 * 500):
    out = {}
    for i, s in enumerate(["BTC/USD", "ETH/USD"]):
        df = data.synthetic(s, bars=n, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000)
        df.index = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
        out[s] = df
    return out


def test_features_no_lookahead():
    bars = _bars()
    full = L.features(bars, LEARN)
    for k in (900, 5000):
        part = L.features({s: b.iloc[:k] for s, b in bars.items()}, LEARN)
        for s in bars:
            pd.testing.assert_frame_equal(full[s].iloc[:k], part[s], check_freq=False)


def test_candidates_match_plain_engine():
    """Labels are simulated with the exact trade rules: same trades as the plain engine
    when portfolio limits can't bind."""
    bars = {"BTC/USD": _bars()["BTC/USD"]}
    cfg = copy.deepcopy(BASE)
    cfg["risk"]["daily_loss_limit_pct"] = 1000
    plain = breakout1h.run(cfg, bars).trades_df
    plain = plain[plain.reason != "end"].reset_index(drop=True)
    c = L.candidates(bars, BASE)
    assert len(c) > 50
    assert list(c.entry_ts) == list(plain.entry_ts)
    assert list(c.exit_ts) == list(plain.exit_ts)
    assert list(c.reason) == list(plain.reason)
    np.testing.assert_allclose(c.net_ret.to_numpy() * 100, plain.pnl_pct.to_numpy(), rtol=1e-9)


def test_training_only_uses_exited_trades(monkeypatch):
    """Every model may only see candidates whose exit bar closed before its retrain time."""
    bars = _bars()
    seen = []
    real_fit = L.fit_logistic
    cands = L.candidates(bars, BASE)

    def spy(X, y, l2=1.0, iters=50):
        seen.append(len(y))
        return real_fit(X, y, l2, iters)
    monkeypatch.setattr(L, "fit_logistic", spy)
    _, hist = L.walk_forward(bars, BASE, LEARN_FAST)
    fitted = [h for h in hist if h["weights"] is not None]
    assert len(fitted) > 5
    for h in fitted:
        allowed = (cands.exit_ts + pd.Timedelta(hours=1) <= h["retrain"]).sum()
        assert h["n_train"] <= allowed


def test_walk_forward_no_lookahead():
    """Wild future prices must not change any approval or trade before the cut."""
    bars = _bars()
    k = 24 * 380
    fut = {s: b.copy() for s, b in bars.items()}
    for b in fut.values():
        b.iloc[k:, :4] *= 1.7
    cut = bars["BTC/USD"].index[k - 1]
    ra, sa, _ = L.run(bars, BASE, LEARN_FAST)
    rb, sb, _ = L.run(fut, BASE, LEARN_FAST)
    a = sa[sa.signal_ts < cut - pd.Timedelta(days=3)].reset_index(drop=True)
    b = sb[sb.signal_ts < cut - pd.Timedelta(days=3)].reset_index(drop=True)
    assert a.approve.sum() > 5
    pd.testing.assert_series_equal(a.approve, b.approve)
    ta, tb = ra.trades_df, rb.trades_df
    pd.testing.assert_frame_equal(ta[ta.exit_ts < cut].reset_index(drop=True), tb[tb.exit_ts < cut].reset_index(drop=True))


def test_engine_only_trades_approved_signals():
    bars = _bars()
    res, scored, _ = L.run(bars, BASE, LEARN_FAST)
    approved = set(zip(scored[scored.approve].symbol, scored[scored.approve].entry_ts))
    t = res.trades_df
    assert len(t) > 5
    assert set(zip(t.symbol, t.entry_ts)) <= approved


def test_no_trades_before_warmup():
    bars = _bars()
    scored, _ = L.walk_forward(bars, BASE, L.with_min_train(LEARN, 10_000))
    assert not scored.approve.any() and scored.prob.isna().all()


def test_logistic_matches_known_answer():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.5 + 2 * X[:, 0] - 1 * X[:, 1])))).astype(float)
    w = L.fit_logistic(X, y, l2=1.0)
    assert w == pytest.approx([0.5, 2.0, -1.0], abs=0.15)
    # gradient of the penalized loss is ~0 at the answer
    Xb = np.column_stack([np.ones(len(X)), X])
    p = 1 / (1 + np.exp(-Xb @ w))
    g = Xb.T @ (p - y) + np.r_[0, w[1:]]
    assert np.abs(g).max() < 1e-6


def test_p_needed_formula():
    # no costs, 3R target, PF 1.3: p*3 = 1.3*(1-p) → p = 1.3/4.3
    assert L.p_needed(0.0, 3.0, 1.3) == pytest.approx(1.3 / 4.3)
    # costs push it up: k = 1/3 → about 39%
    assert L.p_needed(1 / 3, 3.0, 1.3) == pytest.approx(0.3935, abs=1e-3)


def test_auc():
    assert L.auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert L.auc(np.array([0, 1, 0, 1]), np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
