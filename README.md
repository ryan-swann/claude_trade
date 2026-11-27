# claude_trade

An aggressive **paper** day-trading system for Alpaca: quant code makes the trades, and Claude agents
supply news and filing sentiment and tune the strategy after every close. Flat every night.

## The pieces
| When (New York time) | Who | What |
|---|---|---|
| 8:35 AM | code | `premarket`: overnight news + SEC filings → `premarket/<date>.json` |
| 8:45 AM | Claude analyst (`PREMARKET.md`) | picks 5–20 catalyst names with a long/short bias → `watchlist.json` |
| continuously, 9:00–close | fast news path (`newswatch`) | polls Alpaca news every 20 s and the EDGAR 8-K feed every 60 s; Claude Haiku scores new catalyst items (`NEWS_PROMPT.md`); enters within minutes when price (+0.3%) and volume (3×) confirm; 1.5% stop, 3% target, 45-min time stop; also feeds `sentiment.json` |
| every 2 min, 9:00–3:58 | code (`tick`) | opening-range breakout + VWAP + volume on stocks in play; enters with bracket orders |
| ~3:48 PM onward | code | flattens every position |
| 4:10 PM | code | `review-data`: per-trade report `journal/<date>-trades.md` (catalyst, Claude's reason, signal, entry, exit reason, P/L) + `results.json` |
| 4:25 PM | Claude reviewer (`REVIEW.md`) | writes `journal/<date>-review.md`, tunes `params.json`, may improve the strategy |

Claude never places orders. The code trades a sentiment or watchlist name only when the price
breakout agrees with Claude's bias. It also trades the day's top movers on breakouts alone if the
move is big enough.

## Strategy (starting values in `params.json`)
- Long or short the break of the first 15 minutes' range, if price is on the right side of VWAP and
  volume runs at least 2× normal.
- Each trade is 20% of equity, with a 2% stop and a 4% target. Up to 5 positions and 12 entries a day.
- No new entries after 3:30 PM, and everything is closed from about 3:48 PM.

## Hard limits (in `agent/config.py`, not tunable)
- Paper endpoint only. The client refuses keys not starting with `PK` and accounts not starting with `PA`.
- Gross exposure at most 200% of equity. No new entries after a 4% down day, or below $25k equity (PDT rule).
- Tuning is clamped to `BOUNDS`, e.g. position size at most 30%, stop between 0.5% and 5%.

## Running on goku
```
cd ~/Projects && git clone https://github.com/ryan-swann/claude_trade && cd claude_trade
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q tests
(crontab -l; cat scripts/crontab.txt) | crontab -
```
Keys are read from `~/alpaca.env` (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, optional `SEC_USER_AGENT`).
Logs go to `logs/`. Stop everything: remove the claude_trade lines from `crontab -e`, then run
`scripts/goku_run.sh flatten`.

GitHub Actions workflows in `.github/workflows` are a manual backup runner (no schedules, so they
never double-trade with goku).
