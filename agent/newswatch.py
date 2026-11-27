"""Fast news path: poll Alpaca news + the EDGAR 8-K feed, score new items with Claude Haiku,
and enter quickly when price and volume confirm. Runs on goku as a long-lived process in market hours.

Claude only scores news. Orders go through trader.enter(), which applies the same risk checks as the tick.
"""
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET_XML
from datetime import datetime, timedelta, timezone

from . import config, edgar
from .alpaca import Alpaca
from .risk import load_universe, minutes_to_close
from .trader import ET, _today_et, enter, load_watchlist

NEWS_PROMPT_FILE = config.ROOT / "NEWS_PROMPT.md"
CATALYST = re.compile(
    r"earnings|results|guidance|outlook|forecast|beats?|miss(es)?|raises?|cuts?|lowers?|acquir|merger|"
    r"buyout|takeover|to buy|deal|offering|dilut|fda|approv|reject|trial|phase|contract|award|"
    r"upgrade|downgrade|price target|initiat|resign|steps down|ceo|cfo|bankrupt|chapter 11|recall|"
    r"lawsuit|probe|investigat|sec charges|buyback|repurchase|dividend|halt|delist|restat",
    re.I)
POLL_SEC = 20
EDGAR_EVERY_SEC = 60


def news_params(P):
    """News-path settings from params.json. Stops/targets are held to the same BOUNDS as the tick."""
    def clamp(v, lo, hi):
        return min(max(float(v), lo), hi)
    sl_lo, sl_hi = config.BOUNDS["stop_loss_pct"]
    tp_lo, tp_hi = config.BOUNDS["take_profit_pct"]
    return {
        "min_score": clamp(P.get("news_min_score", 0.7), 0.5, 0.95),
        "confirm_pct": clamp(P.get("news_confirm_pct", 0.003), 0.0, 0.02),
        "vol_mult": clamp(P.get("news_vol_mult", 3.0), 1.0, 20.0),
        "stop_pct": clamp(P.get("news_stop_pct", 0.015), sl_lo, sl_hi),
        "tp_pct": clamp(P.get("news_tp_pct", 0.03), tp_lo, tp_hi),
        "max_age_min": clamp(P.get("news_max_age_min", 10), 1, 60),
        "max_llm_calls": int(clamp(P.get("news_max_llm_calls", 300), 0, 600)),
    }


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(row):
    config.JOURNAL_DIR.mkdir(exist_ok=True)
    with open(config.JOURNAL_DIR / f"{_today_et()}-news.jsonl", "a") as f:
        f.write(json.dumps({"seen_at": _now().isoformat(), **row}) + "\n")


