# ADR spec (named after Andres): TJR style model on QQQ + SPY 5m (DRAFT, not locked until committed)

**Goal:** test the TJR style "sweep, structure shift, fair value gap" model as a fixed rule set, and only take A+ setups: every check must pass or there is no trade. Andres's rule: follow the strategy always, stay consistent, and if a setup isn't high probability, don't trade it.

**Rule:** these settings are committed before any backtest runs. The holdout runs once. If it fails, it fails.

## Where the rules come from (honest note)

TJR's free bootcamp teaches the idea but leaves the exact picks (which level, which candle, which gap) to the trader's judgment. There is no official written rule set. The rules below are our objective version, built from his public model as described by third parties:

- The model: liquidity sweep, then market structure shift with displacement, then entry on the retrace into a fair value gap (FVG); stop beyond the sweep; target the opposite liquidity pool.
- Bias from the higher timeframe (1H/4H structure: higher highs and higher lows = up).
- Liquidity pools: previous day high/low, overnight session highs/lows, equal highs/lows.
- Trades NQ/ES in the New York morning; SMT divergence (NQ vs ES) as confirmation.
- Sources: TradingView "TJR v4 Strategy" and "FREEDOM TJR Model" descriptions, snappchart.app TJR strategy guide, TJR's own site (bootcamp overview). None of these are TJR's exact rules, so this is "TJR style", not a guaranteed match.

## Market

| Choice | Why |
|---|---|
| QQQ and SPY instead of NQ and ES | Alpaca has no futures. QQQ tracks the Nasdaq 100 (NQ), SPY tracks the S&P 500 (ES) |
| 5m bars, 9:30 to 16:00 New York | Entry chart. Premarket bars (4:00 to 9:30) are used only to mark the overnight high/low |
| Long AND short | The model is two sided. Intraday only, so no overnight borrow fees |
| Costs 0.02% per side | Same as the QQQ spec: no commission, covers spread, slippage, SEC/FINRA fees |

## The setup (long; short is the exact mirror)

All checks must pass. If any one fails, no trade.

1. **Bias (1H):** on regular hours 1H bars, mark swing highs/lows (a bar whose high/low beats the 2 bars on each side; confirmed only after those 2 right bars close). Bullish bias = the last confirmed swing high is above the one before it AND the last confirmed swing low is above the one before it. Bearish = both lower. Anything else = no trades that day for that symbol. Bias is fixed at 9:30 using only bars closed by then.
2. **Liquidity levels (sell side, for longs):** previous regular session low (PDL) and premarket low (4:00 to 9:30). 
3. **Sweep:** a 5m bar that starts between 9:30 and 11:00 trades below a sell side level and **closes back above it**. The sweep low is that bar's low (or the lowest low until the shift, if price goes lower).
4. **SMT confirmation:** at the sweep bar, the other ETF (SPY for QQQ, QQQ for SPY) has **not** traded below the same kind of level (its own PDL or premarket low, whichever was swept) since 9:30. One swept, the other held = divergence. Both swept = no trade.
5. **Structure shift with displacement:** within 12 bars (1 hour) after the sweep, a 5m bar **closes above** the last confirmed 5m swing high (2 bars each side) that formed before the sweep, and that bar's body is at least 50% of its range. If the sweep low breaks again first, the setup is dead.
6. **Fair value gap:** among the bars from the sweep to the shift bar, the most recent bullish gap: bar i's low is above bar i minus 2's high. The FVG is that gap (bottom = high of i minus 2, top = low of i). No gap = no trade.
7. **Room to target (the high probability check):** target = nearest buy side level above entry among: previous day high and premarket high (v2: the session high so far is no longer a target). If target is less than **2R** from entry, no trade.

## Trade

- **Entry:** limit buy at the FVG top, placed after the shift bar closes, valid for 12 bars. Filled only if a later bar trades **below** the limit (a touch is not a fill). Cancelled if price reaches the target or the sweep low before filling, or at 11:30.
- **Stop:** 1 cent below the sweep low. Fixed, never moved.
- **Target:** the level from step 7. Limit exit, filled only if a bar trades through it.
- Stop and target in the same bar: assume the **stop**.
- Gap through the stop: filled at that bar's open.
- Anything still open is closed at the close of the 15:55 bar.
- One trade per symbol per day. QQQ and SPY can both be in a trade.

