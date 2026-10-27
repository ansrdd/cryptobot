# Day trading mode: locked spec (written before any backtest)

**Goal:** an open, free clone of the QuantFlow TradingView indicator (thequantflow.com), run on 5 minute BTC/ETH bars with real Alpaca costs, to find out if the idea has an edge before anyone pays for the real thing or puts in money.

**Rule:** these settings are committed before the backtest runs. The commit timestamp is the proof. If it fails, it fails. No tuning to make it pass.

## What QuantFlow publicly says it does

From its website (the code itself is invite only and hidden):

- A trend ribbon (green up, red down) that colors candles
- Five switchable signal sources: ribbon flips, gap fills, MACD, RSI(2), band re-entry
- Signals take turns: every BUY gets its SELL
- Each trade has a target, a stop, and a stop that moves to breakeven
- Signals print only on candle close (no repainting)
- Filters: pause around CPI / jobs / Fed releases, pause on VIX spikes, block buys when the 10 year yield jumps, pause after sudden outsized candles
- Best on the 5 minute chart; built and tested on Nasdaq futures

## How each piece is cloned

The exact settings are not public, so each piece uses its standard textbook definition.

| QuantFlow piece | Clone | Why this version |
|---|---|---|
| Trend ribbon | EMA 9 vs EMA 21 | Most common fast/slow pair for 5m trend ribbons |
| Ribbon flip | BUY when EMA 9 crosses above EMA 21, SELL when it crosses below | Direct definition |
| MACD | MACD(12, 26, 9) line crosses signal line; BUY only if ribbon is up | Standard settings; ribbon used as the trend context |
| RSI(2) | BUY when RSI(2) < 10 and close > SMA 200; SELL when RSI(2) > 90 | Larry Connors' original RSI(2) rules |
| Band re-entry | Bollinger(20, 2): BUY when price closes back above the lower band after closing below it, and close > SMA 200; SELL on the mirror at the upper band | Standard re-entry definition, with Connors' trend filter |
| Gap fills | **Off** | Crypto trades 24/7, there are no session gaps to fill |
| Target / stop / breakeven | Stop 1.5 × ATR(14) below entry, target 2R, stop to entry once +1R is reached | Common default trade management |
| Every BUY gets its SELL | One position per coin; an opposite signal closes it at the next open | Direct definition |
| Outsized candle filter | Bar range > 3 × ATR(14) pauses new entries for 90 min | Their news pause window is 30 min before to 90 min after |

## What is NOT cloned, and why

- **Short selling.** QuantFlow's SELL can open a short. Alpaca does not offer margin for crypto, so the bot can only be long. SELL = exit.
- **News calendar (CPI, jobs, Fed).** Needs an exact historical release calendar. Filters can only remove trades, they can't create an edge. Added later only if the core signals show one.
- **VIX and 10 year yield filters.** Stock market risk gauges with daily data. Same reasoning.

## Execution model (pessimistic on purpose)

- Signals are computed on a closed 5m candle and filled at the **next** candle's open
- Costs: 0.25% fee + 0.05% slippage **per side** (0.60% round trip)
- If the stop and target are both touched in the same candle, assume the **stop** hit first
- Breakeven move takes effect on the candle after the +1R level is touched
- Risk: 1% of equity per trade, max 30% of equity per coin, 3% daily loss halt
- Kill switch at 20% drawdown is recorded but the test keeps running so the full picture is visible

## Passing bar (all must hold, at real costs)

- Sharpe ≥ 0.5 (from daily returns)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 100 trades
- Profitable in at least 60% of calendar quarters

## Diagnostics (shown, but never the verdict)

- Same test at 0.15% fee with no slippage (maker fees) and at zero cost, to show how much fees matter
- Results per signal source and per coin. **Picking the best source after seeing these is curve fitting**, so any follow up based on them needs its own new locked spec and a fresh test period.

## Data

Coinbase public 5 minute candles, BTC/USD and ETH/USD, 2023-01-01 to the run date.
