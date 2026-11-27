#!/usr/bin/env bash
# Runs a Claude agent on goku. Usage: scripts/goku_agent.sh SENTIMENT.md|PREMARKET.md|REVIEW.md
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source "$HOME/alpaca.env"; set +a
mkdir -p logs
exec 8>/tmp/claude_trade_agent.lock
flock -n 8 || { echo "$(date -Is) agent still running, skipping $1" >> logs/agent.log; exit 0; }
git pull -q --rebase --autostash >> logs/runner.log 2>&1 || true
# Cheap model for the frequent sentiment runs; stronger model for the twice-daily analyst/reviewer.
MODEL="claude-sonnet-5-5"
if [ "$1" = "SENTIMENT.md" ]; then
  MODEL="claude-haiku-5-5"
  # Only wake Claude when there is something new to read.
  .venv/bin/python -m agent news --minutes 16 >> logs/agent.log 2>&1 || exit 0
  .venv/bin/python - <<'PY' || { echo "$(date -Is) no fresh news/filings, skipping sentiment" >> logs/agent.log; exit 0; }
import json, sys
d = json.load(open("runs/news-latest.json"))
f = d.get("filings") if isinstance(d.get("filings"), dict) else {}
sys.exit(0 if d.get("news") or any(isinstance(v, list) and v for v in f.values()) else 1)
PY
fi
TOOLS="Read,Write,Bash(.venv/bin/python -m agent:*),Bash(git add:*),Bash(git commit:*),Bash(git push:*),Bash(git log:*),Bash(git diff:*)"
[ "$1" = "REVIEW.md" ] && TOOLS="$TOOLS,Edit,Bash(.venv/bin/python -m pytest:*)"
{ echo "=== $(date -Is) $1"; claude -p "Today is $(TZ=America/New_York date +%F) (New York). $(cat "$1")" --model "$MODEL" --allowedTools "$TOOLS"; } >> logs/agent.log 2>&1
