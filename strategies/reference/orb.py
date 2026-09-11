"""Reference strategy: opening-range breakout.

Deliberately simple. Its job is to exercise the engine end to end and to be the
thing the Java port must reproduce trade-for-trade, not to make money.

Entry is a market order on the bar that breaks the opening range, filling at the
next bar's open. The classic formulation rests a stop order at the range edge
instead; a market-on-break avoids needing OCO and order expiry, and every level
it references is known at decision time. No value used here comes from a bar the
strategy has not already been shown.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.core.types import Order, OrderType, Side
from engine.data.annotate import RTH_OPEN_MIN

DESCRIBE = {
    "proto": 1,
    "name": "orb_breakout",
    "version": "1.0",
    "params": [
        {"name": "or_minutes", "type": "int", "default": 30, "min": 5, "max": 120, "step": 5},
        {"name": "buffer_ticks", "type": "int", "default": 2, "min": 0, "max": 20, "step": 1},
        {"name": "stop_ticks", "type": "int", "default": 40, "min": 10, "max": 200, "step": 5},
        {"name": "target_r", "type": "float", "default": 2.0, "min": 0.5, "max": 6.0, "step": 0.5},
        {"name": "no_new_after", "type": "int", "default": 840, "min": 600, "max": 900, "step": 30},
        {"name": "qty", "type": "int", "default": 1, "min": 1, "max": 10, "step": 1},
    ],
}

DEFAULTS = {p["name"]: p["default"] for p in DESCRIBE["params"]}


def generate_orders(bars: dict[str, np.ndarray], tick_size: float, **params) -> list[Order]:
    """One entry per session, at the first bar that breaks the opening range."""
    p = {**DEFAULTS, **params}
    or_end = RTH_OPEN_MIN + int(p["or_minutes"])
    buf = int(p["buffer_ticks"]) * tick_size
    stop_dist = int(p["stop_ticks"]) * tick_size
    target_dist = stop_dist * float(p["target_r"])

    mod, sess = bars["mod"], bars["sess"]
    hi, lo, cl = bars["high"], bars["low"], bars["close"]

    in_or = (mod >= RTH_OPEN_MIN) & (mod < or_end)
    if not in_or.any():
        return []

    # Opening range per session, broadcast back to every bar of that session.
    df = pd.DataFrame({"sess": sess, "hi": hi, "lo": lo})
    rng = df[in_or].groupby("sess").agg(or_hi=("hi", "max"), or_lo=("lo", "min"))
    or_hi = df["sess"].map(rng["or_hi"]).to_numpy()
    or_lo = df["sess"].map(rng["or_lo"]).to_numpy()

    armed = (mod >= or_end) & (mod < int(p["no_new_after"])) & ~np.isnan(or_hi)
    long_break = armed & (hi > or_hi + buf)
    short_break = armed & (lo < or_lo - buf)
    any_break = long_break | short_break
    if not any_break.any():
        return []

    # First breaking bar of each session.
    idx = np.flatnonzero(any_break)
    first = pd.Series(idx).groupby(sess[idx]).min().to_numpy()

    # A bar that breaks BOTH sides is ambiguous -- the bar's high and low give
    # no ordering, so which side triggered first is unknowable. Skipping the
    # session is the only honest option; picking one would be a coin flip
    # dressed as a signal.
    both = long_break[first] & short_break[first]
    first = first[~both]

    qty = int(p["qty"])
    orders: list[Order] = []
    for i in first:
        i = int(i)
        ref = float(cl[i])          # known at decision time
        if long_break[i]:
            orders.append(
                Order(Side.BUY, OrderType.MARKET, qty, bar=i,
                      stop_loss=ref - stop_dist, take_profit=ref + target_dist, tag="orb_long")
            )
        else:
            orders.append(
                Order(Side.SELL, OrderType.MARKET, qty, bar=i,
                      stop_loss=ref + stop_dist, take_profit=ref - target_dist, tag="orb_short")
            )
    return orders


def describe() -> dict:
    return DESCRIBE
