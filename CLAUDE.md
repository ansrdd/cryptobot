# CryptoBot: rules for Claude Code

Paper trading crypto bot (BTC/USD, ETH/USD) on Alpaca paper. Live mode is refused in code on purpose. Read README.md for setup and strategy history.

## Ground rules
- **Lock settings before testing.** Write the spec and passing bar, commit it, THEN backtest. Never change numbers after seeing results to make a test pass. That's curve fitting.
- **Report honestly.** If a backtest fails its bar, say so plainly. Show cost sensitivity (real fees vs maker vs zero) as diagnostics, never as the verdict.
- **Real costs always:** 0.25% fee + 0.05% slippage per side (Alpaca crypto Tier 1 taker). Long only: Alpaca has no margin for crypto, so no shorts.
- **Don't break the live daily bot.** `config.yaml`, `bot/runner.py` and `.github/workflows/trade.yml` run every hour on GitHub Actions. New work goes in new files.
- The hourly workflow commits to `main` every run, so `git pull --rebase` before pushing.
- Never commit API keys or tokens. They live in GitHub Secrets and a local `.env` (gitignored).

## Current state (2026-10-27, updated after day trade run)
- **Live daily bot (v2):** daily trend following, checks stops hourly. Backtest +5.8%, Sharpe 0.21, PF 1.23. Failed its bar (Sharpe ≥ 0.5, PF ≥ 1.3) but runs on paper as a learning exercise.
- **Known issue:** GitHub's scheduler has been skipping most hourly runs (only 1 scheduled run between 07:21 and 17:50 UTC on 2026-10-27). Plan is an always on server later (Google Cloud free e2-micro in us-east1).
- **Day trading mode: built, tested once, FAILED (2026-10-27).** A free, open clone of the QuantFlow TradingView indicator (thequantflow.com) on 5 minute bars.
  - Locked spec `docs/DAYTRADE_SPEC.md` + settings `config_daytrade.yaml` (763ed29), code committed before the run (63bf75d): `bot/quantflow.py` (signals), `bot/daytrade.py` (engine), `tests/test_daytrade.py` (incl. no lookahead tests).
  - Result (`docs/daytrade_backtest.md`, 6c58988): -99.97%, Sharpe -9.49, PF 0.00, 4,431 trades. Failed 4 of 5 checks.
  - Cause is fees: median 5m ATR is 0.15% (BTC) / 0.20% (ETH), so a 2R target (~0.45% / ~0.61%) is at or below the 0.60% round trip cost. At zero cost: +42%, PF 1.04, avg trade +0.004%, i.e. no real edge.
  - **Do not tune this spec.** Any new day trading idea needs its own locked spec and a fresh test period.
  - Data: Coinbase public 5m candles from 2023-01-01, cached in `data/*_5m.parquet` (gitignored, too big).
- **1h volatility breakout ("slow day trading").** Spec and settings locked BEFORE any backtest (2b76f14): `docs/BREAKOUT1H_SPEC.md`, `config_breakout1h.yaml`.
  - Built: `bot/breakout1h.py`, `tests/test_breakout1h.py`, `python cli.py breakout1h-backtest` (dev check). `--holdout` is the one shot verdict and refuses to run twice.
  - **Dev check (2020 to 2024) FAILED:** -58%, Sharpe -0.96, PF 0.86, 1,024 trades (`docs/breakout1h_dev.md`). Zero cost +169%, PF 1.22: real signal, but the 0.60% round trip is about a third of 1R. Maker fees only get it to breakeven.
  - **Standalone holdout retired, never run** (logged in its spec's change log) so 2025+ stays unseen for the learner below.
  - Don't write to the tracked `data/*_1h.parquet` files (the live bot commits them); work from a copy.
  - Paper trading only if it passes: same Telegram bot with a "[1h]" tag, separate Alpaca paper account, needs the always on server.
- **1h breakout + learning trade filter (meta labeling).** Spec locked BEFORE any backtest (0884293): `docs/BREAKOUT1H_LEARN_SPEC.md`, `config_breakout1h_learn.yaml`.
  - Same base signal and trade rules. L2 logistic regression on 12 inputs known at signal time predicts P(net win); retrain monthly on candidates that already EXITED (expanding window, 200 sample warm up). Take a trade only if P >= the PF 1.3 breakeven for that trade's cost/R.
  - Built: `bot/breakout1h_learn.py` (features, candidates + labels, numpy logistic regression, monthly walk forward), `tests/test_breakout1h_learn.py`, `python cli.py breakout1h-learn-backtest` (`--holdout` runs once, refuses a rerun). `bot/breakout1h.py` got an optional `buy_filter` hook (no change when unused).
  - **Dev check (2020 to 2024) FAILED, but close:** -4.4%, Sharpe -0.03, PF 0.98, 388 trades (`docs/breakout1h_learn_dev.md`). AUC 0.559; taken trades won 38.9% vs 31.4% skipped, needed ~39%. Faded in 2024. Maker fees: +35%, PF 1.16.
  - **Holdout NOT run.** Andres's call.
  - Dev 2020 to 2024 for code checks; holdout 2025+ once. Plain breakout 2025+ only shown as a diagnostic after the verdict.
- **Local setup:** venv at `.venv` (Python 3.13). System Python is 3.9 with no deps.

## Commands
```
python cli.py backtest               # daily bot backtest → docs/backtest.html
python cli.py backtest --synthetic   # offline smoke test
python -m pytest -q tests            # tests
python cli.py daytrade-backtest       # QuantFlow clone on 5m → docs/daytrade_backtest.md (already run once, failed)
```
