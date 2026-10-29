# Global Trend spec: multi asset trend following with ETFs (DRAFT, not locked until committed)

**Goal:** stop fighting for tiny edges on short timeframes. Copy the one systematic approach with over a century of public evidence that a small account can actually run: trend following across many different asset classes, sized by risk, traded monthly.

**Rule:** settings committed before any data is downloaded or any backtest runs. The holdout runs once. If it fails, it fails.

## Why this, and not another chart signal (research, 2026-10-29)

| What we learned | Source | How this spec uses it |
|---|---|---|
| Trend following across 67 markets (stocks, bonds, commodities, currencies) made money in every decade since 1880, with low correlation to stocks and strong results in stock bear markets. Sharpe about 0.4 even after 2/20 fees (secondary summary) | Hurst, Ooi, Pedersen (AQR), "A Century of Evidence on Trend Following Investing" | Many asset classes, 1/3/12 month lookbacks |
| They size each market so it carries the same risk (volatility target), not the same dollars | same paper | Inverse volatility weights |
| A simple monthly trend rule on 5 asset classes matched buy and hold returns with far smaller drawdowns | Faber, "A Quantitative Approach to Tactical Asset Allocation" | Monthly, long or cash, benchmark vs buy and hold |
| Backtest Sharpe barely predicts live results (888 Quantopian algos, R² < 0.025), and the more you tweak, the worse live gets | Wiecki et al. (Quantopian) | Few settings, textbook values, locked first, one holdout |
| No independent evidence retail grid/DCA bots are profitable; big claims are cherry picked | Search found only vendor and commentary sources | Not copying them |
| Pros like Renaissance win with thousands of tiny edges, huge data and cheap execution | Public profiles | Not copyable at our size; we pick a slow, cheap, well documented edge instead |

What's different from our crypto bot: it has 2 coins that move together. This has 8 markets that often don't, which is where trend following gets most of its edge.

## Universe (long only, ETFs Alpaca can trade)

| ETF | What | Since |
|---|---|---|
| SPY | US stocks | 1993 |
| EFA | Developed international stocks | 2001 |
| EEM | Emerging market stocks | 2003 |
| IEF | 7 to 10 year US Treasuries | 2002 |
| TLT | 20+ year US Treasuries | 2002 |
| GLD | Gold | 2004 |
| DBC | Broad commodities | 2006 |
| VNQ | US real estate | 2004 |
| SHY | Cash stand in (1 to 3 year Treasuries); holds whatever isn't invested | 2002 |

All 9 exist from 2006-02 (DBC's start). With a 12 month lookback, trading starts 2007-02, so 2008 is in the test.

## Rules

Checked once a month, on the **last trading day's close**:

1. **Trend score per ETF (not SHY):** average of three yes/no checks on total return (dividends included): last 1 month > 0, last 3 months > 0, last 12 months > 0. Score is 0, 1/3, 2/3 or 1.
2. **Risk weight per ETF:** 1 / its volatility, where volatility = standard deviation of the last 63 daily returns (about 3 months). Normalized so the 8 risk weights add to 100%.
3. **Target weight** = risk weight × trend score. Whatever is left over goes to SHY. Never more than 100% invested (no leverage, no shorts).
4. **Trade** to the target weights at the **next trading day's open**. Skip any trade smaller than 0.5% of equity (saves costs on tiny changes).
5. Nothing happens between month ends: no stops, no intraday checks.

## Costs

0.02% per side (commission free ETFs; covers spread and slippage). Same as the stock specs.

## Data

- Daily adjusted OHLC (splits + dividends) from **Yahoo Finance** (free, back to each ETF's start). Alpaca's history only starts in 2016, which would miss 2008.
- **Data check before any backtest:** compare Yahoo's adjusted daily returns with Alpaca's for 2016+ on every ETF. If any ETF's daily returns differ by more than 0.1% on more than 1% of days, stop and log it in the change log before going on.
- Cached in gitignored `data/research/`.

## Test periods

- **Development:** 2007-02 to 2016-12 (includes 2008). Code check.
- **Holdout (the verdict):** 2017-01 to the run date (includes 2020, 2022). Run **once**.
- Data honesty note: we looked at SPY and QQQ daily prices over the last 6 months on 2026-10-29, and we know roughly how stocks did since 2017 (everyone does). The rules here were picked from published research, not from that.

## Passing bar (holdout only, all must hold, at real costs)

The point is better **risk adjusted** returns than just holding, not beating SPY in a bull market.

- Sharpe ≥ 0.5 (monthly returns, annualized, 0% risk free so it's comparable with our other specs)
- Sharpe ≥ the Sharpe of a **60/40 portfolio** (60% SPY, 40% IEF, rebalanced monthly) over the same window
- Max drawdown no more than **half** of SPY buy and hold's max drawdown over the same window
- Total return > 0%
- Positive in at least 60% of calendar years (the partial current year counts)

## Diagnostics (shown, never the verdict)

- SPY buy and hold, 60/40, equal weight buy and hold of the 8 ETFs
- Costs at 0.05% per side and at zero
- Each lookback alone (1, 3, 12 month), equal weights instead of risk weights
- Yearly returns and the worst 3 drawdowns vs SPY

Picking a lookback or asset after seeing these is curve fitting.

## If it passes

Paper trade it on a separate Alpaca paper account, rebalanced monthly from the always on server (one run on the last trading day plus one at the next open), Telegram tag "[TREND]". Fractional shares are fine here (long only). Real money only after a clean paper run.

## Change log

- 2026-10-29, data check, before any backtest (no strategy results seen). Yahoo vs Alpaca **daily bars**, 2016+: SPY (1.62% of days over 0.1%) and EEM (1.03%) failed the 1% limit; the other 7 passed. Cause: Alpaca's daily bar close sometimes picks up an after hours print instead of the 4:00 PM close (e.g. SPY 2020-08-31: Alpaca daily 321.34, last regular hours 5m bar 320.91, Yahoo 320.89). Rechecked SPY against Alpaca's **regular hours 5m closes** instead: 0.88% of 2,720 days over 0.1%, which passes. Yahoo matches the real session close better, so the backtest uses Yahoo as planned. The fault was in the reference, not the data.
