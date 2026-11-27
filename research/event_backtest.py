"""Backtest scheduled "logistics" data releases against intraday ETF moves.

Events (all free, no key):
  crude  - EIA weekly crude inventories (Wed 10:30 ET)      -> USO, XLE, XOP
  gas    - EIA weekly natural gas storage (Thu 10:30 ET)    -> UNG, BOIL
  tsa    - TSA checkpoint throughput (daily, ~9 AM ET)      -> JETS, DAL, UAL

The surprise is a proxy, since consensus forecasts aren't free:
  crude/gas: this week's change minus the average change of the same calendar week over the prior 5 years.
  tsa: the 7-day YoY growth change vs the prior week.
What we test is the part we can actually capture: the move AFTER the first minute (10:31 -> 10:45 / 11:30),
since HFTs own the first seconds.

Run on goku: .venv/bin/python research/event_backtest.py  (needs pandas, xlrd, requests; Alpaca keys in env)
Writes research/results-<event>.json and prints a summary.
"""
import io
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.alpaca import Alpaca  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent
UA = {"User-Agent": "claude_trade research bot"}
YEARS = 6  # history to test


def eia_weekly(url):
    raw = requests.get(url, headers=UA, timeout=60).content
    df = pd.read_excel(io.BytesIO(raw), sheet_name="Data 1", skiprows=2)
    df.columns = ["date", "value"] + list(df.columns[2:])
    df = df[["date", "value"]].dropna()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["chg"] = df["value"].diff()
    df["week"] = df["date"].dt.isocalendar().week.astype(int)
    df["year"] = df["date"].dt.year
    norm = []
    for _, r in df.iterrows():
        prior = df[(df.week == r.week) & (df.year < r.year) & (df.year >= r.year - 5)]["chg"]
        norm.append(prior.mean() if len(prior) >= 3 else math.nan)
    df["surprise"] = df["chg"] - pd.Series(norm)
    return df.dropna()


def release_dates(df, weekday_offset):
    """Week-ending Friday -> release day (Wed = +5, Thu = +6). Holiday shifts are caught by the bar check."""
    return [(r.date + timedelta(days=weekday_offset)).date() for r in df.itertuples()]