def parse_calls(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        calls = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [c for c in calls if isinstance(c, dict) and c.get("bias") in ("long", "short")]


def ask_claude(items, model="claude-haiku-5-5", timeout=90):
    """One cheap Claude call for a batch of new items."""
    prompt = json.dumps(items, separators=(",", ":"))
    try:
        out = subprocess.run(["claude", "-p", prompt, "--model", model,
                              "--system-prompt", NEWS_PROMPT_FILE.read_text(), "--output-format", "text"],
                             capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"claude call failed: {e}")
        return []
    calls = parse_calls(out.stdout)
    if not calls and "[" not in out.stdout:
        print(f"claude returned no JSON: {out.stdout[:200]!r} {out.stderr[:200]!r}")
    return calls


def update_sentiment(call, item):
    """Share fast calls with the 2-minute tick (it reads sentiment.json as a bias)."""
    path = config.SENTIMENT_FILE
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    now = _now()
    syms = {s: v for s, v in data.get("symbols", {}).items() if v.get("expires", "") > now.isoformat()}
    syms[call["symbol"]] = {"bias": call["bias"], "score": call.get("score"), "reason": call.get("reason"),
                            "source": item.get("url"), "headline": item.get("headline"), "via": "news-fast",
                            "expires": (now + timedelta(minutes=90)).isoformat()}
    path.write_text(json.dumps({"updated": now.isoformat(), "symbols": syms}, indent=2))


class Watcher:
    def __init__(self, dry_run=False):
        self.api = Alpaca()
        self.dry_run = dry_run
        self.P = config.P
        self.N = news_params(self.P)
        self.seen, self.edgar_seen = set(), set()
        self.last_news = _now() - timedelta(minutes=3)
        self.last_edgar = 0.0
        self.calls_today = 0
        self.pending = []
        self.universe = set(load_universe())
        try:
            self.cik2tic = {v: k for k, v in edgar.ticker_to_cik().items()}
        except Exception as e:
            print(f"EDGAR ticker map failed: {e}")
            self.cik2tic = {}

    # ---- sources
    def poll_news(self):
        res = self.api.data("/v1beta1/news", params={"start": _iso(self.last_news), "limit": 50, "sort": "asc"})
        watch = set(load_watchlist())
        items = []
        for n in res.get("news", []):
            if n["id"] in self.seen:
                continue
            self.seen.add(n["id"])
            created = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00"))
            self.last_news = max(self.last_news, created)
            syms = [s for s in n.get("symbols", []) if s.isalpha() and len(s) <= 5]
            text = f"{n.get('headline', '')} {n.get('summary', '')}"
            if not syms or not (CATALYST.search(text) or self.universe.intersection(syms) or watch.intersection(syms)):
                continue
            items.append({"id": f"n{n['id']}", "symbols": syms[:4], "headline": n.get("headline"),
                          "summary": (n.get("summary") or "")[:400], "created_at": n.get("created_at"),
                          "url": n.get("url"), "src": "alpaca-news"})
        return items

    def poll_edgar(self):
        if time.time() - self.last_edgar < EDGAR_EVERY_SEC:
            return []
        self.last_edgar = time.time()
        try:
            xml = edgar._get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
                             "&company=&dateb=&owner=include&start=0&count=40&output=atom").text
        except Exception as e:
            print(f"EDGAR poll failed: {e}")
            return []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = []
        for e in ET_XML.fromstring(xml).findall("a:entry", ns):
            eid = e.findtext("a:id", default="", namespaces=ns)
            title = e.findtext("a:title", default="", namespaces=ns)
            if eid in self.edgar_seen or not title.startswith("8-K"):
                continue
            self.edgar_seen.add(eid)
            m = re.search(r"\((\d{10})\)", title)
            tic = self.cik2tic.get(int(m.group(1))) if m else None
            if not tic or not tic.isalpha():
                continue
            summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", e.findtext("a:summary", default="", namespaces=ns)))
            link = e.find("a:link", ns)
            url = link.get("href") if link is not None else ""
            excerpt = self._filing_excerpt(url) if re.search(r"\b(1\.01|2\.02|5\.02|7\.01|8\.01)\b", summary) else ""
            items.append({"id": f"e{eid[-24:]}", "symbols": [tic], "headline": title,
                          "summary": f"{summary} {excerpt}"[:1500],
                          "created_at": e.findtext("a:updated", default="", namespaces=ns),
                          "url": url, "src": "edgar-8k"})
        return items

    def _filing_excerpt(self, index_url):
        try:
            base = index_url.rsplit("/", 1)[0]
            idx = edgar._get(base + "/index.json").json()
            docs = [f["name"] for f in idx["directory"]["item"]
                    if f["name"].endswith((".htm", ".html")) and "index" not in f["name"]]
            ex = [d for d in docs if "ex99" in d.lower()]  # press-release exhibit first
            return edgar.excerpt(f"{base}/{(ex or docs)[0]}", limit=1200) if docs else ""
        except Exception:
            return ""

    # ---- trading
    def confirm(self, sym, bias, t0):
        """Price and volume must agree with Claude's call since the news hit."""
        bars = self.api.bars([sym], "1Min", _iso(t0 - timedelta(minutes=2))).get(sym, [])
        if len(bars) < 2:
            return None, {"note": "no bars yet"}
        daily = self.api.bars([sym], "1Day", (_now() - timedelta(days=40)).strftime("%Y-%m-%d"),
                              end=(_now() - timedelta(days=1)).strftime("%Y-%m-%d")).get(sym, [])[-20:]
        adv = sum(b["v"] for b in daily) / len(daily) if daily else 0
        ref, last = bars[0]["o"], bars[-1]["c"]
        move = (last - ref) / ref if ref else 0
        vmult = (sum(b["v"] for b in bars[-3:]) / min(3, len(bars))) / (adv / 390) if adv else 0
        f = {"ref": ref, "last": last, "move_pct": round(move, 4), "vol_mult": round(vmult, 1)}
        want = 1 if bias == "long" else -1
        if move * want < self.N["confirm_pct"] or vmult < self.N["vol_mult"]:
            return None, f
        return last, f

    def can_enter(self, clock):
        return bool(clock.get("is_open")) and minutes_to_close(clock) >= config.NO_NEW_ENTRIES_BEFORE_CLOSE_MIN

    def handle_calls(self, calls, by_id):
        for c in calls:
            item = by_id.get(c.get("id"))
            if not item:
                continue
            c["symbol"] = str(c.get("symbol", "")).upper()
            _log({"stage": "scored", "id": c["id"], "symbol": c["symbol"], "bias": c["bias"],
                  "score": c.get("score"), "reason": c.get("reason"), "headline": item["headline"],
                  "created_at": item.get("created_at"), "src": item["src"]})
            if float(c.get("score") or 0) < self.N["min_score"]:
                continue
            update_sentiment(c, item)
            try:
                t0 = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            except (ValueError, KeyError, AttributeError):
                t0 = _now()
            expires = t0 + timedelta(minutes=self.N["max_age_min"])  # age counts from the headline
            if expires <= _now():
                _log({"stage": "too_old", "symbol": c["symbol"], "id": c["id"], "created_at": item.get("created_at")})
                continue
            self.pending.append({"call": c, "item": item, "t0": t0, "expires": expires})

    def work_pending(self, clock):
        keep = []
        for p in self.pending:
            c, item = p["call"], p["item"]
            if _now() > p["expires"] or not self.can_enter(clock):
                _log({"stage": "expired", "symbol": c["symbol"], "id": c["id"]})
                continue
            try:
                price, f = self.confirm(c["symbol"], c["bias"], p["t0"])
            except RuntimeError as e:
                _log({"stage": "error", "symbol": c["symbol"], "error": str(e)[:200]})
                continue
            if price is None:
                keep.append(p)
                continue
            lat = (_now() - p["t0"]).total_seconds()
            e = enter(self.api, symbol=c["symbol"], side="buy" if c["bias"] == "long" else "short", price=price,
                      kind="news", features=f,
                      signal_text=f"news confirm: move {f['move_pct']:+.2%}, vol {f['vol_mult']}x, {lat:.0f}s after headline",
                      catalyst={"bias": c["bias"], "score": c.get("score"), "reason": c.get("reason"),
                                "headline": item["headline"], "source": item.get("url"), "via": item["src"]},
                      stop_pct=self.N["stop_pct"], tp_pct=self.N["tp_pct"], dry_run=self.dry_run, P=self.P)
            _log({"stage": "entry", "symbol": c["symbol"], "result": e.get("result"), "latency_s": round(lat)})
        self.pending = keep

    def fresh(self, item):
        """Only score items young enough to trade; drops e.g. older 8-Ks on the first EDGAR poll."""
        try:
            t0 = datetime.fromisoformat(str(item.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            return False
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        return _now() - t0 <= timedelta(minutes=self.N["max_age_min"])

    def run(self):
        print(f"newswatch started {_now().isoformat()} dry_run={self.dry_run}")
        last_call = 0.0
        while True:
            clock = self.api.clock()
            if not clock.get("is_open") and datetime.now(ET).hour >= 12:
                print("market closed, exiting")
                return
            try:
                items = self.poll_news() + self.poll_edgar()
            except RuntimeError as e:
                print(f"poll error: {e}")
                items = []
            items = [i for i in items if self.fresh(i)]
            if items and self.calls_today < self.N["max_llm_calls"]:
                wait = 15 - (time.time() - last_call)  # group bursts into one call
                if wait > 0:
                    time.sleep(wait)
                    try:
                        items += self.poll_news()
                    except RuntimeError:
                        pass
                for it in items:
                    _log({"stage": "new", "id": it["id"], "symbols": it["symbols"], "headline": it["headline"],
                          "created_at": it.get("created_at"), "src": it["src"]})
                calls = ask_claude(items[:25])
                self.calls_today += 1
                last_call = time.time()
                self.handle_calls(calls, {i["id"]: i for i in items})
            if self.pending and clock.get("is_open"):
                self.work_pending(clock)
            time.sleep(POLL_SEC)
