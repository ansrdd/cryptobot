# CryptoBot — paper-trading research bot

A long-only daily trend-following crypto bot (checks stops hourly) built to answer one question honestly: **does this method make money over time, after costs, with drawdowns a human could live through?**

It paper-trades on Alpaca, runs itself every hour on GitHub Actions for $0, and publishes a P&L dashboard to GitHub Pages. No real money is involved anywhere, and live mode is deliberately not implemented.

```
Data (Coinbase hourly → daily candles) → Signal (daily trend-following)
        → Risk layer (vol-targeted sizing, stops, circuit breakers) → Alpaca paper → Dashboard
```

---

## Setup (≈30 min, once)

You need to do the parts that involve your accounts. Everything else is automated.

### 1. Alpaca paper account (5 min)
1. Go to https://alpaca.markets and sign up (free).
2. In the dashboard, make sure the toggle at the top-left says **Paper** (not Live).
3. Click **View API Keys** → **Generate New Keys**. Copy both the **Key** and the **Secret** somewhere safe — the secret is shown once.
4. Paper accounts start with $100,000 fake dollars. That matches `starting_equity` in `config.yaml`. If yours differs, change the config.

### 2. GitHub repo (5 min)
1. Create a GitHub account if you don't have one.
2. Click **+** → **New repository**. Name it `cryptobot`. Set it to **Private**. Don't add a README (this folder already has one).
3. Push this folder to it. In Terminal, from inside the `cryptobot` folder:
   ```bash
   git init
   git add -A
   git commit -m "initial"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/cryptobot.git
   git push -u origin main
   ```

### 3. Secrets (2 min)
In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `ALPACA_API_KEY` | from step 1 |
| `ALPACA_SECRET_KEY` | from step 1 |
| `TELEGRAM_BOT_TOKEN` | optional, step 5 |
| `TELEGRAM_CHAT_ID` | optional, step 5 |

### 4. Turn on GitHub Pages + Actions (2 min)
1. **Settings → Pages → Source:** choose **GitHub Actions**.
2. **Settings → Actions → General → Workflow permissions:** choose **Read and write permissions**. Save.
3. Go to the **Actions** tab. If it asks you to enable workflows, click enable.

### 5. Telegram alerts (5 min)
1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts (pick any name; the username must end in `bot`). Copy the token it gives you → `TELEGRAM_BOT_TOKEN`.
2. Open a chat with your new bot and send it anything (say "hi"). It won't reply yet — that's fine.
3. In Terminal, inside the `cryptobot` folder: `python3 cli.py telegram`. Paste the token when asked. It prints your `TELEGRAM_CHAT_ID` and sends you a test message.
4. Add both as GitHub Secrets (step 3).

### 6. Verify Alpaca keys (1 min, optional)
`python3 cli.py alpaca` — paste the key and secret, it prints your paper account balance if they work.

---

## First run

1. **Actions tab → "Backtest (manual)" → Run workflow.** Takes ~5 min the first time (downloads 2019→now hourly history and builds daily candles from it). When it finishes, click the run and read the summary. The full report is at `docs/backtest.html` in the repo — open it, or view it on your Pages URL once that's live.
2. **Actions tab → "Paper trade (hourly)" → Run workflow** to kick off the first real cycle. After that it runs itself at :04 past every hour.
3. Your dashboard lives at **`https://YOUR_USERNAME.github.io/cryptobot/`**. Bookmark it. Backtest is at `/backtest.html` on the same site.

---

## What you'll see

**Dashboard (top → bottom):** total P&L after costs · balance · vs buy-and-hold · today/week/month · drawdown → equity curve → drawdown chart → performance stats → open positions → P&L by strategy / symbol / exit reason → trade log.

**Status pill** in the header: *Running* / *Paused for today* / *KILL SWITCH*. If it ever says kill switch, the bot has flattened and disabled itself; you'll have a Telegram message explaining why.

---

## Day-to-day

Nothing. Check the dashboard when you want. You'll get a Telegram message only when something matters.