def tsa_series():
    rows = []
    this_year = datetime.now().year
    for y in range(this_year - YEARS + 1, this_year + 1):
        url = "https://www.tsa.gov/travel/passenger-volumes" + ("" if y == this_year else f"/{y}")
        try:
            tables = pd.read_html(io.StringIO(requests.get(url, headers=UA, timeout=60).text))
        except Exception as e:
            print(f"TSA {y}: {e}")
            continue
        t = tables[0]
        t.columns = ["date", "pax"] + list(t.columns[2:])
        rows.append(t[["date", "pax"]])
        time.sleep(1)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["pax"] = pd.to_numeric(df["pax"].astype(str).str.replace(",", ""), errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date").set_index("date")
    s = df["pax"].rolling(7).sum()
    yoy = s / s.shift(364) - 1
    out = pd.DataFrame({"yoy": yoy, "surprise": yoy - yoy.shift(7)}).dropna()
    # Data for day D is published the morning of D+1.
    out["release"] = (out.index + timedelta(days=1)).date
    return out


def minute_bars(api, sym, day, start_hm, end_hm):
    s = datetime(day.year, day.month, day.day, *start_hm, tzinfo=ET).astimezone(timezone.utc)
    e = datetime(day.year, day.month, day.day, *end_hm, tzinfo=ET).astimezone(timezone.utc)
    for feed in ("sip", "iex"):
        try:
            res = api.data("/v2/stocks/bars", params={"symbols": sym, "timeframe": "1Min", "feed": feed,
                                                     "start": s.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                     "end": e.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 1000})
            bars = (res.get("bars") or {}).get(sym, [])
            if bars:
                return {datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M"): b
                        for b in bars}
        except RuntimeError:
            continue
    return {}


def px(bars, hm):
    """Close of the bar at hm, or the latest bar before it."""
    keys = sorted(k for k in bars if k <= hm)
    return bars[keys[-1]]["c"] if keys else None


def stats(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None and not math.isnan(x)]
    n = len(pairs)
    if n < 10:
        return {"n": n}
    xm = sum(p[0] for p in pairs) / n
    ym = sum(p[1] for p in pairs) / n
    cov = sum((x - xm) * (y - ym) for x, y in pairs)
    vx = sum((x - xm) ** 2 for x, _ in pairs)
    vy = sum((y - ym) ** 2 for _, y in pairs)
    corr = cov / math.sqrt(vx * vy) if vx and vy else 0
    t = corr * math.sqrt((n - 2) / max(1e-12, 1 - corr ** 2))
    return {"n": n, "corr": round(corr, 3), "t": round(t, 2)}


def rule(xs, ys, sign, q=0.3):
    """Trade only the biggest |surprise| tercile, in direction sign*surprise. Mean return and hit rate."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None and not math.isnan(x)]
    if len(pairs) < 10:
        return {}
    cut = sorted(abs(x) for x, _ in pairs)[int(len(pairs) * (1 - q))]
    rets = [(1 if sign * x > 0 else -1) * y for x, y in pairs if abs(x) >= cut]
    n = len(rets)
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / max(1, n - 1))
    return {"trades": n, "mean_bp": round(m * 1e4, 1), "hit": round(sum(r > 0 for r in rets) / n, 2),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd else None,
            "mean_bp_after_2bp_cost": round((m - 0.0002) * 1e4, 1)}


def run_event(api, name, events, symbols, t_react, horizons, sign):
    """events: [(date, surprise)], sign: expected direction of price for a positive surprise."""
    res = {"event": name, "symbols": {}}
    for sym in symbols:
        xs, outs = [], {h: [] for h in horizons}
        jump_xs, jumps = [], []
        for day, sur in events:
            bars = minute_bars(api, sym, day, (t_react[0], t_react[1] - 2), horizons[-1])
            pre = px(bars, f"{t_react[0]:02d}:{t_react[1] - 2:02d}")  # last bar fully before the release
            entry = px(bars, f"{t_react[0]:02d}:{t_react[1]:02d}")
            if not pre or not entry:
                continue
            xs.append(sur)
            jump_xs.append(sur)
            jumps.append(entry / pre - 1)  # the first-minute reaction (not capturable by us)
            for h in horizons:
                p = px(bars, f"{h[0]:02d}:{h[1]:02d}")
                outs[h].append(p / entry - 1 if p else None)
            time.sleep(0.05)
        r = {"first_minute": stats(jump_xs, jumps)}
        for h in horizons:
            key = f"after_to_{h[0]:02d}{h[1]:02d}"
            r[key] = {**stats(xs, outs[h]), "rule": rule(xs, outs[h], sign)}
        res["symbols"][sym] = r
        print(name, sym, json.dumps(r))
    (OUT / f"results-{name}.json").write_text(json.dumps(res, indent=2))
    return res


def main(which=("crude", "gas", "tsa")):
    api = Alpaca()
    cutoff = datetime.now().date() - timedelta(days=365 * YEARS)
    if "crude" in which:
        df = eia_weekly("https://www.eia.gov/dnav/pet/hist_xls/WCESTUS1w.xls")
        ev = [(d, s) for d, s in zip(release_dates(df, 5), df["surprise"]) if d >= cutoff]
        # A bigger-than-normal BUILD is bearish for oil, so sign = -1.
        run_event(api, "crude", ev, ["USO", "XLE", "XOP"], (10, 31), [(10, 45), (11, 30), (15, 55)], -1)
    if "gas" in which:
        df = eia_weekly("https://www.eia.gov/dnav/ng/hist_xls/NW2_EPG0_SWO_R48_BCFw.xls")
        ev = [(d, s) for d, s in zip(release_dates(df, 6), df["surprise"]) if d >= cutoff]
        run_event(api, "gas", ev, ["UNG", "BOIL"], (10, 31), [(10, 45), (11, 30), (15, 55)], -1)
    if "tsa" in which:
        t = tsa_series()
        ev = [(r.release, r.surprise) for r in t.itertuples() if r.release >= cutoff and r.release.weekday() < 5]
        # Data lands before the open: measure from 9:31 to later in the day.
        run_event(api, "tsa", ev, ["JETS", "DAL", "UAL"], (9, 31), [(10, 0), (12, 0), (15, 55)], 1)


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or ("crude", "gas", "tsa"))
