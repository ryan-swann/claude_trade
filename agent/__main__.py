import argparse

p = argparse.ArgumentParser(prog="python -m agent")
sub = p.add_subparsers(dest="cmd", required=True)
t = sub.add_parser("tick", help="one intraday pass: flatten near close, else look for entries")
t.add_argument("--dry-run", action="store_true")
fl = sub.add_parser("flatten", help="cancel all orders and close all positions")
fl.add_argument("--dry-run", action="store_true")
n = sub.add_parser("news", help="fresh news and filings for the sentiment agent")
n.add_argument("--minutes", type=int, default=20)
w = sub.add_parser("newswatch", help="fast news path (long-running, market hours)")
w.add_argument("--dry-run", action="store_true")
sub.add_parser("premarket", help="collect overnight news and filings")
sub.add_parser("review-data", help="dump today's fills and P/L for the review")
a = p.parse_args()

if a.cmd == "tick":
    from .trader import tick
    tick(a.dry_run)
elif a.cmd == "flatten":
    from .alpaca import Alpaca
    from .trader import flatten
    flatten(Alpaca(), "manual flatten", a.dry_run)
elif a.cmd == "news":
    from .news import fresh
    fresh(a.minutes)
elif a.cmd == "newswatch":
    from datetime import datetime
    from .alpaca import Alpaca
    from .newswatch import Watcher
    from .trader import ET
    if not Alpaca().clock().get("is_open") and datetime.now(ET).hour >= 12:
        raise SystemExit("market closed")
    Watcher(a.dry_run).run()
elif a.cmd == "premarket":
    from .premarket import premarket
    premarket()
else:
    from .trader import review_data
    review_data()
