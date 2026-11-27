import pytest

from agent import alpaca, config
from agent.risk import check_buy, trading_window_ok, bracket_prices

ACCT = {"equity": "100000", "last_equity": "100000", "cash": "100000"}
U = {"AAPL"}


def buy(**kw):
    args = dict(account=ACCT, positions=[], buys_today=0, price=200.0, universe=U, tradable=True)
    args.update(kw)
    return check_buy({"symbol": "AAPL", "confidence": 0.8}, **args)


def test_sizes_to_five_percent():
    assert buy() == (25, "ok")


def test_rejections():
    assert buy(universe=set())[0] == 0
    assert buy(positions=[{"symbol": "AAPL"}])[0] == 0
    assert buy(buys_today=config.MAX_NEW_BUYS_PER_DAY)[0] == 0
    assert buy(positions=[{"symbol": f"X{i}"} for i in range(10)])[0] == 0
    assert buy(account={**ACCT, "equity": "96000"})[0] == 0
    assert buy(account={**ACCT, "cash": "10100"})[0] == 0
    assert buy(price=5.0)[0] == 0
    assert check_buy({"symbol": "AAPL", "confidence": 0.5}, account=ACCT, positions=[], buys_today=0,
                     price=200.0, universe=U, tradable=True)[0] == 0


def test_window():
    base = {"is_open": True, "timestamp": "2026-11-27T10:00:00-05:00"}
    assert trading_window_ok({**base, "next_close": "2026-11-27T16:00:00-05:00"})[0]
    assert not trading_window_ok({**base, "timestamp": "2026-11-27T09:35:00-05:00",
                                  "next_close": "2026-11-27T16:00:00-05:00"})[0]
    assert not trading_window_ok({**base, "timestamp": "2026-11-27T15:50:00-05:00",
                                  "next_close": "2026-11-27T16:00:00-05:00"})[0]
    assert not trading_window_ok({"is_open": False})[0]


def test_bracket():
    assert bracket_prices(100.0) == (115.0, 92.0)


def test_refuses_live_key():
    with pytest.raises(alpaca.LiveTradingRefused):
        alpaca.Alpaca(key="AKLIVE", secret="x")


def test_paper_endpoint_is_constant():
    assert config.PAPER_BASE_URL == "https://paper-api.alpaca.markets"
