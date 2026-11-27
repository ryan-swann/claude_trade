# Pre-market watchlist (goku, 8:45 AM ET on weekdays)

1. Read `premarket/<today>.json` (today in New York time). If it is missing, write an
   empty watchlist for today and skip to step 4.
2. Find stocks with a fresh, material catalyst: earnings beats or misses, guidance changes, M&A,
   FDA decisions, big contracts, offerings (dilution means short), activist 13D filings, insider
   cluster buys, downgrades or upgrades with real substance. Skip rehashed or routine items.
3. Write `watchlist.json`:
   `{"date": "<today>", "symbols": {"TICKER": {"bias": "long"|"short", "conviction": 0.0-1.0, "reason": "...", "source": "url"}}}`
   Keep 5 to 20 names, plain US tickers only. The intraday code only trades a watchlist name if
   its breakout agrees with the bias.
4. The runner has already pulled `main`; don't pull yourself. Commit `watchlist.json` with the message `watchlist: <today>` and push to `main`.
