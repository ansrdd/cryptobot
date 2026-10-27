# 1h volatility breakout: locked spec (written before any backtest)

**Goal:** a "slow day trading" bot. Hold for hours to two days, not minutes, so each winning trade is several times bigger than the 0.60% round trip cost. This is the lesson from the failed 5m QuantFlow clone (`docs/daytrade_backtest.md`): its whole 2R target was smaller than the fee.

**Rule:** these settings are committed before any backtest. The commit timestamp is the proof. The holdout runs once. If it fails, it fails.

## Strategy

| Piece | Rule | Why |
|---|---|---|
| Bars | 1 hour, BTC/USD and ETH/USD | A typical 1h move is several times a 5m move, so fees are a smaller slice |
| Trend filter | Close above EMA 200 (about 8 days of 1h bars) | Long only, so only buy when the bigger trend is up |
| Entry | Close breaks above the highest high of the previous 24 bars (one day), not counting the current bar | Classic Donchian style breakout; crypto tends to keep going after a clean break |
| Stop | 2 × ATR(14) below entry | Room for normal 1h noise |
| Target | 3R (3 × the stop distance) | Winners have to pay for losers plus fees |
| Fee filter | Skip the trade if the 3R target is less than 5 × the round trip cost (5 × 0.60% = 3.0% of price) | Set from costs, not from data. If the move can't be big enough to beat fees, don't take it |
| Time stop | Exit at the next open after 48 bars (2 days) in the trade | Keeps it a short term trade, frees the money for the next setup |
| No breakeven move, no trailing stop | | Fewer knobs, less room to curve fit |

## Execution model (same pessimistic rules as the 5m test)

- Signals use a closed 1h bar and fill at the **next** bar's open
- Costs: 0.25% fee + 0.05% slippage **per side** (0.60% round trip)
- If the stop and target are both touched in the same bar, assume the **stop** hit first
- If a bar opens past the stop or target, fill at that open
- One position per coin. After an exit, a new entry needs a fresh signal on a later bar
- Risk: 1% of equity per trade, max 30% of equity per coin, 3% daily loss halts new entries for the rest of the UTC day
- Kill switch at 20% drawdown is recorded, but the test keeps running so the full picture is visible

## Test periods

- **Development:** 2020-01-01 to 2024-12-31. Used only to check the code works. Any setting change after seeing development results must be written into the change log below and committed **before** the holdout runs.
- **Holdout (the verdict):** 2025-01-01 to the run date. Run **once**. Indicators warm up on data before 2025, but trades and scores count only inside the holdout.

## Passing bar (holdout only, all must hold, at real costs)

- Sharpe ≥ 0.5 (from daily returns, 365 days a year)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 100 trades
- Profitable in at least 60% of calendar quarters (the partial current quarter counts)

## Diagnostics (shown, never the verdict)

- Development period results
- Same test at 0.15% fee with no slippage (maker) and at zero cost
- Results per coin and per exit reason (target, stop, time)
- Buy and hold over the same holdout, for context

Picking a better coin, exit or setting after seeing these is curve fitting. Any follow up needs its own new locked spec.

## Data

Coinbase public 1 hour candles, BTC/USD and ETH/USD (same source as the live bot). The backtest reads them without changing the tracked `data/*_1h.parquet` files the live bot commits.

## Change log

- 2026-10-27: no setting changes. After the dev check failed, the standalone holdout is **retired, not run**: the next attempt (`docs/BREAKOUT1H_LEARN_SPEC.md`) reuses this signal with a learning trade filter and needs 2025+ to stay unseen. This strategy's 2025+ results will only appear as a diagnostic next to that test's holdout.
