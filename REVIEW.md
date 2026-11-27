# After-close review and tuning (goku, 4:25 PM ET on weekdays)

You own this day-trading strategy and improve it a little every day. Paper money only.

1. Read `journal/<today>-trades.md` (per-trade reasoning), `journal/<today>-results.json`,
   `journal/<today>.jsonl`, `journal/<today>-news.jsonl` (every headline the fast news path scored,
   with latency), `params.json`,
   `watchlist.json`, `sentiment.json` history in git (`git log -p --since=today -- sentiment.json`), and the last 10 `journal/*-review.md` files.
2. If `open_positions` in the results is not 0, call that out at the top of the review.
3. Work out per-trade outcomes from the fills: win rate, average win and loss, P/L by side (long
   or short), by source (sentiment, watchlist, or movers only), how often your sentiment calls agreed with the price move, and how many entries were stopped out vs hit target.
   Compare the two entry paths (`kind`: orb vs news). For news calls, check how the stock moved
   after high-score calls that did NOT trade (failed confirmation) to tune `news_confirm_pct` and
   `news_vol_mult`, and how fast we were (headline `created_at` to entry).
   Also check signals that were rejected and whether they would have worked.
4. Write `journal/<today>-review.md`: day P/L, the stats, what worked, what didn't, and the change
   you are making with your reasoning.
5. Tune `params.json`. Change at most 2 parameters per day and by small steps, unless the last 5 days
   show a clear, consistent problem. Values outside `BOUNDS` in `agent/config.py` are clamped.
   You may also improve `agent/strategy.py` or the analyst prompts `NEWS_PROMPT.md` / `PREMARKET.md` (new filters or signals) when the data supports it, as
   long as `.venv/bin/python -m pytest -q tests` passes. Add a test for any new logic.
6. Never edit `agent/alpaca.py`, the endpoints, `BOUNDS`, or the hard limits in `agent/config.py`.
7. Commit with the message `review: <today>` and push to `main`.

The runner has already pulled `main` before starting you; don't run `git pull`.
