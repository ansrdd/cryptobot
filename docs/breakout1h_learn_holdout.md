# 1h breakout + learning filter: HOLDOUT VERDICT

Coinbase 1h candles (research copy in data/research). Spec: docs/BREAKOUT1H_LEARN_SPEC.md. Settings: config_breakout1h_learn.yaml + config_breakout1h.yaml.

Window: 2025-01-01 00:00 to 2026-10-27 19:00 UTC

## Passing bar at real costs (25 bps fee + 5 bps slippage per side): **FAIL**

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -1.55 | FAIL |
| Profit factor >= 1.3 | 0.47 | FAIL |
| Total return > 0.0% | -15.02 | FAIL |
| Trades >= 100 | 55 | FAIL |
| Quarters profitable >= 60% | 25.00 | FAIL |

Max drawdown -18.9%, win rate 23.6%, daily loss halts 0, kill switch (20% DD, recorded only): never

## Diagnostics (NOT the verdict)

### Did it learn anything

- Candidates in window: 326, scored by a trained model: 326 (the rest came before 200 labeled trades existed, or had missing inputs)
- Out of sample AUC: 0.502 (0.5 = coin flip)
- Taken: 55, net win rate 23.6%, avg net return -0.980%
- Skipped: 271, net win rate 26.2%, avg net return -0.754%
- Typical win rate needed (median p_needed): 40.9%

### Plain breakout, same window (no filter)

Return -54.45%, Sharpe -3.47, PF 0.50, 326 trades

### Cost sensitivity (approvals fixed from the real cost model; only execution costs change)

| Costs | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| real (25+5 bps) | -15.02 | -1.55 | 0.47 | 55 |
| maker_no_slip (15+0 bps) | -10.72 | -1.09 | 0.58 | 55 |
| zero_cost (0+0 bps) | -6.21 | -0.61 | 0.73 | 55 |

### Model weights over time (standardized inputs; + means more likely to win)

```
               2025-01 (n=1206)  2025-12 (n=1382)  2026-10 (n=1526)  same sign % of months
atr_pct                   0.171             0.157             0.135                100.000
atr_ratio                -0.081            -0.130            -0.125                100.000
trend_dist                0.404             0.332             0.251                100.000
ema_slope                -0.261            -0.135            -0.111                100.000
brk_strength              0.123             0.129             0.137                100.000
log_vol_ratio            -0.038            -0.007            -0.003                 90.909
hour_sin                 -0.105            -0.087            -0.084                100.000
hour_cos                 -0.048            -0.046            -0.056                100.000
weekend                  -0.222            -0.221            -0.209                100.000
ret_short                -0.146            -0.109            -0.078                100.000
ret_long                 -0.176            -0.147            -0.102                100.000
btc_trend                -0.062            -0.080            -0.079                100.000
```

Per coin and per exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
         trades      pnl  win_rate_pct  profit_factor
symbol                                               
BTC/USD      17 -7787.74         17.65           0.13
ETH/USD      38 -7232.44         26.32           0.62
```

```
        trades       pnl  win_rate_pct  profit_factor
reason                                               
stop        38 -27858.99          0.00           0.00
target       8  11074.78        100.00            inf
time         9   1764.03         55.56           8.56
```

Quarterly returns %:

```
ts
2025Q1   -7.25
2025Q2   -4.55
2025Q3    0.27
2025Q4   -1.16
2026Q1   -2.76
2026Q2   -3.22
2026Q3    3.60
2026Q4   -0.66
Freq: Q-DEC
```
