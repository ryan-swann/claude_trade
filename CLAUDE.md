# claude_trade

Paper day-trading system for Alpaca. See README.md for the pieces.
Hard rules:
- Paper only. Never edit `agent/alpaca.py` guards, the endpoints, `BOUNDS`, or the hard limits in `agent/config.py`.
- Claude agents never place orders. Only `python -m agent tick` / `flatten` trade.
- Never commit secrets (`~/alpaca.env`, `.env`).
- Tests: `.venv/bin/python -m pytest -q tests` (or `python3 -m pytest -q tests`).
