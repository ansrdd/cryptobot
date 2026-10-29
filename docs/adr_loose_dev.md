# ADR Loose spec (looser TJR style) on QQQ + SPY 5m: DEV CHECK (not the verdict)

Alpaca SIP 5m bars incl. premarket from 04:00, split + dividend adjusted (research copy in data/research). SIP history starts 2016-01-04. Spec: docs/ADR_LOOSE_SPEC.md. Settings: config_adr_loose.yaml.

Window: 2016-01-04 to 2022-12-30 New York, 1,762 sessions

## Passing bar at real costs (2 bps per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -0.97 | FAIL |
| Profit factor >= 1.3 | 0.39 | FAIL |
| Total return > 0.0% | -4.22% | FAIL |
| Trades >= 50 | 58 | pass |
| Win rate >= breakeven + 5 pts | 25.9% vs 48.1% breakeven | FAIL |
| Quarters profitable >= 60% | 17.9% | FAIL |

Max drawdown -5.4%, daily loss halts 0, kill switch (20% DD, recorded only): never

## Diagnostics (NOT the verdict)

| Version | Return % | Sharpe | PF | Trades | Win % |
|---|---|---|---|---|---|
| as specced, real (2 bps) | -4.22 | -0.97 | 0.39 | 58 | 25.9 |
| stress (5 bps) | -5.86 | -1.29 | 0.28 | 58 | 24.1 |
| zero_cost (0 bps) | -3.10 | -0.72 | 0.50 | 58 | 29.3 |
| long only | -2.21 | -0.82 | 0.39 | 38 | 23.7 |
| short only | -2.05 | -0.59 | 0.40 | 20 | 30.0 |
| QQQ only | -2.10 | -0.82 | 0.38 | 32 | 21.9 |
| SPY only | -2.16 | -0.61 | 0.41 | 26 | 30.8 |
| with SMT required | -1.42 | -0.55 | 0.53 | 31 | 32.3 |

Buy and hold QQQ over the same window: +157.9%
Buy and hold SPY over the same window: +114.3%

Per symbol, side, swept level and exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
        trades      pnl  win_rate_pct  avg_r  profit_factor
symbol                                                     
QQQ         32 -2083.21         21.88  -0.48           0.38
SPY         26 -2132.80         30.77  -0.41           0.41
```

```
       trades      pnl  win_rate_pct  avg_r  profit_factor
side                                                      
long       38 -2177.81         23.68  -0.51           0.39
short      20 -2038.20         30.00  -0.33           0.40
```

```
           trades      pnl  win_rate_pct  avg_r  profit_factor
level                                                         
h1_swing        3   196.02         66.67   0.54           1.62
premarket      40 -3461.84         27.50  -0.47           0.30
prev_day       15  -950.19         13.33  -0.58           0.44
```

```
        trades      pnl  win_rate_pct  avg_r  profit_factor
reason                                                     
close       20   939.17          45.0   0.22           2.09
stop        32 -6103.25           0.0  -1.19           0.00
target       6   948.07         100.0   1.30            inf
```

```
       trades      pnl  win_rate_pct  avg_r  profit_factor
smt                                                       
False      30 -3115.15         20.00  -0.50           0.28
True       28 -1100.85         32.14  -0.39           0.58
```

```
             trades      pnl  win_rate_pct  avg_r  profit_factor
target_kind                                                     
fixed            12 -1606.04         16.67  -0.62           0.23
h1_swing         20  -887.90         30.00  -0.32           0.51
premarket        17 -1270.46         23.53  -0.58           0.45
prev_day          9  -451.62         33.33  -0.25           0.40
```

Average trade -0.148% / -0.45R after costs, median hold 32 bars, about 0.16 trades a week

Quarterly returns %:

```
2016Q1   -0.14
2016Q2    0.15
2016Q3   -0.14
2016Q4   -0.09
2017Q1    0.00
2017Q2   -0.40
2017Q3   -0.03
2017Q4   -0.01
2018Q1   -0.86
2018Q2   -0.44
2018Q3   -0.25
2018Q4   -0.75
2019Q1    0.00
2019Q2    0.16
2019Q3   -0.09
2019Q4   -0.39
2020Q1    0.00
2020Q2   -0.16
2020Q3    0.22
2020Q4    0.07
2021Q1    0.00
2021Q2   -0.24
2021Q3    0.00
2021Q4    0.00
2022Q1   -0.39
2022Q2   -0.35
2022Q3    0.14
2022Q4   -0.27
Freq: Q-DEC
```
