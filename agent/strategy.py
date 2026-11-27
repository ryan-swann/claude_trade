"""Opening-range breakout on stocks in play (catalyst / movers), confirmed by VWAP and volume."""


def features(bars, avg_daily_volume, minutes_elapsed, or_minutes):
    """bars: today's 5-minute bars [{o,h,l,c,v}] oldest first."""
    n_or = max(1, or_minutes // 5)
    if len(bars) <= n_or:
        return None
    or_bars = bars[:n_or]
    or_high = max(b["h"] for b in or_bars)
    or_low = min(b["l"] for b in or_bars)
    pv = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars)
    vol = sum(b["v"] for b in bars)
    vwap = pv / vol if vol else bars[-1]["c"]
    open_ = bars[0]["o"]
    last = bars[-1]["c"]
    frac = max(minutes_elapsed, 5) / 390
    rvol = vol / (avg_daily_volume * frac) if avg_daily_volume else 0.0
    return {"or_high": or_high, "or_low": or_low, "vwap": vwap, "last": last, "open": open_,
            "move_pct": (last - open_) / open_ if open_ else 0.0, "rvol": rvol}


def signal(f, P, bias=None):
    """Return ("buy"|"short"|None, score, why)."""
    if not f:
        return None, 0.0, "not enough bars"
    if f["rvol"] < P["min_rvol"]:
        return None, 0.0, f"rvol {f['rvol']:.1f} < {P['min_rvol']}"
    buf = P["breakout_buffer_pct"]
    side = None
    if f["last"] > f["or_high"] * (1 + buf) and f["last"] > f["vwap"]:
        side = "buy"
    elif f["last"] < f["or_low"] * (1 - buf) and f["last"] < f["vwap"]:
        side = "short"
    if not side:
        return None, 0.0, "no breakout"
    if side == "short" and not P.get("allow_shorts", True):
        return None, 0.0, "shorts disabled"
    want = "long" if side == "buy" else "short"
    if bias and bias != want:
        return None, 0.0, f"breakout against Claude's {bias} bias"
    if P.get("require_bias") and not bias:
        return None, 0.0, "no Claude bias"
    if abs(f["move_pct"]) < P["min_move_pct"] and not bias:
        return None, 0.0, f"move {f['move_pct']:+.1%} too small without a catalyst"
    score = f["rvol"] * (1 + abs(f["move_pct"]) * 10) * (1 + (P["bias_boost"] if bias else 0))
    return side, score, f"ORB {side}, rvol {f['rvol']:.1f}, move {f['move_pct']:+.1%}" + (f", bias {bias}" if bias else "")
