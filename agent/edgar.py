"""Recent SEC filings via EDGAR's public JSON APIs (no key needed)."""
import html
import os
import re
import time
from datetime import datetime, timezone

import requests

from . import config

UA = os.environ.get("SEC_USER_AGENT", "alpaca-paper-agent research-bot")
_s = requests.Session()
_s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
_last = [0.0]


def _get(url):
    # SEC asks for at most 10 requests/second.
    wait = 0.12 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    r = _s.get(url, timeout=30)
    r.raise_for_status()
    return r


def ticker_to_cik():
    data = _get("https://www.sec.gov/files/company_tickers.json").json()
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def recent_filings(symbols, since: datetime):
    """Return {symbol: [filing,...]} for filings accepted after `since` (UTC)."""
    ciks = ticker_to_cik()
    out = {}
    for sym in symbols:
        cik = ciks.get(sym.replace(".", "-"))
        if not cik:
            continue
        try:
            sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        except requests.RequestException:
            continue
        rec = sub.get("filings", {}).get("recent", {})
        rows = zip(rec.get("form", []), rec.get("acceptanceDateTime", []), rec.get("accessionNumber", []),
                   rec.get("primaryDocument", []), rec.get("items", [""] * len(rec.get("form", []))))
        for form, accepted, acc, doc, items in rows:
            if form not in config.FILING_FORMS:
                continue
            ts = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < since:
                break  # list is newest first
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            out.setdefault(sym, []).append({
                "form": form, "accepted": ts.isoformat(), "items": items, "url": url,
                "excerpt": excerpt(url),
            })
    return out


def excerpt(url, limit=6000):
    try:
        text = _get(url).text
    except requests.RequestException:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit]
