"""Gather filings, news, account state and prices into one context file."""
import json
from datetime import datetime, timedelta, timezone

from . import config, edgar
from .alpaca import Alpaca
from .risk import load_universe


def collect(lookback_minutes=75):
    api = Alpaca()
    account = api.account()
    clock = api.clock()
    positions = api.positions()
    universe = load_universe()
    since = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    news = api.news(universe, since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    by_sym = {}
    for n in news:
        for s in n.get("symbols", []):
            if s in universe:
                by_sym.setdefault(s, {"news": [], "filings": []})["news"].append({
                    "time": n.get("created_at"), "headline": n.get("headline"),
                    "summary": n.get("summary"), "source": n.get("source"), "url": n.get("url"),
                })
    for s, fl in edgar.recent_filings(universe, since).items():
        by_sym.setdefault(s, {"news": [], "filings": []})["filings"] = fl
    for p in positions:
        by_sym.setdefault(p["symbol"], {"news": [], "filings": []})

    prices = api.latest_trades(sorted(by_sym)) if by_sym else {}
    for s, v in by_sym.items():
        v["price"] = prices.get(s)

    ctx = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_minutes": lookback_minutes,
        "clock": clock,
        "account": {k: account.get(k) for k in ("equity", "last_equity", "cash", "buying_power")},
        "positions": [{k: p.get(k) for k in ("symbol", "qty", "avg_entry_price", "current_price",
                                             "unrealized_plpc", "market_value")} for p in positions],
        "limits": {k: getattr(config, k) for k in dir(config) if k.isupper() and k.startswith(("MAX", "MIN", "STOP", "TAKE", "NO_"))},
        "symbols": by_sym,
    }
    run_dir = config.RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "context.json"
    path.write_text(json.dumps(ctx, indent=2))
    n_f = sum(len(v["filings"]) for v in by_sym.values())
    n_n = sum(len(v["news"]) for v in by_sym.values())
    print(f"Wrote {path}: {len(by_sym)} symbols, {n_f} filings, {n_n} news items, "
          f"{len(positions)} positions, market open={clock.get('is_open')}")
    return path
