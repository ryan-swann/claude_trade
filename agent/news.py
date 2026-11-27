"""Fresh news + filings for the intraday sentiment agent."""
import json
from datetime import datetime, timedelta, timezone

from . import config, edgar
from .alpaca import Alpaca
from .risk import load_universe


def fresh(minutes=20):
    api = Alpaca()
    universe = load_universe()
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    start = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = api.news(universe, start)
    raw += api.data("/v1beta1/news", params={"start": start, "limit": 50, "sort": "desc"}).get("news", [])
    seen, items = set(), []
    for n in raw:
        if n.get("id") in seen:
            continue
        seen.add(n.get("id"))
        items.append({"time": n.get("created_at"), "symbols": n.get("symbols"), "headline": n.get("headline"),
                      "summary": n.get("summary"), "url": n.get("url")})
    try:
        filings = edgar.recent_filings(universe, since)
    except Exception as e:
        filings = {"error": str(e)}
    try:
        movers = api.movers(20)
    except RuntimeError:
        movers = []
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "minutes": minutes,
           "news": items, "filings": filings, "movers": movers}
    config.RUNS_DIR.mkdir(exist_ok=True)
    path = config.RUNS_DIR / "news-latest.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {path}: {len(items)} news, "
          f"{sum(len(v) for v in filings.values() if isinstance(v, list))} filings")
