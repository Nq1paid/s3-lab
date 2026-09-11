"""Monte Carlo method 1 -- trade-sequence bootstrap.

Two questions, deliberately kept separate:

- **resample** (with replacement) asks what this edge might have produced on a
  different draw of trades from the same distribution. Final equity varies.
- **shuffle** (without replacement) asks what the SAME trades in a different
  order would have done. Final equity is identical in every path by
  construction -- only the path differs. That is the point: it isolates
  sequence risk, and a strategy whose drawdown depends heavily on trade order
  is one bad run away from a very different account.

Reporting a single number from either would defeat the exercise, so everything
comes back as percentile bands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numba import njit, prange

PERCENTILES = (5, 25, 50, 75, 95)


@njit(cache=True, parallel=True)
def _path_stats(pnl, start_equity, ruin_level):
    n_iter, n_tr = pnl.shape
    final = np.empty(n_iter, np.float64)
    maxdd = np.empty(n_iter, np.float64)
    streak = np.empty(n_iter, np.int64)
    ruined = np.zeros(n_iter, np.int8)
    for i in prange(n_iter):
        eq = start_equity
        peak = start_equity
        dd = 0.0
        s = 0
        best = 0
        hit = 0
        for j in range(n_tr):
            x = pnl[i, j]
            eq += x
            if eq > peak:
                peak = eq
            d = peak - eq
            if d > dd:
                dd = d
            if x < 0.0:
                s += 1
                if s > best:
                    best = s
            else:
                s = 0
            if eq <= ruin_level:
                hit = 1
        final[i] = eq
        maxdd[i] = dd
        streak[i] = best
        ruined[i] = hit
    return final, maxdd, streak, ruined


@dataclass
class BootstrapResult:
    method: str
    iterations: int
    n_trades: int
    starting_equity: float
    ruin_level: float
    final_equity: np.ndarray
    max_drawdown: np.ndarray
    longest_losing_streak: np.ndarray
    risk_of_ruin: float
    observed_final: float
    observed_max_dd: float
    notes: list[str] = field(default_factory=list)

    def bands(self, values: np.ndarray) -> dict[int, float]:
        return {p: float(np.percentile(values, p)) for p in PERCENTILES}

    def render(self) -> str:
        fe, dd, st = (self.bands(x) for x in
                      (self.final_equity, self.max_drawdown, self.longest_losing_streak))
        head = (f"  {self.method.upper()}   {self.iterations:,} iterations over "
                f"{self.n_trades:,} trades")
        cols = "        " + "".join(f"{f'P{p}':>12}" for p in PERCENTILES)
        rows = [
            head, "", cols,
            "  final " + "".join(f"{fe[p]:>12,.0f}" for p in PERCENTILES),
            "  maxDD " + "".join(f"{dd[p]:>12,.0f}" for p in PERCENTILES),
            "  strk  " + "".join(f"{st[p]:>12,.0f}" for p in PERCENTILES),
            "",
            f"  observed final {self.observed_final:>14,.0f}   max drawdown {self.observed_max_dd:>12,.0f}",
            f"  risk of ruin   {self.risk_of_ruin:>13.2%}   (equity ever <= {self.ruin_level:,.0f})",
        ]
        for n in self.notes:
            rows.append(f"  ! {n}")
        return "\n".join(rows)


def bootstrap_trades(
    pnl: np.ndarray,
    starting_equity: float = 100_000.0,
    iterations: int = 10_000,
    method: str = "resample",
    ruin_fraction: float = 0.5,
    seed: int = 0,
    chunk: int = 2_000,
) -> BootstrapResult:
    """Bootstrap a trade P&L series.

    `ruin_fraction` is the share of the account whose loss counts as ruin --
    0.5 means equity halving. Ruin is checked at every trade along the path,
    not just at the end, because an account that hits zero mid-sequence does
    not get to trade its way back.
    """
    pnl = np.asarray(pnl, dtype="float64")
    n = len(pnl)
    if n == 0:
        raise ValueError("no trades to bootstrap")
    if method not in ("resample", "shuffle"):
        raise ValueError(f"unknown method {method!r}; expected 'resample' or 'shuffle'")

    ruin_level = starting_equity * (1.0 - ruin_fraction)
    rng = np.random.default_rng(seed)

    finals, dds, streaks, ruins = [], [], [], []
    done = 0
    while done < iterations:
        m = min(chunk, iterations - done)
        if method == "resample":
            idx = rng.integers(0, n, size=(m, n))
        else:
            idx = np.argsort(rng.random((m, n)), axis=1)
        f, d, s, r = _path_stats(pnl[idx], starting_equity, ruin_level)
        finals.append(f); dds.append(d); streaks.append(s); ruins.append(r)
        done += m

    final = np.concatenate(finals)
    maxdd = np.concatenate(dds)
    streak = np.concatenate(streaks)
    ruined = np.concatenate(ruins)

    # Observed path, for comparison against the distribution.
    obs_f, obs_d, _, _ = _path_stats(pnl.reshape(1, -1), starting_equity, ruin_level)

    notes: list[str] = []
    if method == "shuffle":
        notes.append(
            "shuffle preserves the trade set, so final equity is identical in every "
            "path by construction -- read the drawdown and streak bands, not final"
        )
    if n < 30:
        notes.append(f"only {n} trades: bands are wide and not a basis for any conclusion")

    return BootstrapResult(
        method=method, iterations=iterations, n_trades=n,
        starting_equity=starting_equity, ruin_level=ruin_level,
        final_equity=final, max_drawdown=maxdd, longest_losing_streak=streak,
        risk_of_ruin=float(ruined.mean()),
        observed_final=float(obs_f[0]), observed_max_dd=float(obs_d[0]),
        notes=notes,
    )
