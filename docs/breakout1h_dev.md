# 1h volatility breakout: DEV CHECK (not the verdict)

Coinbase 1h candles (research copy in data/research). Spec: docs/BREAKOUT1H_SPEC.md. Settings: config_breakout1h.yaml.

Window: 2020-01-01 00:00 to 2024-12-31 23:00 UTC

## Passing bar at real costs (25 bps fee + 5 bps slippage per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -0.96 | FAIL |
| Profit factor >= 1.3 | 0.86 | FAIL |
| Total return > 0.0% | -58.09 | FAIL |
| Trades >= 100 | 1024 | pass |
| Quarters profitable >= 60% | 25.00 | FAIL |

Max drawdown -66.3%, win rate 34.9%, daily loss halts 4, kill switch (20% DD, recorded only): 2020-10-14 14:00:00+00:00

## Diagnostics (NOT the verdict)

Cost sensitivity (the fee filter scales with cost, so it loosens at lower cost):

| Costs | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| real (25+5 bps) | -58.09 | -0.96 | 0.86 | 1024 |
| maker_no_slip (15+0 bps) | -0.70 | 0.08 | 1.00 | 1118 |
| zero_cost (0+0 bps) | 168.99 | 1.25 | 1.22 | 1125 |

Buy and hold over the same window, for context:

- BTC/USD: +1205.6%
- ETH/USD: +2494.9%

Per coin and per exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

```
         trades       pnl  win_rate_pct  profit_factor
symbol                                                
BTC/USD     480 -40783.65         35.42           0.77
ETH/USD     544 -17303.38         34.38           0.92
```

```
        trades        pnl  win_rate_pct  profit_factor
reason                                                
stop       611 -392290.26          0.00           0.00
target     218  277723.55        100.00            inf
time       195   56479.68         71.28           6.13
```

Quarterly returns %:

```
ts
2020Q1     3.72
2020Q2     3.69
2020Q3    -6.69
2020Q4     2.66
2021Q1    -0.98
2021Q2    -5.48
2021Q3     3.67
2021Q4    -9.70
2022Q1    -8.53
2022Q2    -5.64
2022Q3    -4.86
2022Q4    -9.35
2023Q1     1.99
2023Q2    -8.14
2023Q3    -8.09
2023Q4    -6.27
2024Q1    -4.49
2024Q2   -11.46
2024Q3    -3.86
2024Q4    -4.94
Freq: Q-DEC
```
