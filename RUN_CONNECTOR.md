# Trading run, connector mode (scheduled cloud task, no API keys)

You are the decision step of a PAPER trading agent. Use only the Alpaca Paper Trading connector.
Never change `agent/config.py`. Place only the orders that `python3 -m agent check` approves.

1. With the connector, get the market clock. If the market is closed, stop.
2. Get account info, open positions, and today's orders. Count today's buy orders (not canceled/rejected) as `buys_today`.
3. Get news from the last 75 minutes for the symbols in `universe.txt`.
4. Get SEC filings: `python3 -m agent filings`. If sec.gov is unreachable, fetch
   `https://data.sec.gov/submissions/CIK##########.json` with WebFetch for symbols in the news
   and for held positions, and note in the journal that filing coverage was partial.
5. Decide buy / sell / hold per symbol using the rules in `RUN.md` step 3. Be skeptical; most runs make no trades.
6. For each candidate buy, get its latest price and whether the asset is tradable.
7. Write `runs/<time>/state.json` (format at the top of `agent/check.py`) and `runs/<time>/decisions.json`.
8. Run `python3 -m agent check runs/<time>/state.json runs/<time>/decisions.json`.
9. Place exactly the approved orders with the connector: bracket market buys with the given qty,
   take-profit and stop-loss; for `close_position`, cancel that symbol's open orders, then close the position.
10. Commit `journal/` and push to `main`.
