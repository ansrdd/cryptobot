#!/usr/bin/env bash
# Hourly paper trading run on the always on server. cron calls this at :04 past every hour.
# Same steps as .github/workflows/trade.yml: pull, run the bot, commit state + dashboard, push.
# Keys live in .env next to this repo on the server (never committed).
set -euo pipefail
cd "$(dirname "$0")/.."

exec 9>/tmp/cryptobot.lock
flock -n 9 || { echo "$(date -u) previous run still going, skipping"; exit 0; }

echo "=== $(date -u +'%Y-%m-%d %H:%M UTC') ==="
git pull --rebase --autostash -q || echo "pull failed, running on local copy"

set -a; . ./.env; set +a
.venv/bin/python cli.py run

git add -A state docs
git diff --cached --quiet || git commit -q -m "run $(date -u +'%Y-%m-%d %H:%M UTC') (server)"
git pull --rebase --autostash -X theirs -q || true
git push -q
