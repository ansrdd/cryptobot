# CryptoBot: rules for Claude Code

Paper trading crypto bot (BTC/USD, ETH/USD) on Alpaca paper. Live mode is refused in code on purpose. Read README.md for setup and strategy history.

## Ground rules
- **Lock settings before testing.** Write the spec and passing bar, commit it, THEN backtest. Never change numbers after seeing results to make a test pass. That's curve fitting.
- **Report honestly.** If a backtest fails its bar, say so plainly. Show cost sensitivity (real fees vs maker vs zero) as diagnostics, never as the verdict.
- **Real costs always:** 0.25% fee + 0.05% slippage per side (Alpaca crypto Tier 1 taker). Long only: Alpaca has no margin for crypto, so no shorts.
- **Don't break the live daily bot.** `config.yaml`, `bot/runner.py`, `bot/alerts.py` and `scripts/vm_run.sh` run every hour on the always on server (see below). New work goes in new files.
- The server commits to `main` every hour (`run ... (server)` commits), so `git pull --rebase` before pushing.
- Never commit API keys or tokens. They live in GitHub Secrets and a local `.env` (gitignored).

## Current state (2026-10-27, end of day: day trading track closed)
- **Live daily bot (v2):** daily trend following, checks stops hourly. Backtest +5.8%, Sharpe 0.21, PF 1.23. Failed its bar (Sharpe ≥ 0.5, PF ≥ 1.3) but runs on paper as a learning exercise.
- **Always on server (since 2026-10-27):** Google Cloud e2-micro `cryptobot`, zone us-east1-b, project `crypto-bot-512920`, Debian 13, Python 3.13, 1 GB swap. cron runs `~/cryptobot/scripts/vm_run.sh` at :04 UTC every hour (pull, `cli.py run`, commit state + docs, push via a read/write deploy key `cryptobot-vm`). Keys in `~/cryptobot/.env` on the server (chmod 600). Log: `~/cryptobot-run.log`. Reach it from the Mac with `gcloud compute ssh cryptobot --zone us-east1-b` (browser SSH times out). $1 budget alert on the billing account.
  - GitHub's hourly schedule is OFF (`trade.yml` is manual only) so the two can't double up orders. `pages.yml` publishes `docs/` to Pages whenever the server pushes.
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
  - **HOLDOUT (2025+, the verdict) FAILED:** -15%, Sharpe -1.55, PF 0.47, 55 trades (`docs/breakout1h_learn_holdout.md`). AUC 0.502: what it learned didn't carry forward. Plain 1h breakout over the same window: -54%. Closed; don't tune.
  - Dev 2020 to 2024 for code checks; holdout 2025+ once. Plain breakout 2025+ only shown as a diagnostic after the verdict.
- **QuantFlow clone on QQQ 5m stocks.** Spec locked BEFORE any data (e995b64): `docs/QQQ_DAYTRADE_SPEC.md`, `config_qqq_daytrade.yaml`.
  - Fair test of the idea on the index it was built for, with commission free costs (0.02% per side). Regular hours, gap fill ON, long AND short with stop and reverse, flat at the close, no leverage. Dev 2016 to 2022, holdout 2023+ once.
  - Built: `bot/stocks_data.py` (Alpaca SIP 5m via local `.env` keys, market calendar, regular hours only, split + dividend adjusted, drops an unfinished session; cache in gitignored `data/research/`), `bot/qqq_daytrade.py` (long/short, stop and reverse, gap fill, no fills at/after 15:30, flat at the close), `tests/test_qqq_daytrade.py`, `python cli.py qqq-backtest` (`--holdout` runs once, refuses a rerun). `daytrade.metrics` takes `periods_per_year` (252 for stocks).
  - SIP history starts 2016-01-04 (not 2015), so no warmup before dev; indicators only need days.
  - **Dev check (2016 to 2022) FAILED hard:** -93%, Sharpe -3.30, PF 0.72, 8,413 trades, 1 of 28 quarters up (`docs/qqq_daytrade_dev.md`). Median 1R is 0.19% of price, so 0.04% round trip is ~1/5 of 1R; avg trade before costs +0.008%. Zero cost +95%, PF 1.07 vs buy and hold +158%.
  - **HOLDOUT (2023+, the verdict) FAILED:** -84%, Sharpe -4.81, PF 0.69, 4,500 trades, 0 of 16 quarters up (`docs/qqq_daytrade_holdout.md`). Zero cost PF 0.99: no edge even for free. Buy and hold +171%. Closed; don't tune.
- **Day trading track CLOSED (2026-10-27).** Four strategies built and tested with locked specs; none passed (5m crypto, 1h breakout, 1h learner, 5m QQQ). Lesson: cost relative to the move kills short timeframe trading, not fee size, and none of these signals had a real edge out of sample. The daily trend bot is the only strategy running. Any new idea needs its own locked spec and fresh data; the holdouts above are spent.
- **Next:** confirm the server's cron runs every hour and the bill stays ~$0 (free tier e2-micro; first days' billing not yet verified).
- **Local setup:** venv at `.venv` (Python 3.13). System Python is 3.9 with no deps. Local `.env` (gitignored) holds the Alpaca paper keys for stock data; never print or commit it.

## Commands
```
python cli.py backtest               # daily bot backtest → docs/backtest.html
python cli.py backtest --synthetic   # offline smoke test
python -m pytest -q tests            # tests
python cli.py daytrade-backtest       # QuantFlow clone on 5m → docs/daytrade_backtest.md (already run once, failed)
```