**To change settings:** edit `config.yaml` on GitHub (pencil icon), commit. Next hourly run picks it up.

**To pause:** Actions → "Paper trade (hourly)" → ⋯ → Disable workflow. Open positions stay open at Alpaca — close them manually in Alpaca if you want to be flat.

**To re-arm after a kill switch:** edit `state/state.json`, set `"killed": false` and `"peak_equity"` to current equity, commit. Think about *why* it tripped first.

**To reset everything** (fresh start): delete `state/` folder in the repo, reset the paper account in Alpaca (Account → Reset), commit.

---

## Strategy history

All results: real Coinbase data, 2019→2026, after 0.25% fees + 0.05% slippage per side.

**v1: hourly trend + mean-reversion (retired).** −64% (kill switch off), Sharpe −2.2, 1,861 trades, lost money in 24 of 27 quarters. With costs set to zero it made +12%, so fees caused most of the loss, but even a zero-cost edge was only ~1.5%/yr. Mean-reversion had no edge at all (−1% at zero cost).

**v2: daily trend-following (current).** Settings committed *before* testing (commit `ac0ba83`): EMA 20/100, 20-day breakout, 3×ATR(20) trailing stop, 1% risk/trade. Result: **+5.8%, Sharpe 0.21, profit factor 1.23, max drawdown −7%, 74 trades.** The pre-set bar was Sharpe ≥ 0.5 and profit factor ≥ 1.3, so **v2 fails it**. It's paper trading anyway, as a learning exercise, not because it passed. Don't tune parameters until it clears the bar; that's how backtests start lying.

## Judging it

Set the bar before you look, so you don't move it after.

**Sample size warning:** v2 trades ~10 times a year. 90 days of paper is ~2–3 trades, which says almost nothing about profitability. Paper trading v2 mainly proves the execution works (orders fill, stops fire, alerts arrive). Judge the *strategy* on the backtest and on a year+ of paper, not on a few months.

Suggested bar after **12 months** of paper:

| Metric | Bar |
|---|---|
| Sharpe | > 1.0 |
| Max drawdown | < 15% |
| Profit factor | > 1.3 |
| vs buy-and-hold | ahead on a risk-adjusted basis (Calmar) |
| Walk-forward | beats B&H in most OOS windows, not just the good ones |

If it clears those, you've got something worth thinking about. If not, you learned that for free. **A handful of trades is noise. Don't read into the first one.**

Things that will fool you: a huge BTC rally (buy-and-hold will crush any risk-managed bot in a straight-up market — that's expected, look at drawdown instead), a single lucky trade, and Alpaca's perfect fills (we haircut them, but still).

---

## Local use (optional)

```bash
pip install -r requirements.txt
python cli.py backtest --synthetic   # offline smoke test, fake data
python cli.py fetch                  # download/refresh real history
python cli.py backtest               # real backtest → docs/backtest.html
cp .env.example .env                 # add keys
set -a; source .env; set +a
python cli.py run --dry              # one cycle, no orders sent
python cli.py run                    # one real paper cycle
python -m pytest -q tests            # tests
```

---

## Design notes (why it's built this way)

- **Risk layer has veto power.** Vol-targeted sizing (fixed % of equity at risk per trade), hard stops on every position, daily loss limit, max-drawdown kill switch, exposure caps, half-size after a losing streak. Drawdown kills bots, not direction.
- **One strategy, slow timeframe.** Daily trend-following only — see Strategy history for why v1's hourly trend + mean-reversion combo was cut.
- **Few parameters, no optimizer.** Deliberately. Every knob is a chance to overfit. Walk-forward shows consistency across windows instead of a single flattering number.
- **Pessimistic costs.** 25 bps fee + 5 bps slippage per side, applied even on paper.
- **Stateless runner.** Alpaca is the source of truth for positions. Every run reconciles. A skipped or doubled cron does nothing bad.
- **Long only, daily signals, paper only.** Each of those removes a whole class of ways to lose money while learning nothing. Revisit after it's proven.

Not financial advice. Paper trading. Build understanding, not expectations.
