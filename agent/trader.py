"""Intraday trading tick, end-of-day flatten, and the daily review data dump."""
import fcntl
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


class order_lock:
    """Serialises order placement between the 2-minute tick and the news watcher."""
    def __enter__(self):
        self.f = open("/tmp/claude_trade_orders.lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def enter(api, *, symbol, side, price, kind, signal_text, features=None, catalyst=None,
          stop_pct=None, tp_pct=None, dry_run=False, P=None):
    """Size, risk-check and submit one bracket entry, journaling the full reasoning.

    kind: "orb" (2-minute tick) or "news" (fast news path). Returns the journal entry.
    """
    P = P or config.P
    with order_lock():
        account = api.account()
        positions = api.positions()
        orders = _todays_agent_orders(api)
        entry = {"symbol": symbol, "action": side, "kind": kind, "signal": signal_text,
                 "features": features, "catalyst": catalyst, "dry_run": dry_run}
        if symbol in {p["symbol"] for p in positions} or symbol in {o["symbol"] for o in orders}:
            entry["result"] = "skipped: already traded today"
            return entry
        try:
            asset = api.asset(symbol)
        except RuntimeError:
            entry["result"] = "rejected: unknown asset"
            journal(entry)
            return entry
        entries_today = sum(1 for o in orders if o.get("order_class") == "bracket")
        qty, reason = size_entry(side, account=account, positions=positions, entries_today=entries_today,
                                 price=price, tradable=asset.get("tradable"),
                                 shortable=asset.get("shortable") and asset.get("easy_to_borrow"), P=P)
        if not qty:
            entry["result"] = f"rejected: {reason}"
            journal(entry)
            return entry
        pp = dict(P)
        if stop_pct:
            pp["stop_loss_pct"] = stop_pct
        if tp_pct:
            pp["take_profit_pct"] = tp_pct
        tp, sl = bracket_prices(price, side, pp)
        entry.update(qty=qty, price=price, take_profit=tp, stop_loss=sl)
        if dry_run:
            entry["result"] = "would enter"
        else:
            coid = f"{config.CLIENT_ORDER_PREFIX}{kind}-{symbol}-{int(datetime.now().timestamp())}"
            res = api.submit_order(symbol=symbol, qty=str(qty), side="buy" if side == "buy" else "sell",
                                   type="market", time_in_force="day", order_class="bracket",
                                   take_profit={"limit_price": str(tp)}, stop_loss={"stop_price": str(sl)},
                                   client_order_id=coid)
            entry.update(result="submitted", order_id=res.get("id"), client_order_id=coid)
        journal(entry)
        print(f"[{kind}] {symbol} {side} x{qty} @~{price:.2f} tp {tp} sl {sl}: {entry['result']}")
        return entry


def time_stops(api, dry_run=False):
    """Close fast-news trades that have not hit target or stop within news_max_hold_min."""
    P = config.P
    now = datetime.now(timezone.utc)
    held = {p["symbol"] for p in api.positions()}
    for o in _todays_agent_orders(api):
        coid = o.get("client_order_id", "")
        if o["symbol"] not in held or not coid.startswith(f"{config.CLIENT_ORDER_PREFIX}news-"):
            continue
        filled = o.get("filled_at")
        if not filled:
            continue
        age = (now - datetime.fromisoformat(filled.replace("Z", "+00:00"))).total_seconds() / 60
        if age >= min(max(int(P.get("news_max_hold_min", 45)), 5), 240):
            if not dry_run:
                for leg in o.get("legs") or []:
                    if leg["status"] in ("new", "accepted", "held", "pending_new"):
                        try:
                            api.cancel_order(leg["id"])
                        except RuntimeError:
                            pass
                api.close_position(o["symbol"])
            held.discard(o["symbol"])
            journal({"symbol": o["symbol"], "action": "exit", "kind": "news",
                     "result": f"time stop after {age:.0f} min", "dry_run": dry_run})


def _tick(api, clock, dry_run=False):
    P = config.P
    if not clock.get("is_open"):
        print("market closed")
        return
    if should_flatten(clock):
        flatten(api, dry_run=dry_run)
        return
    # Safety net: anything still open from a previous day gets closed first.
    todays = {o["symbol"] for o in _todays_agent_orders(api)}
    for p in [p for p in api.positions() if p["symbol"] not in todays]:
        if not dry_run:
            api.close_position(p["symbol"])
        journal({"symbol": p["symbol"], "action": "flatten", "result": "closed overnight leftover",
                 "dry_run": dry_run})
    time_stops(api, dry_run)
    ok, why = entries_allowed(clock, P)
    if not ok:
        print(f"no new entries: {why}")
        return

    held = {p["symbol"] for p in api.positions()}
    watch = merged_bias()
    cands = list(watch)
    for s in api.movers(20) + api.most_actives(20):
        if s not in cands:
            cands.append(s)
    cands = [s for s in cands if s not in held and s not in todays and s.isalpha()][:P["max_candidates"]]
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
        cat = watch.get(s)
        side, score, why = signal(f, P, (cat or {}).get("bias"))
        if side:
            scored.append((score, s, side, f, why, cat))
    scored.sort(key=lambda x: x[0], reverse=True)

    for score, s, side, f, why, cat in scored:
        e = enter(api, symbol=s, side=side, price=f["last"], kind="orb", signal_text=f"{why}, score {score:.1f}",
                  features={k: round(v, 4) for k, v in f.items()}, catalyst=cat, dry_run=dry_run, P=P)
        if str(e.get("result", "")).startswith("rejected: ") and e["result"][10:] in (
                "max positions reached", "daily entry limit reached", "daily loss limit hit"):
            break
    if not scored:
        print(f"{len(cands)} candidates, no signals")


