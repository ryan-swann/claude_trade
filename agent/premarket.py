"""Before the open: gather overnight news and filings for Claude's watchlist step."""
import json
from datetime import datetime, timedelta, timezone

from . import config, edgar
from .alpaca import Alpaca
from .risk import load_universe
from .trader import _today_et


def premarket(lookback_hours=18):
    api = Alpaca()
    universe = load_universe()
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    start = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    news = api.news(universe, start)
    res = api.data("/v1beta1/news", params={"start": start, "limit": 50, "sort": "desc"})
    news += res.get("news", [])
    seen, items = set(), []
    for n in news:
        if n.get("id") in seen:
            continue
        seen.add(n.get("id"))
        items.append({"time": n.get("created_at"), "symbols": n.get("symbols"), "headline": n.get("headline"),
                      "summary": n.get("summary"), "url": n.get("url")})
    try:
        filings = edgar.recent_filings(universe, since)
    except Exception as e:  # SEC hiccups should not block the day
        filings = {"error": str(e)}
    ctx = {"date": _today_et(), "generated_at": datetime.now(timezone.utc).isoformat(),
           "news": items, "filings": filings}
    config.PREMARKET_DIR.mkdir(exist_ok=True)
    path = config.PREMARKET_DIR / f"{_today_et()}.json"
    path.write_text(json.dumps(ctx, indent=2))
    print(f"Wrote {path}: {len(items)} news items, {sum(len(v) for v in filings.values() if isinstance(v, list))} filings")
