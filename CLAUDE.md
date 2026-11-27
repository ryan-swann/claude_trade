# claude_trade

Paper trading agent for Alpaca. Hard rules:
- Paper only. `PAPER_BASE_URL` in `agent/config.py` is fixed; the client refuses non-PK keys and non-PA accounts. Do not weaken these checks.
- Orders only go through `python3 -m agent execute`, which applies the risk limits in `agent/config.py`.
- Never commit `.env`.
- Tests: `python3 -m pytest -q tests`.
