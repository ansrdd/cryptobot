# QuantFlow clone on QQQ 5m: DEV CHECK (not the verdict)

Alpaca SIP 5m bars, regular hours, split + dividend adjusted (research copy in data/research). SIP history starts 2016-01-04. Spec: docs/QQQ_DAYTRADE_SPEC.md. Settings: config_qqq_daytrade.yaml.

Window: 2016-01-04 09:30 to 2022-12-30 15:55 New York, 136,951 bars, 1,762 sessions

## Passing bar at real costs (2 bps per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -3.30 | FAIL |
| Profit factor >= 1.3 | 0.72 | FAIL |
| Total return > 0.0% | -93.43 | FAIL |
| Trades >= 100 | 8413 | pass |
| Quarters profitable >= 60% | 3.57 | FAIL |

Max drawdown -93.9%, win rate 30.5%, daily loss halts 8, kill switch (20% DD, recorded only): 2016-06-20 17:10:00+00:00

## Diagnostics (NOT the verdict)

| Version | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| long + short, real (2 bps) | -93.43 | -3.30 | 0.72 | 8413 |
| long + short, stress (5 bps) | -99.96 | -8.82 | 0.42 | 8409 |
| long + short, zero_cost (0 bps) | 95.17 | 0.92 | 1.07 | 8417 |
| long only, real | -77.36 | -2.77 | 0.70 | 4582 |

Buy and hold QQQ over the same window: +157.9%

Per source, side and exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
              trades       pnl  win_rate_pct  profit_factor
source                                                     
band_reentry     524  -2952.39         31.87           0.84
gap_fill         387  -1970.48         28.42           0.89
macd            1007  -7941.00         33.96           0.81
ribbon_flip     2574 -33863.08         30.96           0.66
rsi2            3921 -46703.78         29.35           0.71
```

```
       trades       pnl  win_rate_pct  profit_factor
side                                                
long     4581 -51211.53         30.84           0.69
short    3832 -42219.20         30.11           0.75
```

```
           trades        pnl  win_rate_pct  profit_factor
reason                                                   
breakeven    1279  -17423.41          0.00           0.00
close         261    7782.38         76.63           7.07
reverse      2208  -25612.74         36.23           0.53
signal        130    -938.04         43.08           0.66
stop         3024 -260781.51          0.00           0.00
target       1511  203542.59        100.00            inf
```

Average trade -0.032% (after costs), median hold 6 bars

Quarterly returns %:

```
timestamp
2016Q1    -0.55
2016Q2   -15.36
2016Q3   -17.32
2016Q4    -7.28
2017Q1   -12.43
2017Q2   -10.36
2017Q3    -8.44
2017Q4   -12.79
2018Q1    -0.46
2018Q2   -13.76
2018Q3    -9.89
2018Q4    -2.13
2019Q1   -12.81
2019Q2    -7.98
2019Q3    -9.83
2019Q4   -11.08
2020Q1     6.72
2020Q2   -16.20
2020Q3    -6.75
2020Q4   -19.56
2021Q1   -14.47
2021Q2    -6.43
2021Q3    -9.97
2021Q4    -7.50
2022Q1    -6.39
2022Q2   -11.16
2022Q3    -6.64
2022Q4    -3.91
Freq: Q-DEC
```
