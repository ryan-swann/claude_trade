"""Pure risk checks, kept separate so they are easy to test."""
import math
from datetime import datetime

from . import config


def load_universe():
    syms = []
    for line in config.UNIVERSE_FILE.read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        syms.extend(line.split())
    return syms


def trading_window_ok(clock):
    """Market open and not too close to the open or the close."""
    if not clock.get("is_open"):
        return False, "market closed"
    now = datetime.fromisoformat(clock["timestamp"])
    nxt_close = datetime.fromisoformat(clock["next_close"])
    mins_to_close = (nxt_close - now).total_seconds() / 60
    if mins_to_close < config.NO_TRADE_BEFORE_CLOSE_MIN:
        return False, "too close to the close"
    # Regular session is 390 minutes; minutes since open follows from the close.
    if 390 - mins_to_close < config.NO_TRADE_AFTER_OPEN_MIN and mins_to_close <= 390:
        return False, "too soon after the open"
    return True, ""


def check_buy(decision, *, account, positions, buys_today, price, universe, tradable):
    """Return (qty, reason). qty == 0 means rejected, reason says why."""
    sym = decision["symbol"]
    held = {p["symbol"] for p in positions}
    equity = float(account["equity"])
    last_equity = float(account.get("last_equity") or equity)
    cash = float(account["cash"])

    if sym not in universe:
        return 0, "not in universe"
    if not tradable:
        return 0, "asset not tradable"
    if sym in held:
        return 0, "already held"
    if float(decision.get("confidence", 0)) < config.MIN_CONFIDENCE:
        return 0, f"confidence below {config.MIN_CONFIDENCE}"
    if len(held) >= config.MAX_POSITIONS:
        return 0, "max positions reached"
    if buys_today >= config.MAX_NEW_BUYS_PER_DAY:
        return 0, "daily buy limit reached"
    if last_equity and (equity - last_equity) / last_equity <= -config.MAX_DAILY_LOSS_PCT:
        return 0, "daily loss limit hit"
    if not price or price < config.MIN_PRICE:
        return 0, "price missing or below minimum"
    budget = equity * config.MAX_POSITION_PCT
    spendable = cash - equity * config.MIN_CASH_BUFFER_PCT
    budget = min(budget, spendable)
    qty = math.floor(budget / price)
    if qty < 1:
        return 0, "not enough cash after buffer"
    return qty, "ok"


def bracket_prices(price):
    return (round(price * (1 + config.TAKE_PROFIT_PCT), 2),
            round(price * (1 - config.STOP_LOSS_PCT), 2))
