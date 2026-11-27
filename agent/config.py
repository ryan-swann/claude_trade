"""Fixed settings. The trading endpoint is deliberately NOT configurable."""
from pathlib import Path

# Paper trading only. Never read this from the environment.
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "universe.txt"
RUNS_DIR = ROOT / "runs"
JOURNAL_DIR = ROOT / "journal"

# Strategy: swing trades (days to a few weeks) on large caps, triggered by
# material SEC filings or news. Long only.
FILING_FORMS = {"8-K", "10-Q", "10-K", "4", "SC 13D", "SC 13D/A", "S-1", "6-K"}

# Risk limits
MAX_POSITION_PCT = 0.05        # each new position = 5% of equity
MAX_POSITIONS = 10
STOP_LOSS_PCT = 0.08           # bracket stop 8% below entry
TAKE_PROFIT_PCT = 0.15         # bracket target 15% above entry
MAX_NEW_BUYS_PER_DAY = 4
MIN_CONFIDENCE = 0.65          # Claude's stated confidence needed to buy
MIN_PRICE = 10.0
MIN_CASH_BUFFER_PCT = 0.10     # keep 10% of equity in cash
MAX_DAILY_LOSS_PCT = 0.03      # no new buys once equity is down 3% on the day
NO_TRADE_AFTER_OPEN_MIN = 15
NO_TRADE_BEFORE_CLOSE_MIN = 15

CLIENT_ORDER_PREFIX = "agent-"
