# claude_trade

Claude reads new SEC filings (EDGAR) and Alpaca news for ~100 large-cap US stocks, decides
whether anything is a real catalyst, and places trades in an **Alpaca paper account only**.

## How a run works
1. `python3 -m agent collect` pulls the last 75 minutes of filings and news, account state and prices.
2. Claude reads it (`RUN.md`) and writes `decisions.json` with a reason for every call.
3. `python3 -m agent execute decisions.json` checks each decision against the risk rules and
   places bracket orders. Every decision lands in `journal/YYYY-MM-DD.jsonl`.
4. After the close, `python3 -m agent summary` writes `journal/YYYY-MM-DD-summary.md`.

## Strategy and limits
Long-only swing trades on material catalysts. Each buy is 5% of equity with a 15% take-profit and
an 8% stop. At most 10 positions and 4 new buys a day, 10% cash kept aside, no new buys after a 3%
down day, and no trading in the first or last 15 minutes of the session. Change them in
`agent/config.py`.

## Safety
The trading endpoint is hard-coded to `paper-api.alpaca.markets`. The client refuses keys that
don't start with `PK` and accounts whose number doesn't start with `PA`.

## Connector mode (no keys)
A scheduled cloud task with the Alpaca Paper Trading connector follows `RUN_CONNECTOR.md`.
The connector handles account, news and orders; `python3 -m agent check` applies the same risk
limits before Claude places anything.

## Key mode setup (on goku)
```
cp .env.example .env   # add paper keys
python3 -m pip install -r requirements.txt
./run.sh trade         # one manual run
crontab -e             # then add:
# CRON_TZ=America/New_York
# 5 10-15 * * 1-5 /home/rswann/Projects/claude_trade/run.sh trade
# 10 16 * * 1-5   /home/rswann/Projects/claude_trade/run.sh summary
```
