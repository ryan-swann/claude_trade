"""Key-free mode: risk-check decisions against state fetched by the Alpaca connector.

state.json (written by Claude from connector calls):
  {"clock": {"is_open", "timestamp", "next_close"},
   "account": {"equity", "last_equity", "cash"},
   "positions": [{"symbol": ...}], "buys_today": 0,
   "prices": {"AAPL": 201.5}, "tradable": {"AAPL": true}}
Prints the approved orders as JSON. Claude places exactly these, and nothing else.
"""
import json
from datetime import datetime, timedelta, timezone

from . import config, edgar
from .risk import bracket_prices, check_buy, load_universe, trading_window_ok
from .execute import _journal


def check(state_path, decisions_path):
    st = json.loads(open(state_path).read())
    decisions = json.loads(open(decisions_path).read())
    ok, why = trading_window_ok(st["clock"])
    universe = set(load_universe())
    positions = list(st.get("positions", []))
    held = {p["symbol"] for p in positions}
    buys = int(st.get("buys_today", 0))
    approved = []
    for d in decisions:
        sym, action = d["symbol"].upper(), d["action"].lower()
        d["symbol"] = sym
        entry = {"time": datetime.now(timezone.utc).isoformat(), **d}
        if action == "hold":
            entry["result"] = "hold"
        elif not ok:
            entry["result"] = f"skipped: {why}"
        elif action == "sell":
            if sym in held:
                approved.append({"symbol": sym, "action": "close_position"})
                entry["result"] = "approved: close position"
            else:
                entry["result"] = "skipped: not held"
        elif action == "buy":
            price = st.get("prices", {}).get(sym)
            qty, reason = check_buy(d, account=st["account"], positions=positions, buys_today=buys,
                                    price=price, universe=universe,
                                    tradable=st.get("tradable", {}).get(sym, False))
            if qty:
                tp, sl = bracket_prices(price)
                approved.append({"symbol": sym, "action": "buy", "qty": qty, "side": "buy", "type": "market",
                                 "time_in_force": "day", "order_class": "bracket",
                                 "take_profit_limit_price": tp, "stop_loss_stop_price": sl})
                positions.append({"symbol": sym})
                held.add(sym)
                buys += 1
                entry.update(result="approved: buy", qty=qty, price=price, take_profit=tp, stop_loss=sl)
            else:
                entry["result"] = f"rejected: {reason}"
        else:
            entry["result"] = "skipped: unknown action"
        _journal(entry)
    print(json.dumps(approved, indent=2))
    return approved


def filings(lookback_minutes=75):
    since = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    out = edgar.recent_filings(load_universe(), since)
    print(json.dumps(out, indent=2))
    return out
