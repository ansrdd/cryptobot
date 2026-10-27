# Day trading backtest: QuantFlow clone (real data)

Coinbase 5m candles from 2023-01-01. Spec: docs/DAYTRADE_SPEC.md. Settings: config_daytrade.yaml.

Period: 2023-01-01 00:00 to 2026-10-27 18:25 UTC, BTC/USD 401,746 bars, ETH/USD 401,738 bars

## Verdict at real costs (25 bps fee + 5 bps slippage per side): **FAIL**

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | -9.49 | FAIL |
| Profit factor >= 1.3 | 0.00 | FAIL |
| Total return > 0.0% | -99.97 | FAIL |
| Trades >= 100 | 4431 | pass |
| Quarters profitable >= 60% | 0.00 | FAIL |

Max drawdown -100.0%, win rate 0.7%, daily loss halts 177, kill switch (20% DD, recorded only): 2023-01-08 23:05:00+00:00

## Diagnostics (NOT the verdict)

Cost sensitivity:

| Costs | Return % | Sharpe | PF | Trades |
|---|---|---|---|---|
| real (25+5 bps) | -99.97 | -9.49 | 0.00 | 4431 |
| maker_no_slip (15+0 bps) | -99.97 | -11.89 | 0.05 | 8922 |
| zero_cost (0+0 bps) | 42.24 | 1.22 | 1.04 | 29247 |

Per signal source and per coin, at real costs. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec and a fresh test period.

```
              trades       pnl  win_rate_pct  profit_factor
source                                                     
band_reentry     336  -8742.56          0.89           0.00
macd             555 -11749.31          0.90           0.01
ribbon_flip     1476 -31923.31          0.14           0.00
rsi2            2064 -47551.59          1.02           0.01
```

```
         trades       pnl  win_rate_pct  profit_factor
symbol                                                
BTC/USD    2266 -50150.71          0.75            0.0
ETH/USD    2165 -49816.06          0.65            0.0
```

```
           trades       pnl  win_rate_pct  profit_factor
reason                                                  
breakeven     137  -3341.67          0.00           0.00
signal       3168 -65637.97          0.44           0.00
stop          981 -30187.97          0.00           0.00
target        145   -799.16         11.72           0.25
```

Quarterly returns %:

```
ts
2023Q1   -91.50
2023Q2   -91.86
2023Q3   -91.92
2023Q4   -40.54
2024Q1     0.00
2024Q2     0.00
2024Q3     0.00
2024Q4     0.00
2025Q1     0.00
2025Q2     0.00
2025Q3     0.00
2025Q4     0.00
2026Q1     0.00
2026Q2     0.00
2026Q3     0.00
2026Q4     0.00
Freq: Q-DEC
```
