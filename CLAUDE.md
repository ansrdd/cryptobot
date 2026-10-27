# CryptoBot: rules for Claude Code

Paper trading crypto bot (BTC/USD, ETH/USD) on Alpaca paper. Live mode is refused in code on purpose. Read README.md for setup and strategy history.

## Ground rules
- **Lock settings before testing.** Write the spec and passing bar, commit it, THEN backtest. Never change numbers after seeing results to make a test pass. That's curve fitting.
- **Report honestly.** If a backtest fails its bar, say so plainly. Show cost sensitivity (real fees vs maker vs zero) as diagnostics, never as the verdict.
- **Real costs always:** 0.25% fee + 0.05% slippage per side (Alpaca crypto Tier 1 taker). Long only: Alpaca has no margin for crypto, so no shorts.
- **Don't break the live daily bot.** `config.yaml`, `bot/runner.py` and `.github/workflows/trade.yml` run every hour on GitHub Actions. New work goes in new files.
- The hourly workflow commits to `main` every run, so `git pull --rebase` before pushing.
- Never commit API keys or tokens. They live in GitHub Secrets and a local `.env` (gitignored).

## Current state (2026-10-27)
- **Live daily bot (v2):** daily trend following, checks stops hourly. Backtest +5.8%, Sharpe 0.21, PF 1.23. Failed its bar (Sharpe ≥ 0.5, PF ≥ 1.3) but runs on paper as a learning exercise.
- **Known issue:** GitHub's scheduler has been skipping most hourly runs (only 1 scheduled run between 07:21 and 17:50 UTC on 2026-10-27). Plan is an always on server later (Google Cloud free e2-micro in us-east1).
- **In progress: day trading mode.** A free, open clone of the QuantFlow TradingView indicator (thequantflow.com) on 5 minute bars.
  - Locked spec: `docs/DAYTRADE_SPEC.md`. Locked settings: `config_daytrade.yaml`. Both committed before any backtest.
  - Not built yet: `bot/quantflow.py` (signals), `bot/daytrade.py` (engine with target, stop, breakeven), 5m Coinbase data (`data.py` needs "5m": 300 sec granularity), a `daytrade-backtest` CLI command, and tests (including a no lookahead test).
  - Data: Coinbase public candles, 300 per request (step 299), from 2023-01-01. Don't commit 5m parquet files (too big).
  - Next: build it, backtest once with the locked settings, compare to the passing bar in the spec.

## Commands
```
python cli.py backtest               # daily bot backtest → docs/backtest.html
python cli.py backtest --synthetic   # offline smoke test
python -m pytest -q tests            # tests
```
