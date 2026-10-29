# ADR spec (TJR style) on QQQ + SPY 5m: DEV CHECK (not the verdict)

Alpaca SIP 5m bars incl. premarket from 04:00, split + dividend adjusted (research copy in data/research). SIP history starts 2016-01-04. Spec: docs/ADR_SPEC.md. Settings: config_adr.yaml.

Window: 2016-01-04 to 2022-12-30 New York, 1,762 sessions

## Passing bar at real costs (2 bps per side): **FAIL** (dev period, for reference only)

| Check | Result | |
|---|---|---|
| Sharpe >= 0.5 | nan | FAIL |
| Profit factor >= 1.3 | nan | FAIL |
| Total return > 0.0% | 0.00% | FAIL |
| Trades >= 50 | 0 | FAIL |
| Win rate >= breakeven + 5 pts | nan% vs nan% breakeven | FAIL |
| Quarters profitable >= 60% | 0.0% | FAIL |

Max drawdown 0.0%, daily loss halts 0, kill switch (20% DD, recorded only): never

## Diagnostics (NOT the verdict)

| Version | Return % | Sharpe | PF | Trades | Win % |
|---|---|---|---|---|---|
| as specced, real (2 bps) | 0.00 | nan | nan | 0 | nan |
| stress (5 bps) | 0.00 | nan | nan | 0 | nan |
| zero_cost (0 bps) | 0.00 | nan | nan | 0 | nan |
| long only | 0.00 | nan | nan | 0 | nan |
| short only | 0.00 | nan | nan | 0 | nan |
| QQQ only | 0.00 | nan | nan | 0 | nan |
| SPY only | 0.00 | nan | nan | 0 | nan |
| without the SMT check | 0.00 | nan | nan | 0 | nan |

Buy and hold QQQ over the same window: +157.9%
Buy and hold SPY over the same window: +114.3%

Per symbol, side, swept level and exit reason. Picking the best row after seeing this is curve fitting; any follow up needs its own locked spec.

Quarterly returns %:

```
2016Q1    0.0
2016Q2    0.0
2016Q3    0.0
2016Q4    0.0
2017Q1    0.0
2017Q2    0.0
2017Q3    0.0
2017Q4    0.0
2018Q1    0.0
2018Q2    0.0
2018Q3    0.0
2018Q4    0.0
2019Q1    0.0
2019Q2    0.0
2019Q3    0.0
2019Q4    0.0
2020Q1    0.0
2020Q2    0.0
2020Q3    0.0
2020Q4    0.0
2021Q1    0.0
2021Q2    0.0
2021Q3    0.0
2021Q4    0.0
2022Q1    0.0
2022Q2    0.0
2022Q3    0.0
2022Q4    0.0
Freq: Q-DEC
```
