# ADR spec (TJR style) on QQQ + SPY 5m: DEV CHECK (not the verdict)

Alpaca SIP 5m bars incl. premarket from 04:00, split + dividend adjusted (research copy in data/research). SIP history starts 2016-01-04. Spec: docs/ADR_SPEC.md. Settings: config_adr.yaml.

Window: 2016-01-04 to 2022-12-30 New York, 1,762 sessions

## Passing bar at real costs (2 bps per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -0.70 | FAIL |
| Profit factor >= 1.3 | 0.12 | FAIL |
| Total return > 0.0% | -0.48% | FAIL |
| Trades >= 50 | 6 | FAIL |
| Win rate >= breakeven + 5 pts | 16.7% vs 73.0% breakeven | FAIL |
| Quarters profitable >= 60% | 3.6% | FAIL |

Max drawdown -0.8%, daily loss halts 0, kill switch (20% DD, recorded only): never

## Diagnostics (NOT the verdict)

| Version | Return % | Sharpe | PF | Trades | Win % |
|---|---|---|---|---|---|
| as specced, real (2 bps) | -0.48 | -0.70 | 0.12 | 6 | 16.7 |
| stress (5 bps) | -0.66 | -0.78 | 0.06 | 6 | 16.7 |
| zero_cost (0 bps) | -0.36 | -0.61 | 0.20 | 6 | 16.7 |
| long only | -0.43 | -0.74 | 0.00 | 4 | 0.0 |
| short only | -0.05 | -0.14 | 0.58 | 2 | 50.0 |
| QQQ only | -0.26 | -0.47 | 0.21 | 4 | 25.0 |
| SPY only | -0.23 | -0.53 | 0.00 | 2 | 0.0 |
| without the SMT check | -0.66 | -0.86 | 0.09 | 8 | 12.5 |

Buy and hold QQQ over the same window: +157.9%
Buy and hold SPY over the same window: +114.3%

Per symbol, side, swept level and exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
        trades     pnl  win_rate_pct  avg_r  profit_factor
symbol                                                    
QQQ          4 -258.78          25.0  -0.63           0.21
SPY          2 -224.92           0.0  -1.22           0.00
```

```
       trades     pnl  win_rate_pct  avg_r  profit_factor
side                                                     
long        4 -433.57           0.0  -1.04           0.00
short       2  -50.12          50.0  -0.40           0.58
```

```
           trades    pnl  win_rate_pct  avg_r  profit_factor
level                                                       
premarket       3 -198.3         33.33  -0.65           0.26
prev_day        3 -285.4          0.00  -1.00           0.00
```

```
        trades     pnl  win_rate_pct  avg_r  profit_factor
reason                                                    
close        2  -21.97          50.0  -0.04           0.76
stop         4 -461.73           0.0  -1.22           0.00
```

Average trade -0.162% / -0.83R after costs, median hold 12 bars, about 0.02 trades a week

Quarterly returns %:

```
2016Q1    0.00
2016Q2    0.00
2016Q3    0.00
2016Q4    0.00
2017Q1    0.00
2017Q2    0.00
2017Q3    0.00
2017Q4    0.00
2018Q1    0.00
2018Q2   -0.12
2018Q3    0.00
2018Q4   -0.09
2019Q1    0.00
2019Q2    0.00
2019Q3   -0.09
2019Q4   -0.11
2020Q1    0.00
2020Q2    0.00
2020Q3   -0.15
2020Q4    0.07
2021Q1    0.00
2021Q2    0.00
2021Q3    0.00
2021Q4    0.00
2022Q1    0.00
2022Q2    0.00
2022Q3    0.00
2022Q4    0.00
Freq: Q-DEC
```
