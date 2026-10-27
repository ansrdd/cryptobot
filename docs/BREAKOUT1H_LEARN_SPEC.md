# 1h breakout + learning trade filter: locked spec (written before any backtest)

**Goal:** keep the 1h breakout signal exactly as locked in `docs/BREAKOUT1H_SPEC.md`, and add a model that learns from past breakouts which ones tend to win. It only takes the trades it rates good enough to beat fees. This is called meta labeling: the base signal says *when* a trade is possible, the model says *whether* to take it.

**Why:** the plain 1h breakout's dev check failed at real costs (-58%, PF 0.86), but at zero cost it was +169%, PF 1.22. The signal has something; fees eat it. With a 3R target and costs around a third of 1R, the trades taken need roughly a 40% net win rate to clear PF 1.3. The plain signal wins about 35%. The model's job is to find those extra points while leaving enough trades.

**What is locked here:** the learning *procedure* (inputs, model, retrain schedule, cutoff), not learned weights. The weights change every month by design, using only the past.

## Base signal and trade rules (unchanged)

Everything from `docs/BREAKOUT1H_SPEC.md` / `config_breakout1h.yaml`: EMA 200 trend filter, 24 bar breakout, 2 ATR stop, 3R target, 48 bar time stop, fee filter, next open fills, real costs (0.25% + 0.05% per side), stop wins ties, 1% risk, 30% cap per coin, 3% daily halt, 20% drawdown recorded only.

## What the model learns from

- **Candidates:** every breakout the plain strategy would take for that coin (one at a time per coin, after the fee filter), ignoring portfolio limits. This is the same sequence of trades the plain strategy makes.
- **Label:** 1 if the candidate trade made money **after real costs**, else 0. It is simulated with the exact trade rules above.
- **No peeking:** a candidate's label only exists after its trade has exited. At each retrain, the model may only use candidates whose exit happened **before** the retrain time.

## Inputs (all known at the signal bar's close)

1. ATR(14) as % of price (how volatile right now)
2. ATR(14) divided by its 30 day average (is volatility expanding)
3. Distance of close above EMA 200, in ATRs (trend strength)
4. EMA 200 change over the last 24 bars, as % of price (trend slope)
5. How far the close broke above the prior 24 bar high, in ATRs (breakout strength)
6. Log of volume divided by its 7 day average (volume surge)
7. Hour of day UTC, as sine and cosine (2 inputs)
8. Weekend flag (Saturday or Sunday UTC)
9. Return over the last 24 bars
10. Return over the last 7 days
11. BTC's close vs BTC's EMA 200 (market trend, same value for both coins)

## Model

- Logistic regression with L2 regularization (penalty strength 1.0 on standardized inputs). Simple on purpose: about 200 candidates a year is not enough data for anything fancy.
- Inputs are standardized using the mean and spread of the **training set only**.
- **Retrain:** at the first bar of every calendar month (UTC), on all eligible candidates since the data starts (expanding window).
- **Warm up:** no trades until the model has at least 200 labeled candidates.

## Cutoff (set from costs, not tuned)

Take a candidate only if the model's predicted win probability is at least the probability needed for a profit factor of 1.3 on that trade:

`p_needed = 1.3 × (1 + k) / ((3 − k) + 1.3 × (1 + k))`

where `k` = round trip cost ÷ 1R, both as % of price, for that specific trade (so `k` = 0.60% ÷ (2 × ATR ÷ price)). Bigger trades relative to fees need a lower probability; small ones need a higher one. This ignores time exits, which is a known simplification.

## Test periods (same as the plain spec)

- **Development:** 2020-01-01 to 2024-12-31. Used to check the code works. Any change after seeing dev results goes in the change log below and is committed **before** the holdout runs.
- **Holdout (the verdict):** 2025-01-01 to the run date. Run **once**. The model keeps retraining monthly through the holdout using only the past, exactly like it would live.
- The plain breakout's own holdout is **not** run on its own (see its change log). It only appears as a diagnostic next to this test's holdout, after the verdict.

## Passing bar (holdout only, all must hold, at real costs)

- Sharpe ≥ 0.5 (from daily returns, 365 days a year)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 100 trades
- Profitable in at least 60% of calendar quarters (the partial current quarter counts)

## Diagnostics (shown, never the verdict)

- **Did it learn anything:** AUC of the model's out of sample predictions (0.5 = coin flip). Win rate of trades taken vs skipped.
- The plain breakout over the same period, for comparison
- Cost sensitivity (maker 0.15% no slippage, zero cost), per coin, per exit reason
- How the model's weights moved over time

Changing inputs, the model or the cutoff after seeing these is curve fitting and needs a new spec.

## If it passes

Paper trading only, on a separate Alpaca paper account, with the same Telegram bot tagged "[1h]". It needs the always on server first, because GitHub's scheduler skips most hourly runs.

## Change log

- (none yet)
