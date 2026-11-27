#!/usr/bin/env bash
# Usage: ./run.sh trade | ./run.sh summary   (called from cron on market days)
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
mkdir -p logs
if [ "${1:-trade}" = "summary" ]; then
  python3 -m agent summary >> logs/summary.log 2>&1
  git add journal && git commit -qm "summary: $(date +%F)" && git push -q || true
else
  claude -p "$(cat RUN.md)" \
    --allowedTools "Read,Write,Bash(python3 -m agent:*),Bash(git add:*),Bash(git commit:*),Bash(git push:*)" \
    >> logs/trade.log 2>&1
fi
