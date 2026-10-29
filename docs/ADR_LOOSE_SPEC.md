# ADR Loose spec: looser TJR style model on QQQ + SPY 5m (DRAFT, not locked until committed)

**Goal:** the ADR spec (`docs/ADR_SPEC.md`) followed the TJR checklist so strictly that it traded 6 times in 7 years. This is a separate, looser reading of the same model, closer to how the third party write ups describe it: SMT is a bonus, not a must, more kinds of liquidity count, and a trade needs more reward than risk instead of 2x.

**Rule:** settings committed before any backtest of this spec. The holdout runs once. If it fails, it fails. No v2 of this spec.

## Honesty note

- We already saw ADR's dev results (2016 to 2022) before writing this. Every change below is justified from the sources ADR cited, not from those 6 trades, but this dev period is not blind. It's only a code check here. **The 2023+ holdout has never been run by any TJR spec, so it is the real test.**
- Same sources as ADR: TradingView "TJR v4 Strategy" (SMT is one optional filter in a confluence score; fallback R:R when no pool is in reach; equal highs/lows and session levels as pools), snappchart.app's TJR guide (seven steps with no SMT; pools are "equal highs or lows, or obvious untouched swing points"), TJR's site. Still "TJR style", not TJR's exact rules, and still no Asia/London levels (no ETF bars before 4 AM).

## What changes from ADR (everything else is identical)

| ADR | ADR Loose | Why |
|---|---|---|
| SMT required | **SMT not required.** Logged per trade, shown as a diagnostic | The seven step guide doesn't use it; the v4 script treats it as one optional confluence |
| Liquidity = previous day + premarket high/low | **Plus the most recent untouched 1H swing high/low** | "Obvious untouched swing points" are pools in the guide |
| Target = nearest opposite pool, at least 2R | **Target = nearest opposite pool at least 1R away; if none, a fixed 2R target** | Never take less reward than risk. The v4 script has a fallback R:R when no pool is in reach |

## The setup (long; short is the exact mirror)

1. **Bias (1H):** same as ADR. Bullish = last confirmed 1H swing high and swing low both higher than the ones before. Bearish = both lower. Else no trades that day.
2. **Sell side levels (for longs):** previous session low, premarket low (4:00 to 9:30), and the **most recent confirmed 1H swing low that price hasn't traded below since it was confirmed** (checked as of the sweep bar, using 1H bars already closed).
3. **Sweep:** same as ADR. A 5m bar starting 9:30 to 11:00 trades below a level and closes back above it. Closing back above while going lower extends the sweep low; closing below the level kills the setup.
4. **SMT:** not required. Recorded on each trade (did the other ETF also break its own same kind of level since 9:30?).
5. **Structure shift:** same as ADR. Within 12 bars, a bar with a body at least 50% of its range closes above the last 5m swing high confirmed at the sweep.
6. **Fair value gap:** same as ADR. Newest bullish gap from the sweep to the shift bar. No gap, no trade.
7. **Target:** nearest buy side level above entry that is **at least 1R** away, among: previous day high, premarket high, the most recent untouched 1H swing high. If none qualifies, target = entry + 2R.

## Trade, risk, data

Identical to ADR: limit at the FVG edge, valid 12 bars, never at/after 11:30, filled only if price trades through it; stop 1 cent past the sweep extreme; target filled only if price trades through it; stop first if both hit in a bar; flat at the 15:55 bar's close; one filled trade per symbol per day; 1% risk, max 50% of equity per symbol, whole shares, 3% daily loss halt, 20% kill switch recorded only. Costs 0.02% per side. Alpaca SIP 5m with premarket.

## Test periods

- **Development:** 2016-01-01 to 2022-12-31. Code check only (not blind, see above).
- **Holdout (the verdict):** 2023-01-01 to the run date. Run **once**.

## Passing bar (holdout only, all must hold, at real costs)

Same as ADR:
- Sharpe ≥ 0.5 (daily returns, 252 days)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 50 trades
- Win rate at least 5 points above breakeven for the trades taken
- Profitable in at least 60% of calendar quarters

## Diagnostics (shown, never the verdict)

- Costs at 0.05% per side and at zero
- Long only, short only, QQQ only, SPY only
- Trades **with** SMT vs without (from the per trade flag)
- Per swept level (previous day, premarket, 1H swing) and per target type (pool vs fixed 2R)
- Buy and hold QQQ and SPY

Picking a level, side or setting after seeing these is curve fitting.

## If it passes

Forward paper test on a separate Alpaca paper account for at least 3 months, Telegram tag "[ADR]", on the always on server. Only after that would it count as working.

## Change log

- 2026-10-29, before any backtest of this spec (committed with the code): how the code reads the open parts.
  - "Most recent untouched 1H swing low" = the newest confirmed 1H swing low that no 5m bar has traded below since; once traded through it is gone. Checked as of the bar's open for sweeps, and after the shift bar for targets.
  - SMT flag for a 1H swing sweep: did the other ETF trade below its own untouched 1H swing low (as of 9:30) since 9:30.
  - Everything shared with ADR runs through the same code; ADR's dev result was rerun after the change and is identical (6 trades, -0.48%, PF 0.12).