## Risk

- 1% of equity risked per trade, max position 50% of equity per symbol (100% combined, no leverage)
- 3% daily loss halts new entries for the day
- Kill switch at 20% drawdown is recorded, but the test keeps running

## Not included (and why)

- Order blocks: overlaps with the FVG entry and adds a discretionary pick.
- Equal highs/lows, weekly levels, news filter, premium/discount: each is a filter that can only remove trades. Added later with its own spec only if the core model shows an edge.

## Data

- Alpaca SIP 5m bars for QQQ and SPY, split and dividend adjusted, cached in gitignored `data/research/`. 
- Half days: the session's last bar is the close.

## Test periods

- **Development:** 2016-01-01 to 2022-12-31. Code checks only. Any change after seeing dev results goes in the change log and is committed **before** the holdout.
- **Holdout (the verdict):** 2023-01-01 to the run date. Run **once**.
- Data honesty note: the QuantFlow QQQ test already used 2023+ QQQ as its holdout. That test shares no rules with this one and we only saw its totals, so we reuse the window, but it isn't perfectly unseen. The last 6 months of SPY/QQQ daily were also looked at on 2026-10-29. A forward paper test (below) is the clean check.

## Passing bar (holdout only, all must hold, at real costs)

- Sharpe ≥ 0.5 (daily returns, 252 trading days a year)
- Profit factor ≥ 1.3
- Total return > 0%
- At least 50 trades
- Win rate at least 5 points above the breakeven win rate for the trades taken (breakeven = 1 / (1 + average win R / average loss R)). This is the "high probability" check in numbers
- Profitable in at least 60% of calendar quarters

## Diagnostics (shown, never the verdict)

- Costs at 0.05% per side and at zero
- Long only, short only, QQQ only, SPY only
- Without the SMT check (to see if it actually helps)
- Buy and hold QQQ and SPY

Picking a symbol, side or setting after seeing these is curve fitting. Any follow up needs its own locked spec.

## If it passes

Forward paper test on a separate Alpaca paper account for at least 3 months, same Telegram bot tagged "[ADR]", on the always on server (needs a 5 minute run during 9:30 to 16:00). Only after that would it count as working.

## Change log

- 2026-10-29, before any backtest (committed with the code, nothing has been run on real data): how the code reads the parts the text left open.
  - Steps 3 and 5 together: after the sweep, a bar that trades past the sweep low but **closes back above the level** just extends the sweep low. A bar that **closes below the level** kills the setup.
  - The structure level is the last 5m swing high already confirmed (2 bars on its right closed) at the sweep bar. 5m swings run on regular hours bars across days.
  - A setup that dies, or an order that never fills, is not a trade, so the bot can keep looking for a new sweep later that morning. The one trade per symbol per day rule counts filled trades only.
  - On the bar an order fills, only the stop can also hit; a target touch on that same bar is ignored (order inside the bar is unknown).
  - Share counts are whole shares (Alpaca can't short fractional shares). The 3% daily loss halt blocks new setups; an order already placed can still fill.
  - Alpaca SIP data starts 2016-01-04, so there is no warm up before dev. Bias is "none" (no trades) until the 1H chart has two swing highs and two swing lows.
- 2026-10-29, **v2, after the dev check** (docs/adr_dev_v1.md: 0 trades in 2016 to 2022, so no result to fit to). The session high/low so far is dropped as a target. Why: after a sweep and a shift, the session extreme is almost always the swing that was just broken, sitting under 1R away, so the 2R check killed nearly every setup (2016: 22 of 23 setups with a gap failed it). The model targets the opposite **external** liquidity pool, which is the previous day and overnight (premarket) high/low. One change only; everything else, including the 2R minimum, stays. The 2023+ holdout is still unseen. If v2 fails its dev check, no v3 on this spec.
