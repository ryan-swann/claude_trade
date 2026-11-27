import pytest

from agent import alpaca, config
from agent.risk import bracket_prices, entries_allowed, should_flatten, size_entry
from agent.strategy import features, signal

P = config.load_params()
ACCT = {"equity": "100000", "last_equity": "100000", "cash": "100000", "buying_power": "200000"}


def size(side="buy", **kw):
    args = dict(account=ACCT, positions=[], entries_today=0, price=100.0, tradable=True, shortable=True, P=P)
    args.update(kw)
    return size_entry(side, **args)


def test_sizes_to_position_pct():
    assert size() == (200, "ok")


def test_rejections():
    assert size(tradable=False)[0] == 0
    assert size("short", shortable=False)[0] == 0
    assert size(positions=[{"symbol": f"X{i}", "market_value": 1} for i in range(5)])[0] == 0
    assert size(entries_today=P["max_entries_per_day"])[0] == 0
    assert size(account={**ACCT, "equity": "95000"})[0] == 0        # -5% day
    assert size(account={**ACCT, "equity": "20000", "last_equity": "20000"})[0] == 0  # PDT
    assert size(price=1.0)[0] == 0
    assert size(positions=[{"symbol": "A", "market_value": 199950}])[0] == 0  # gross exposure


def clk(t):
    return {"is_open": True, "timestamp": f"2026-11-30T{t}:00-05:00", "next_close": "2026-11-30T16:00:00-05:00"}


def test_windows():
    assert not entries_allowed(clk("09:40"), P)[0]   # opening range forming
    assert entries_allowed(clk("10:00"), P)[0]
    assert not entries_allowed(clk("15:35"), P)[0]   # entry cutoff
    assert not should_flatten(clk("15:40"))
    assert should_flatten(clk("15:50"))
    assert not entries_allowed({"is_open": False}, P)[0]


def test_brackets():
    assert bracket_prices(100.0, "buy", P) == (104.0, 98.0)
    assert bracket_prices(100.0, "short", P) == (96.0, 102.0)


def bars(seq):
    return [{"o": o, "h": h, "l": l, "c": c, "v": v} for o, h, l, c, v in seq]


def test_orb_long_and_bias_filter():
    b = bars([(100, 101, 99, 100.5, 1e5), (100.5, 101, 100, 100.8, 1e5), (100.8, 101, 100.2, 100.9, 1e5),
              (101, 104, 101, 103.5, 3e5)])
    f = features(b, avg_daily_volume=1e6, minutes_elapsed=20, or_minutes=15)
    assert f["or_high"] == 101 and f["rvol"] > 2
    assert signal(f, P)[0] == "buy"
    assert signal(f, P, bias="short")[0] is None


def test_orb_short():
    b = bars([(100, 101, 99, 99.5, 1e5), (99.5, 100, 99, 99.2, 1e5), (99.2, 99.8, 99, 99.1, 1e5),
              (99, 99, 96, 96.5, 3e5)])
    f = features(b, avg_daily_volume=1e6, minutes_elapsed=20, or_minutes=15)
    assert signal(f, P)[0] == "short"


def test_params_clamped(tmp_path):
    p = tmp_path / "p.json"
    p.write_text('{"position_pct": 0.9, "stop_loss_pct": 0.5, "max_positions": 50}')
    c = config.load_params(p)
    assert c["position_pct"] == 0.30 and c["stop_loss_pct"] == 0.05 and c["max_positions"] == 8


def test_refuses_live_key():
    with pytest.raises(alpaca.LiveTradingRefused):
        alpaca.Alpaca(key="AKLIVE", secret="x")


def test_paper_endpoint_is_constant():
    assert config.PAPER_BASE_URL == "https://paper-api.alpaca.markets"


def test_sentiment_expiry(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from agent import trader
    f = tmp_path / "s.json"
    now = datetime.now(timezone.utc)
    f.write_text(json.dumps({"symbols": {
        "AAA": {"bias": "long", "expires": (now + timedelta(minutes=30)).isoformat()},
        "BBB": {"bias": "short", "expires": (now - timedelta(minutes=1)).isoformat()}}}))
    monkeypatch.setattr(config, "SENTIMENT_FILE", f)
    monkeypatch.setattr(config, "WATCHLIST_FILE", tmp_path / "none.json")
    assert set(trader.merged_bias()) == {"AAA"}


def test_news_params_clamped():
    from agent.newswatch import news_params
    n = news_params({"news_stop_pct": 0.5, "news_min_score": 0.1, "news_max_llm_calls": 10000})
    assert n["stop_pct"] == config.BOUNDS["stop_loss_pct"][1]
    assert n["min_score"] == 0.5 and n["max_llm_calls"] == 600


def test_parse_calls():
    from agent.newswatch import parse_calls
    txt = 'Sure:\n[{"id":"n1","symbol":"ABC","bias":"long","score":0.8,"reason":"beat"},{"id":"n2","bias":"meh"}]'
    assert [c["id"] for c in parse_calls(txt)] == ["n1"]
    assert parse_calls("nothing") == []


def test_catalyst_filter():
    from agent.newswatch import CATALYST
    assert CATALYST.search("Acme raises full-year guidance")
    assert not CATALYST.search("5 stocks moving in Tuesday's session")


class FakeAPI:
    def __init__(self, ask, bid, last=None):
        self.ask, self.bid, self.last = ask, bid, last

    def data(self, path, params=None):
        return {"quotes": {params["symbols"]: {"ap": self.ask, "bp": self.bid}}}

    def latest_trades(self, syms):
        return {syms[0]: self.last} if self.last else {}


def test_live_reference_rules():
    from agent.trader import entry_params, live_reference
    E = entry_params(P)
    ref, lim, info = live_reference(FakeAPI(10.02, 10.00), "X", "buy", 10.0, E)
    assert ref == 10.02 and lim == round(10.02 * 1.002, 2)
    assert live_reference(FakeAPI(10.20, 10.18), "X", "buy", 10.0, E)[0] is None   # ran 2% away (RETO case)
    assert live_reference(FakeAPI(10.30, 10.00), "X", "buy", 10.0, E)[0] is None   # wide spread
    ref, lim, _ = live_reference(FakeAPI(0, 0, last=9.99), "X", "short", 10.0, E)  # no quote -> last trade
    assert ref == 9.99 and lim == round(9.99 * 0.998, 2)


def test_news_freshness():
    from datetime import datetime, timedelta, timezone
    from agent.newswatch import Watcher
    w = Watcher.__new__(Watcher)
    w.N = {"max_age_min": 10}
    now = datetime.now(timezone.utc)
    assert w.fresh({"created_at": (now - timedelta(minutes=2)).isoformat()})
    assert not w.fresh({"created_at": (now - timedelta(days=2)).isoformat()})
    assert not w.fresh({"created_at": "garbage"})
