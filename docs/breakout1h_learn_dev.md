# 1h breakout + learning filter: DEV CHECK (not the verdict)

Coinbase 1h candles (research copy in data/research). Spec: docs/BREAKOUT1H_LEARN_SPEC.md. Settings: config_breakout1h_learn.yaml + config_breakout1h.yaml.

Window: 2020-01-01 00:00 to 2024-12-31 23:00 UTC

## Passing bar at real costs (25 bps fee + 5 bps slippage per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -0.03 | FAIL |
| Profit factor >= 1.3 | 0.98 | FAIL |
| Total return > 0.0% | -4.39 | FAIL |
| Trades >= 100 | 388 | pass |
| Quarters profitable >= 60% | 40.00 | FAIL |

Max drawdown -25.0%, win rate 38.9%, daily loss halts 2, kill switch (20% DD, recorded only): 2024-04-27 19:00:00+00:00

## Diagnostics (NOT the verdict)

### Did it learn anything

- Candidates in window: 1024, scored by a trained model: 999 (the rest came before 200 labeled trades existed, or had missing inputs)
- Out of sample AUC: 0.559 (0.5 = coin flip)
- Taken: 388, net win rate 38.9%, avg net return -0.042%
- Skipped: 611, net win rate 31.4%, avg net return -0.466%
- Typical win rate needed (median p_needed): 39.1%

### Plain breakout, same window (no filter)

Return -58.09%, Sharpe -0.96, PF 0.86, 1024 trades

### Cost sensitivity (approvals fixed from the real cost model; only execution costs change)

| Costs | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| real (25+5 bps) | -4.39 | -0.03 | 0.98 | 388 |
| maker_no_slip (15+0 bps) | 34.89 | 0.62 | 1.16 | 388 |
| zero_cost (0+0 bps) | 90.23 | 1.26 | 1.40 | 388 |

### Model weights over time (standardized inputs; + means more likely to win)

```
               2020-02 (n=206)  2022-07 (n=726)  2024-12 (n=1190)  same sign % of months
atr_pct                 -0.138            0.108             0.170                 89.831
atr_ratio                0.349           -0.021            -0.082                 55.932
trend_dist               0.497            0.539             0.395                100.000
ema_slope               -0.613           -0.436            -0.251                100.000
brk_strength             0.012            0.104             0.130                100.000
log_vol_ratio            0.146            0.035            -0.045                 55.932
hour_sin                -0.024           -0.000            -0.107                 32.203
hour_cos                -0.058           -0.033            -0.059                100.000
weekend                 -0.285           -0.272            -0.225                100.000
ret_short               -0.693           -0.232            -0.142                100.000
ret_long                -0.314           -0.116            -0.188                100.000
btc_trend                0.320           -0.116            -0.052                 89.831
```

Per coin and per exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
         trades       pnl  win_rate_pct  profit_factor
symbol                                                
BTC/USD     197 -17980.32         36.55           0.82
ETH/USD     191  13589.84         41.36           1.13
```

```
        trades        pnl  win_rate_pct  profit_factor
reason                                                
stop       212 -196895.38          0.00           0.00
target      91  158318.63        100.00            inf
time        85   34186.28         70.59           5.43
```

Quarterly returns %:

```
ts
2020Q1    -0.68
2020Q2    15.59
2020Q3     1.76
2020Q4     3.32
2021Q1    -4.29
2021Q2    -0.42
2021Q3     2.94
2021Q4    -2.41
2022Q1    -1.77
2022Q2     0.22
2022Q3     2.50
2022Q4    -3.50
2023Q1     1.22
2023Q2     0.32
2023Q3    -1.33
2023Q4    -2.64
2024Q1    -4.51
2024Q2    -2.41
2024Q3    -3.71
2024Q4    -2.94
Freq: Q-DEC
```
