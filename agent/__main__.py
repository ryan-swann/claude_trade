import argparse

from .collect import collect
from .execute import execute, summary
from .check import check, filings

p = argparse.ArgumentParser(prog="python -m agent")
sub = p.add_subparsers(dest="cmd", required=True)
c = sub.add_parser("collect", help="gather filings, news and account state")
c.add_argument("--lookback", type=int, default=75, help="minutes of history")
e = sub.add_parser("execute", help="apply decisions.json through risk checks")
e.add_argument("decisions")
e.add_argument("--dry-run", action="store_true")
sub.add_parser("summary", help="end-of-day account summary")
k = sub.add_parser("check", help="key-free: risk-check decisions against connector state")
k.add_argument("state")
k.add_argument("decisions")
f = sub.add_parser("filings", help="key-free: recent SEC filings for the universe")
f.add_argument("--lookback", type=int, default=75)
a = p.parse_args()
if a.cmd == "collect":
    collect(a.lookback)
elif a.cmd == "execute":
    execute(a.decisions, a.dry_run)
elif a.cmd == "summary":
    summary()
elif a.cmd == "check":
    check(a.state, a.decisions)
else:
    filings(a.lookback)
