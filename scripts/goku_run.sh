#!/usr/bin/env bash
# Runner on goku. Usage: scripts/goku_run.sh tick|premarket|review-data
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source "$HOME/alpaca.env"; set +a
mkdir -p logs
exec 9>/tmp/claude_trade.lock
flock -n 9 || { echo "$(date -Is) previous run still going" >> logs/runner.log; exit 0; }
git pull -q --rebase --autostash >> logs/runner.log 2>&1 || true
.venv/bin/python -m agent "$@" >> "logs/$1.log" 2>&1
# Add each path separately: one missing path would make a combined `git add` add nothing.
for p in journal premarket sentiment.json watchlist.json; do [ -e "$p" ] && git add "$p"; done
# Commit the heartbeat with any journal change, or every 30 minutes.
if ! git diff --cached --quiet || [ $(( 10#$(date +%M) % 30 )) -lt 2 ]; then [ -e status.json ] && git add status.json; fi
git diff --cached --quiet || { git commit -qm "$1: $(TZ=America/New_York date +%F\ %H:%M)" && git push -q; } >> logs/runner.log 2>&1
