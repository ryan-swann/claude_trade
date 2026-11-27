#!/usr/bin/env bash
# Starts the fast news watcher if it isn't already running (cron calls this every minute).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source "$HOME/alpaca.env"; set +a
mkdir -p logs
exec 7>/tmp/claude_trade_newswatch.lock
flock -n 7 || exit 0
.venv/bin/python -m agent newswatch >> logs/newswatch.log 2>&1
