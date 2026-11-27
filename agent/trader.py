"""Intraday trading tick, end-of-day flatten, and the daily review data dump."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .alpaca import Alpaca
from .risk import bracket_prices, entries_allowed, load_universe, should_flatten, size_entry
from .strategy import features, signal

ET = ZoneInfo(config.TZ)


def _today_et():
    return datetime.now(ET).date().isoformat()


def journal(entry):
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    with open(config.JOURNAL_DIR / f"{_today_et()}.jsonl", "a") as f:
        f.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **entry}) + "\n")


def load_watchlist():
    try:
        wl = json.loads(config.WATCHLIST_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return wl.get("symbols", {}) if wl.get("date") == _today_et() else {}


def load_sentiment(now=None):
    """Intraday sentiment from Claude: {sym: {bias, score, reason, expires}}; expired entries dropped."""
    now = now or datetime.now(timezone.utc)
    try:
        data = json.loads(config.SENTIMENT_FILE.read_text()).get("symbols", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for s, v in data.items():
        try:
            if datetime.fromisoformat(v["expires"].replace("Z", "+00:00")) > now and v.get("bias") in ("long", "short"):
                out[s] = v
        except (KeyError, ValueError):
            continue
    return out


def merged_bias():
    """Fresh intraday sentiment overrides the pre-market watchlist."""
    bias = {s: v for s, v in load_watchlist().items() if v.get("bias") in ("long", "short")}
    bias.update(load_sentiment())
    return bias


def _todays_agent_orders(api):
    start = datetime.now(ET).replace(hour=0, minute=0, second=0).astimezone(timezone.utc)
    return [o for o in api.orders(status="all", after=start.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if o.get("client_order_id", "").startswith(config.CLIENT_ORDER_PREFIX)
            and o["status"] not in ("canceled", "rejected", "expired")]


def flatten(api, reason="end-of-day flatten", dry_run=False):
    positions = api.positions()
    if not dry_run:
        api.trade("DELETE", "/v2/orders")
        if positions:
            api.trade("DELETE", "/v2/positions", params={"cancel_orders": "true"})
    for p in positions:
        journal({"symbol": p["symbol"], "action": "flatten", "qty": p["qty"], "side": p["side"],
                 "unrealized_pl": p.get("unrealized_pl"), "result": reason, "dry_run": dry_run})
    print(f"flattened {len(positions)} positions ({reason})")


def write_status(api, clock, note=""):
    """Heartbeat for the reporting tasks: account, positions, last tick time."""
    try:
        acct, positions = api.account(), api.positions()
        eq, last = float(acct["equity"]), float(acct["last_equity"])
        st = {"last_tick": datetime.now(timezone.utc).isoformat(), "market_open": clock.get("is_open"),
              "equity": eq, "day_pl": round(eq - last, 2), "day_pl_pct": round((eq - last) / last, 4) if last else 0,
              "positions": [{k: p.get(k) for k in ("symbol", "side", "qty", "avg_entry_price", "unrealized_pl")}
                            for p in positions], "note": note}
        (config.ROOT / "status.json").write_text(json.dumps(st, indent=2))
    except Exception as e:  # never let the heartbeat break trading
        print(f"status write failed: {e}")


def tick(dry_run=False):
    api = Alpaca()
    clock = api.clock()
    try:
        _tick(api, clock, dry_run)
    finally:
        write_status(api, clock)


def _tick(api, clock, dry_run=False):
    P = config.P
    if not clock.get("is_open"):
        print("market closed")
        return
    if should_flatten(clock):
        flatten(api, dry_run=dry_run)
        return
    # Safety net: anything still open from a previous day gets closed first.
    stale = [p for p in api.positions()
             if p["symbol"] not in {o["symbol"] for o in _todays_agent_orders(api)}]
    if stale:
        for p in stale:
            if not dry_run:
                api.close_position(p["symbol"])
            journal({"symbol": p["symbol"], "action": "flatten", "result": "closed overnight leftover",
                     "dry_run": dry_run})
    ok, why = entries_allowed(clock, P)
    if not ok:
        print(f"no new entries: {why}")
        return

    account = api.account()
    positions = api.positions()
    held = {p["symbol"] for p in positions}
    orders = _todays_agent_orders(api)
    traded_today = {o["symbol"] for o in orders}
    entries_today = sum(1 for o in orders if o.get("order_class") == "bracket")
    watch = merged_bias()

    # Stocks in play: Claude's sentiment + watchlist names first, then market movers and most actives.
    cands = list(watch)
    for s in api.movers(20) + api.most_actives(20):
        if s not in cands:
            cands.append(s)
    cands = [s for s in cands if s not in held and s not in traded_today and s.isalpha()][:P["max_candidates"]]
    if not cands:
        print("no candidates")
        return

    now = datetime.now(ET)
    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes = (now - open_dt).total_seconds() / 60
    intraday = api.bars(cands, "5Min", open_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    daily = api.bars(cands, "1Day", (now - timedelta(days=40)).strftime("%Y-%m-%d"),
                     end=(now - timedelta(days=1)).strftime("%Y-%m-%d"))

    scored = []
    for s in cands:
        d = daily.get(s, [])[-20:]
        adv = sum(b["v"] for b in d) / len(d) if d else 0
        f = features(intraday.get(s, []), adv, minutes, P["or_minutes"])
        bias = (watch.get(s) or {}).get("bias")
        side, score, why = signal(f, P, bias)
        if side:
            scored.append((score, s, side, f, why))
    scored.sort(reverse=True)

    for score, s, side, f, why in scored:
        try:
            asset = api.asset(s)
        except RuntimeError:
            continue
        qty, reason = size_entry(side, account=account, positions=positions, entries_today=entries_today,
                                 price=f["last"], tradable=asset.get("tradable"),
                                 shortable=asset.get("shortable") and asset.get("easy_to_borrow"), P=P)
        entry = {"symbol": s, "action": side, "signal": why, "features": f, "score": round(score, 2),
                 "dry_run": dry_run}
        if not qty:
            entry["result"] = f"rejected: {reason}"
            journal(entry)
            if reason in ("max positions reached", "daily entry limit reached", "daily loss limit hit"):
                break
            continue
        tp, sl = bracket_prices(f["last"], side, P)
        entry.update(qty=qty, price=f["last"], take_profit=tp, stop_loss=sl)
        if dry_run:
            entry["result"] = "would enter"
        else:
            res = api.submit_order(symbol=s, qty=str(qty), side="buy" if side == "buy" else "sell",
                                   type="market", time_in_force="day", order_class="bracket",
                                   take_profit={"limit_price": str(tp)}, stop_loss={"stop_price": str(sl)},
                                   client_order_id=f"{config.CLIENT_ORDER_PREFIX}{s}-{int(now.timestamp())}")
            entry["result"] = f"submitted {res.get('id')}"
        journal(entry)
        print(f"{s} {side} x{qty} @~{f['last']:.2f} tp {tp} sl {sl}: {entry['result']}")
        positions.append({"symbol": s, "market_value": qty * f["last"]})
        entries_today += 1
    if not scored:
        print(f"{len(cands)} candidates, no signals")


def review_data():
    """After the close: dump today's fills and account result for Claude's review."""
    api = Alpaca()
    acct = api.account()
    start = datetime.now(ET).replace(hour=0, minute=0, second=0).astimezone(timezone.utc)
    fills = api.trade("GET", "/v2/account/activities/FILL",
                      params={"after": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "page_size": 100})
    eq, last = float(acct["equity"]), float(acct["last_equity"])
    out = {"date": _today_et(), "equity": eq, "last_equity": last, "day_pl": eq - last,
           "day_pl_pct": (eq - last) / last if last else 0, "open_positions": len(api.positions()),
           "params": config.P, "fills": fills}
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    path = config.JOURNAL_DIR / f"{_today_et()}-results.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {path}: day P/L ${eq - last:,.2f} ({out['day_pl_pct']:+.2%}), {len(fills)} fills")
