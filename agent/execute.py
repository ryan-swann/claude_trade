"""Validate Claude's decisions against the risk rules and place paper orders."""
import json
from datetime import datetime, timezone

from . import config
from .alpaca import Alpaca
from .risk import bracket_prices, check_buy, load_universe, trading_window_ok


def _journal(entry):
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(config.JOURNAL_DIR / f"{day}.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _buys_today(api, clock):
    today = datetime.fromisoformat(clock["timestamp"]).date().isoformat()
    orders = api.orders(status="all", after=f"{today}T00:00:00Z")
    return sum(1 for o in orders if o["side"] == "buy"
               and o.get("client_order_id", "").startswith(config.CLIENT_ORDER_PREFIX)
               and o["status"] not in ("canceled", "rejected", "expired"))


def execute(decisions_path, dry_run=False):
    decisions = json.loads(open(decisions_path).read())
    api = Alpaca()
    clock = api.clock()
    ok, why = trading_window_ok(clock)
    account = api.account()
    positions = api.positions()
    universe = set(load_universe())
    buys_today = _buys_today(api, clock)
    now = datetime.now(timezone.utc).isoformat()
    results = []

    for d in decisions:
        sym, action = d["symbol"].upper(), d["action"].lower()
        d["symbol"] = sym
        entry = {"time": now, **d, "dry_run": dry_run}
        if action == "hold":
            entry["result"] = "hold"
        elif not ok:
            entry["result"] = f"skipped: {why}"
        elif action == "sell":
            if sym not in {p["symbol"] for p in positions}:
                entry["result"] = "skipped: not held"
            elif dry_run:
                entry["result"] = "would close position"
            else:
                for o in api.orders(status="open"):
                    if o["symbol"] == sym:
                        api.cancel_order(o["id"])
                api.close_position(sym)
                entry["result"] = "closed position"
        elif action == "buy":
            try:
                tradable = bool(api.asset(sym).get("tradable"))
            except RuntimeError:
                tradable = False
            price = api.latest_trades([sym]).get(sym)
            qty, reason = check_buy(d, account=account, positions=positions, buys_today=buys_today,
                                    price=price, universe=universe, tradable=tradable)
            if not qty:
                entry["result"] = f"rejected: {reason}"
            else:
                tp, sl = bracket_prices(price)
                order = {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market",
                         "time_in_force": "day", "order_class": "bracket",
                         "take_profit": {"limit_price": str(tp)}, "stop_loss": {"stop_price": str(sl)},
                         "client_order_id": f"{config.CLIENT_ORDER_PREFIX}{sym}-{int(datetime.now().timestamp())}"}
                entry.update(qty=qty, price=price, take_profit=tp, stop_loss=sl)
                if dry_run:
                    entry["result"] = "would buy"
                else:
                    res = api.submit_order(**order)
                    entry["result"] = f"submitted {res.get('id')}"
                    positions.append({"symbol": sym})
                buys_today += 1
        else:
            entry["result"] = "skipped: unknown action"
        _journal(entry)
        results.append(entry)
        print(f"{sym:6} {action:5} -> {entry['result']}")
    return results


def summary():
    api = Alpaca()
    acct = api.account()
    positions = api.positions()
    eq, last = float(acct["equity"]), float(acct["last_equity"])
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# Paper account summary {day}", "",
             f"Equity ${eq:,.2f} ({(eq - last) / last:+.2%} today), cash ${float(acct['cash']):,.2f}", "",
             "| Symbol | Qty | Entry | Now | P/L |", "|---|---|---|---|---|"]
    for p in positions:
        lines.append(f"| {p['symbol']} | {p['qty']} | {float(p['avg_entry_price']):.2f} | "
                     f"{float(p['current_price']):.2f} | {float(p['unrealized_plpc']):+.2%} |")
    jf = config.JOURNAL_DIR / f"{day}.jsonl"
    if jf.exists():
        lines += ["", "## Decisions today", ""]
        for row in map(json.loads, jf.read_text().splitlines()):
            if row.get("action") != "hold":
                lines.append(f"- {row['symbol']} {row['action']}: {row['result']} ({row.get('reason', '')})")
    text = "\n".join(lines) + "\n"
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    (config.JOURNAL_DIR / f"{day}-summary.md").write_text(text)
    print(text)
