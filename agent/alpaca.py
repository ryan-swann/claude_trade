"""Minimal Alpaca REST client, locked to the paper endpoint."""
import os
import requests

from . import config


class LiveTradingRefused(RuntimeError):
    pass


class Alpaca:
    def __init__(self, key=None, secret=None, session=None):
        self.key = key or os.environ.get("ALPACA_API_KEY", "")
        self.secret = secret or os.environ.get("ALPACA_SECRET_KEY", "")
        if not self.key or not self.secret:
            raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (paper keys).")
        # Alpaca live keys start with "AK"; paper keys start with "PK".
        if not self.key.startswith("PK"):
            raise LiveTradingRefused("API key does not look like a paper key (expected 'PK...'). Refusing.")
        self.s = session or requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret})
        self._verified = False

    def _req(self, method, base, path, **kw):
        if base not in (config.PAPER_BASE_URL, config.DATA_BASE_URL):
            raise LiveTradingRefused(f"Endpoint {base} is not allowed.")
        r = self.s.request(method, base + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Alpaca {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def trade(self, method, path, **kw):
        if not self._verified and not (method == "GET" and path == "/v2/account"):
            self.verify_paper()
        return self._req(method, config.PAPER_BASE_URL, path, **kw)

    def data(self, path, **kw):
        return self._req("GET", config.DATA_BASE_URL, path, **kw)

    def verify_paper(self):
        acct = self._req("GET", config.PAPER_BASE_URL, "/v2/account")
        if not str(acct.get("account_number", "")).startswith("PA"):
            raise LiveTradingRefused("Account is not a paper account. Refusing.")
        self._verified = True
        return acct

    # Convenience wrappers
    def account(self):
        return self.verify_paper()

    def clock(self):
        return self.trade("GET", "/v2/clock")

    def positions(self):
        return self.trade("GET", "/v2/positions")

    def asset(self, symbol):
        return self.trade("GET", f"/v2/assets/{symbol}")

    def orders(self, status="all", after=None, limit=500):
        params = {"status": status, "limit": limit, "nested": "true"}
        if after:
            params["after"] = after
        return self.trade("GET", "/v2/orders", params=params)

    def cancel_order(self, order_id):
        return self.trade("DELETE", f"/v2/orders/{order_id}")

    def submit_order(self, **order):
        return self.trade("POST", "/v2/orders", json=order)

    def close_position(self, symbol):
        return self.trade("DELETE", f"/v2/positions/{symbol}")

    def latest_trades(self, symbols):
        out = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            res = self.data("/v2/stocks/trades/latest", params={"symbols": ",".join(chunk), "feed": "iex"})
            for sym, t in (res.get("trades") or {}).items():
                out[sym] = t["p"]
        return out

    def news(self, symbols, start, limit=50):
        items, token = [], None
        for i in range(0, len(symbols), 50):
            chunk = ",".join(symbols[i:i + 50])
            token = None
            while True:
                params = {"symbols": chunk, "start": start, "limit": limit, "sort": "desc"}
                if token:
                    params["page_token"] = token
                res = self.data("/v1beta1/news", params=params)
                items.extend(res.get("news", []))
                token = res.get("next_page_token")
                if not token or len(items) > 400:
                    break
        return items

    # Market data helpers (IEX feed, free with paper accounts)
    def bars(self, symbols, timeframe, start, end=None, limit=10000):
        out, token = {}, None
        while True:
            params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start,
                      "limit": limit, "feed": "iex", "adjustment": "raw"}
            if end:
                params["end"] = end
            if token:
                params["page_token"] = token
            res = self.data("/v2/stocks/bars", params=params)
            for s, bs in (res.get("bars") or {}).items():
                out.setdefault(s, []).extend(bs)
            token = res.get("next_page_token")
            if not token:
                return out

    def movers(self, top=20):
        res = self.data("/v1beta1/screener/stocks/movers", params={"top": top})
        return [m["symbol"] for m in res.get("gainers", []) + res.get("losers", [])]

    def most_actives(self, top=20):
        res = self.data("/v1beta1/screener/stocks/most-actives", params={"by": "volume", "top": top})
        return [m["symbol"] for m in res.get("most_actives", [])]
