"""Pure risk checks, kept separate so they are easy to test."""
import math
from datetime import datetime

from . import config


def load_universe():
    syms = []
    for line in config.UNIVERSE_FILE.read_text().splitlines():
        if not line.strip().startswith("#"):
            syms.extend(line.split())
    return syms


def minutes_to_close(clock):
    now = datetime.fromisoformat(clock["timestamp"])
    return (datetime.fromisoformat(clock["next_close"]) - now).total_seconds() / 60


def entries_allowed(clock, P=None):
    P = P or config.P
    if not clock.get("is_open"):
        return False, "market closed"
    mtc = minutes_to_close(clock)
    if mtc < config.NO_NEW_ENTRIES_BEFORE_CLOSE_MIN:
        return False, "past the entry cutoff"
    if mtc <= 390 and 390 - mtc < P["or_minutes"]:
        return False, "opening range still forming"
    return True, ""


def should_flatten(clock):
    return bool(clock.get("is_open")) and minutes_to_close(clock) <= config.FLATTEN_BEFORE_CLOSE_MIN


def size_entry(side, *, account, positions, entries_today, price, tradable, shortable, P=None):
    """Return (qty, reason). qty == 0 means rejected."""
    P = P or config.P
    equity = float(account["equity"])
    last_equity = float(account.get("last_equity") or equity)
    buying_power = float(account.get("buying_power") or account.get("cash") or 0)
    gross = sum(abs(float(p.get("market_value") or 0)) for p in positions)
    if side == "short" and not P.get("allow_shorts", True):
        return 0, "shorting disabled"
    if not tradable:
        return 0, "asset not tradable"
    if side == "short" and not shortable:
        return 0, "not shortable / not easy to borrow"
    if equity < config.PDT_MIN_EQUITY:
        return 0, "equity below $25k pattern-day-trader minimum"
    if len(positions) >= P["max_positions"]:
        return 0, "max positions reached"
    if entries_today >= P["max_entries_per_day"]:
        return 0, "daily entry limit reached"
    if last_equity and (equity - last_equity) / last_equity <= -config.MAX_DAILY_LOSS_PCT:
        return 0, "daily loss limit hit"
    if not price or price < P["min_price"]:
        return 0, "price missing or below minimum"
    budget = min(equity * P["position_pct"], equity * config.MAX_GROSS_EXPOSURE - gross, buying_power)
    qty = math.floor(budget / price)
    if qty < 1:
        return 0, "no room under exposure / buying power"
    return qty, "ok"


def bracket_prices(price, side, P=None):
    """Return (take_profit, stop_loss) for a long ("buy") or a "short"."""
    P = P or config.P
    tp, sl = P["take_profit_pct"], P["stop_loss_pct"]
    if side == "short":
        return round(price * (1 - tp), 2), round(price * (1 + sl), 2)
    return round(price * (1 + tp), 2), round(price * (1 - sl), 2)
