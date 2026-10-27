# QuantFlow clone on QQQ 5m: HOLDOUT VERDICT

Alpaca SIP 5m bars, regular hours, split + dividend adjusted (research copy in data/research). SIP history starts 2016-01-04. Spec: docs/QQQ_DAYTRADE_SPEC.md. Settings: config_qqq_daytrade.yaml.

Window: 2023-01-03 09:30 to 2026-10-26 15:55 New York, 74,358 bars, 957 sessions

## Passing bar at real costs (2 bps per side): **FAIL**

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -4.81 | FAIL |
| Profit factor >= 1.3 | 0.69 | FAIL |
| Total return > 0.0% | -84.37 | FAIL |
| Trades >= 100 | 4500 | pass |
| Quarters profitable >= 60% | 0.00 | FAIL |

Max drawdown -85.2%, win rate 29.3%, daily loss halts 4, kill switch (20% DD, recorded only): 2023-07-06 16:05:00+00:00

## Diagnostics (NOT the verdict)

| Version | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| long + short, real (2 bps) | -84.37 | -4.81 | 0.69 | 4500 |
| long + short, stress (5 bps) | -98.94 | -10.73 | 0.44 | 4497 |
| long + short, zero_cost (0 bps) | -4.59 | -0.08 | 0.99 | 4502 |
| long only, real | -63.97 | -3.77 | 0.68 | 2427 |

Buy and hold QQQ over the same window: +170.8%

Per source, side and exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
              trades       pnl  win_rate_pct  profit_factor
source                                                     
band_reentry     259  -6309.26         26.64           0.68
gap_fill         177  -7263.29         23.73           0.51
macd             545  -7471.26         31.93           0.78
ribbon_flip     1361 -28182.99         30.64           0.61
rsi2            2158 -35143.94         28.54           0.73
```

```
       trades       pnl  win_rate_pct  profit_factor
side                                                
long     2426 -42189.69         29.51           0.70
short    2074 -42181.05         29.03           0.67
```

```
           trades        pnl  win_rate_pct  profit_factor
reason                                                   
breakeven     707  -13930.16          0.00           0.00
close         188    6576.66         67.55           4.08
reverse      1191  -27219.47         33.59           0.41
signal         77    -502.03         33.77           0.73
stop         1572 -206848.07          0.00           0.00
target        765  157552.33        100.00            inf
```

Average trade -0.041% (after costs), median hold 7 bars

Quarterly returns %:

```
timestamp
2023Q1    -1.75
2023Q2   -14.50
2023Q3   -14.11
2023Q4    -8.51
2024Q1   -14.36
2024Q2   -10.18
2024Q3   -14.11
2024Q4   -10.67
2025Q1    -8.56
2025Q2   -19.34
2025Q3   -16.44
2025Q4   -11.61
2026Q1   -14.99
2026Q2    -0.08
2026Q3    -8.56
2026Q4    -5.19
Freq: Q-DEC
```