def _exit_reason(order):
    """Classify how a bracket ended: target, stop, or closed by us."""
    for leg in order.get("legs") or []:
        if leg.get("status") == "filled":
            return ("target" if leg.get("type") == "limit" else "stop"), leg
    return "closed (time stop / flatten / manual)", None


def review_data():
    """After the close: per-trade reasoning report plus raw fills and P/L for Claude's review."""
    api = Alpaca()
    acct = api.account()
    start = datetime.now(ET).replace(hour=0, minute=0, second=0).astimezone(timezone.utc)
    after = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    fills = api.trade("GET", "/v2/account/activities/FILL", params={"after": after, "page_size": 100})
    orders = [o for o in _todays_agent_orders(api) if o.get("order_class") == "bracket"]
    jpath = config.JOURNAL_DIR / f"{_today_et()}.jsonl"
    jrows = [json.loads(l) for l in jpath.read_text().splitlines()] if jpath.exists() else []
    by_coid = {r.get("client_order_id"): r for r in jrows if r.get("client_order_id")}
    closes = {}
    for f in fills:
        closes.setdefault(f["symbol"], []).append(f)

    trades = []
    for o in sorted(orders, key=lambda o: o.get("filled_at") or ""):
        if not o.get("filled_at"):
            continue
        j = by_coid.get(o.get("client_order_id"), {})
        sym, qty = o["symbol"], float(o["filled_qty"])
        entry_px = float(o["filled_avg_price"])
        side = "long" if o["side"] == "buy" else "short"
        how, leg = _exit_reason(o)
        if leg:
            exit_px, exit_at = float(leg["filled_avg_price"]), leg["filled_at"]
        else:  # closed by a market order from flatten / time stop: use the last opposite-side fill
            opp = "sell" if side == "long" else "buy"
            fs = [f for f in closes.get(sym, []) if f["side"] in (opp, "sell_short" if opp == "sell" else opp)
                  and f["transaction_time"] > o["filled_at"]]
            exit_px = float(fs[-1]["price"]) if fs else None
            exit_at = fs[-1]["transaction_time"] if fs else None
        pl = None if exit_px is None else (exit_px - entry_px) * qty * (1 if side == "long" else -1)
        cat = j.get("catalyst") or {}
        trades.append({"symbol": sym, "side": side, "kind": j.get("kind"), "qty": qty,
                       "entry_at": o["filled_at"], "entry": entry_px, "exit_at": exit_at, "exit": exit_px,
                       "exit_reason": how, "pl": None if pl is None else round(pl, 2),
                       "signal": j.get("signal"), "catalyst_reason": cat.get("reason"),
                       "catalyst_source": cat.get("source"), "headline": cat.get("headline"),
                       "claude_score": cat.get("score") or cat.get("conviction")})

    eq, last = float(acct["equity"]), float(acct["last_equity"])
    out = {"date": _today_et(), "equity": eq, "last_equity": last, "day_pl": round(eq - last, 2),
           "day_pl_pct": (eq - last) / last if last else 0, "open_positions": len(api.positions()),
           "params": config.P, "trades": trades, "fills": fills}
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    (config.JOURNAL_DIR / f"{_today_et()}-results.json").write_text(json.dumps(out, indent=2))

    def t(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M") if ts else "?"
    lines = [f"# Trades {_today_et()}", "",
             f"Day P/L ${eq - last:,.2f} ({out['day_pl_pct']:+.2%}), equity ${eq:,.2f}, "
             f"{len(trades)} trades, open after close: {out['open_positions']}", ""]
    for x in trades:
        pl = "?" if x["pl"] is None else f"${x['pl']:+,.2f}"
        lines += [f"## {x['symbol']} {x['side']} ({x['kind']}) {pl}",
                  f"- Entry {t(x['entry_at'])} @ {x['entry']:.2f} x{x['qty']:g}; exit {t(x['exit_at'])} @ "
                  f"{x['exit'] if x['exit'] is not None else '?'} ({x['exit_reason']})",
                  f"- Signal: {x['signal']}"]
        if x["catalyst_reason"]:
            lines.append(f"- Claude ({x['claude_score']}): {x['catalyst_reason']}")
        if x["headline"] or x["catalyst_source"]:
            lines.append(f"- Source: {x['headline'] or ''} {x['catalyst_source'] or ''}".rstrip())
        lines.append("")
    (config.JOURNAL_DIR / f"{_today_et()}-trades.md").write_text("\n".join(lines))
    print(f"Wrote results and trades report: day P/L ${eq - last:,.2f}, {len(trades)} trades")
