"""Fixed settings plus tunable parameters loaded (and clamped) from params.json.

The trading endpoint is deliberately NOT configurable.
"""
import json
from pathlib import Path

# Paper trading only. Never read this from the environment.
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "universe.txt"
PARAMS_FILE = ROOT / "params.json"
WATCHLIST_FILE = ROOT / "watchlist.json"
SENTIMENT_FILE = ROOT / "sentiment.json"
PREMARKET_DIR = ROOT / "premarket"
JOURNAL_DIR = ROOT / "journal"
RUNS_DIR = ROOT / "runs"
TZ = "America/New_York"

FILING_FORMS = {"8-K", "10-Q", "10-K", "4", "SC 13D", "SC 13D/A", "S-1", "S-3", "424B5", "6-K"}

# Hard limits that tuning can never exceed (min, max).
BOUNDS = {
    "or_minutes": (5, 60),
    "breakout_buffer_pct": (0.0, 0.01),
    "min_rvol": (1.0, 10.0),
    "min_move_pct": (0.0, 0.2),
    "stop_loss_pct": (0.005, 0.05),
    "take_profit_pct": (0.005, 0.15),
    "position_pct": (0.02, 0.30),
    "max_positions": (1, 8),
    "max_entries_per_day": (1, 25),
    "min_price": (2.0, 100.0),
    "bias_boost": (0.0, 0.5),
    "max_candidates": (5, 100),
}
MAX_GROSS_EXPOSURE = 2.0        # longs + shorts at most 200% of equity
MAX_DAILY_LOSS_PCT = 0.04       # no new entries once equity is down 4% on the day
PDT_MIN_EQUITY = 25000.0        # pattern day trader rule
NO_NEW_ENTRIES_BEFORE_CLOSE_MIN = 30   # no new entries after 3:30 PM ET
FLATTEN_BEFORE_CLOSE_MIN = 12          # close everything from ~3:48 PM ET
CLIENT_ORDER_PREFIX = "agent-"


def load_params(path=None):
    raw = json.loads(Path(path or PARAMS_FILE).read_text())
    p = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if k in BOUNDS:
            lo, hi = BOUNDS[k]
            v = type(lo)(min(max(v, lo), hi)) if isinstance(lo, int) else min(max(float(v), lo), hi)
        p[k] = v
    return p


P = load_params()
