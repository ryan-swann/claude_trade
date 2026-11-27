import argparse

from .collect import collect
from .execute import execute, summary

p = argparse.ArgumentParser(prog="python -m agent")
sub = p.add_subparsers(dest="cmd", required=True)
c = sub.add_parser("collect", help="gather filings, news and account state")
c.add_argument("--lookback", type=int, default=75, help="minutes of history")
e = sub.add_parser("execute", help="apply decisions.json through risk checks")
e.add_argument("decisions")
e.add_argument("--dry-run", action="store_true")
sub.add_parser("summary", help="end-of-day account summary")
a = p.parse_args()
if a.cmd == "collect":
    collect(a.lookback)
elif a.cmd == "execute":
    execute(a.decisions, a.dry_run)
else:
    summary()
