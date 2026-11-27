#!/usr/bin/env bash
# Runs a Claude agent on goku. Usage: scripts/goku_agent.sh SENTIMENT.md|PREMARKET.md|REVIEW.md
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source "$HOME/alpaca.env"; set +a
mkdir -p logs
exec 8>/tmp/claude_trade_agent.lock
flock -n 8 || { echo "$(date -Is) agent still running, skipping $1" >> logs/agent.log; exit 0; }
git pull -q --rebase --autostash >> logs/runner.log 2>&1 || true
TOOLS="Read,Write,Bash(.venv/bin/python -m agent:*),Bash(git add:*),Bash(git commit:*),Bash(git push:*),Bash(git log:*),Bash(git diff:*)"
[ "$1" = "REVIEW.md" ] && TOOLS="$TOOLS,Edit,Bash(.venv/bin/python -m pytest:*)"
{ echo "=== $(date -Is) $1"; claude -p "Today is $(TZ=America/New_York date +%F) (New York). $(cat "$1")" --allowedTools "$TOOLS"; } >> logs/agent.log 2>&1
