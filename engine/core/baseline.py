"""Buy-and-hold baseline.

Required alongside every result: same period, same instrument, same slippage
applied to a single entry and exit, sized to the same contract count. A strategy that merely tracks being long is not a strategy, and the only
way to see that is to draw the alternative next to it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np


class InsufficientCoverage(Exception):
    """The cached series does not span the window the baseline was asked for."""


def _day(ts) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def buy_and_hold(
    ts: np.ndarray,
    close: np.ndarray,
    start_ts: int,
    end_ts: int,
    point_value: float,
    starting_equity: float = 100_000.0,
    qty: int = 1,
    slippage_ticks: float = 1.0,
    tick_size: float = 0.25,
    samples: int | None = None,
    tolerance_days: float = 3.0,
) -> np.ndarray:
    """Equity curve for holding `qty` contracts across the window.

    Slippage is charged on the single entry and the single exit only -- that
    is the whole point of the comparison, and loading it with per-trade costs
    it never paid would flatter the strategy.

    Raises `InsufficientCoverage` if the series does not actually span the
    window asked for. It used to silently hold whatever bars it had, which
    turned a baseline over a shorter period into "the" baseline: on a run from
    2019 measured against four years of bars it reported +340% where the truth
    was +447%, and nothing on screen said the comparison had moved. A missing
    baseline is a fact the UI can report; a wrong one is not.

    `tolerance_days` allows the small gap between a run's first trade and the
    first bar of that session, which is normal and not missing data.
    """
    lo = int(np.searchsorted(ts, start_ts, side="left"))
    hi = int(np.searchsorted(ts, end_ts, side="right"))
    seg = close[lo:hi]
    def refuse() -> InsufficientCoverage:
        """Both refusals name both windows. "No data" without saying which
        data is a message that sends someone looking in the wrong place."""
        return InsufficientCoverage(
            "the cached series covers "
            + _day(ts[0]) + " to " + _day(ts[-1])
            + ", the run covers " + _day(start_ts) + " to " + _day(end_ts)
            + " -- import the full series for a buy-and-hold comparison")

    if len(seg) < 2:
        raise refuse()

    slack = tolerance_days * 24 * 3600
    if ts[0] > start_ts + slack or ts[-1] < end_ts - slack:
        raise refuse()

    slip = slippage_ticks * tick_size
    entry = float(seg[0]) + slip
    equity = starting_equity + (seg - entry) * point_value * qty
    equity[-1] -= slip * point_value * qty                        # exit side

    if samples and samples != len(equity):
        equity = np.interp(
            np.linspace(0.0, 1.0, samples), np.linspace(0.0, 1.0, len(equity)), equity
        )
    return equity
