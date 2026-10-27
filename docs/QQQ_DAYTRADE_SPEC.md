# QuantFlow clone on QQQ 5m stocks: locked spec (written before any data is downloaded)

**Goal:** the fairest test of the QuantFlow idea itself. QuantFlow was built and tested on Nasdaq futures (NQ). QQQ tracks the same Nasdaq 100 index and trades commission free on Alpaca, so costs are a tiny slice of a 5 minute move. The crypto test (`docs/daytrade_backtest.md`) died from fees (0.60% round trip vs ~0.15% typical 5m move). This test removes that excuse: if it fails here, the signals themselves are the problem.

**Rule:** these settings are committed before any QQQ data is downloaded or any backtest runs. The holdout runs once. If it fails, it fails.

## What's different from the crypto clone (and why)

| Change | Why |
|---|---|
| QQQ instead of BTC/ETH | Same index QuantFlow was built on |
| Regular hours only, 9:30 to 16:00 New York time | When QQQ is liquid; QuantFlow's NQ chart is session based |
| **Gap fill source ON** | Stocks close overnight, so gaps exist |
| **Long AND short** | QuantFlow's SELL can open a short. Shorting QQQ is allowed on a margin account. Intraday only, so no overnight borrow fees |
| Flat at the close every day | Day trading: no overnight risk, no borrow fees, no gaps against an open trade |
| Costs 0.02% per side | No commission; covers spread (~1 cent on QQQ), slippage and SEC/FINRA sell fees |
| Max position 100% of equity | No leverage. A 1% risk on a ~0.2% stop would want 5x; capped instead |

## Signals (each source has a BUY and a mirrored SELL)

Indicators run continuously across days on regular hours 5m bars (like a TradingView RTH chart).

| Source | BUY | SELL |
|---|---|---|
| Ribbon flip | EMA 9 crosses above EMA 21 | EMA 9 crosses below EMA 21 |
| MACD(12, 26, 9) | MACD crosses above signal while ribbon is up | MACD crosses below signal while ribbon is down |
| RSI(2) | RSI(2) < 10 and close > SMA 200 | RSI(2) > 90 and close < SMA 200 |
| Band re-entry, Bollinger(20, 2) | Close back above the lower band after closing below it, and close > SMA 200 | Close back below the upper band after closing above it, and close < SMA 200 |
| Gap fill | Session opens at least 0.25% **below** the prior session close: BUY at the close of the first 5m bar | Opens at least 0.25% **above**: SELL at the close of the first bar |

A BUY and a SELL on the same bar cancel out (no action).

## Trades

- Signal on a closed 5m bar, filled at the **next** bar's open
- Stop 1.5 × ATR(14) from entry, target 2R, stop moves to entry the bar **after** +1R is touched (same as the crypto clone)
- If stop and target are both touched in one bar, assume the **stop** hit first
- **Every BUY gets its SELL:** one position at a time. An opposite signal closes the position at the next open **and** opens the opposite side at that same open (stop and reverse)
- No new entries after 15:30. Anything open is closed at the **close of the 15:55 bar** (last bar of the day). Signals on the last bar are ignored
- Outsized candle filter: a bar whose range is more than 3 × ATR(14) pauses new entries for 90 minutes (18 bars)
- Risk: 1% of equity per trade, max position 100% of equity (no leverage), 3% daily loss halts new entries for the rest of the day
- Kill switch at 20% drawdown is recorded, but the test keeps running

## Not cloned (same reasoning as the crypto spec)

- News calendar (CPI, jobs, Fed), VIX and 10 year yield filters. Filters can only remove trades; added later with their own spec only if the core signals show an edge.

## Data

- Alpaca market data API, QQQ 5m bars, regular hours, **SIP feed** (all US exchanges). If the account only gets the IEX feed, stop and record that in the change log **before** any test, because IEX only bars miss most volume.
- Needs Alpaca keys in a local `.env` (gitignored). Data cached in gitignored files, never committed.
- Half trading days (like the day after Thanksgiving): the session's last bar is the close; same rules.

## Test periods

- **Development:** 2016-01-01 to 2022-12-31. Code checks only. Any change after seeing dev results goes in the change log and is committed **before** the holdout.
- **Holdout (the verdict):** 2023-01-01 to the run date. Run **once**.

## Passing bar (holdout only, all must hold, at real costs)

- Sharpe ≥ 0.5 (from daily returns, 252 trading days a year)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 100 trades
- Profitable in at least 60% of calendar quarters (the partial current quarter counts)

## Diagnostics (shown, never the verdict)

- Costs at 0.05% per side (stress) and at zero
- Long only version (like the crypto clone)
- Per signal source, per side, per exit reason
- Buy and hold QQQ over the same period

Picking a source, side or setting after seeing these is curve fitting. Any follow up needs its own locked spec.

## If it passes

Paper trading on a separate Alpaca paper account, same Telegram bot tagged "[QQQ]". Needs the always on server (GitHub's scheduler skips most runs, and the session is only 6.5 hours). Real money would also run into the Pattern Day Trader rule ($25k minimum for 4+ day trades in 5 business days; check whether FINRA's proposed change has taken effect).

## Change log

- (none yet)
