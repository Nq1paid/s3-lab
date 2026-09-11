"""Monte Carlo methods 3 and 4 -- noise injection and block bootstrap.

Both re-run the strategy against an altered price series, so both answer the
same underlying question from different directions: how much of this result
depends on the exact path prices took, rather than on an edge that would
survive a slightly different history?

Method 3 perturbs the series it was fitted to. Method 4 rebuilds a new series
from contiguous blocks of the real one, preserving short-range autocorrelation
while destroying the specific sequence of sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PERCENTILES = (5, 25, 50, 75, 95)


@dataclass
class ResampleResult:
    method: str
    iterations: int
    baseline: float
    values: np.ndarray
    degradation: float          # median outcome vs baseline, as a fraction
    beat_baseline: float        # share of iterations at or above baseline
    notes: list[str] = field(default_factory=list)

    def bands(self) -> dict[int, float]:
        return {p: float(np.percentile(self.values, p)) for p in PERCENTILES}

    def render(self) -> str:
        b = self.bands()
        cols = "        " + "".join(f"{f'P{p}':>12}" for p in PERCENTILES)
        rows = [
            f"  {self.method.upper()}   {self.iterations:,} iterations",
            "", cols,
            "  net   " + "".join(f"{b[p]:>12,.0f}" for p in PERCENTILES),
            "",
            f"  baseline (unaltered)     {self.baseline:>12,.0f}",
            f"  median degradation       {self.degradation:>11.1%}  of baseline retained",
            f"  iterations >= baseline   {self.beat_baseline:>11.1%}",
        ]
        for n in self.notes:
            rows.append(f"  ! {n}")
        return "\n".join(rows)


def inject_noise(
    bars: dict[str, np.ndarray],
    rng: np.random.Generator,
    price_ticks: float,
    tick_size: float,
    shift_bars: int = 0,
) -> dict[str, np.ndarray]:
    """Add tick-scale noise to prices and optionally shift entry timing.

    Noise is applied to the whole bar so O/H/L/C stay internally consistent --
    jittering each field independently would manufacture bars where the high is
    below the close, which the engine rightly refuses to trade.
    """
    out = dict(bars)
    n = len(bars["close"])
    if price_ticks > 0:
        offset = rng.normal(0.0, price_ticks * tick_size, n)
        offset = np.round(offset / tick_size) * tick_size
        for key in ("open", "high", "low", "close"):
            out[key] = bars[key] + offset
    if shift_bars:
        # Shifting the series moves every decision by the same number of bars,
        # which is the timing question; shifting orders instead would let a
        # strategy peek at bars it has not been shown.
        k = int(rng.integers(-abs(shift_bars), abs(shift_bars) + 1))
        if k:
            for key in ("open", "high", "low", "close"):
                out[key] = np.roll(out[key], k)
    return out


def session_starts(sess: np.ndarray) -> np.ndarray:
    """Index of the first bar of each session."""
    return np.concatenate(([0], np.flatnonzero(np.diff(sess)) + 1))


def block_bootstrap(
    bars: dict[str, np.ndarray],
    rng: np.random.Generator,
    block_len: int,
    align_starts: np.ndarray | None = None,
    returns: str = "diff",
) -> dict[str, np.ndarray]:
    """Rebuild the series from contiguous blocks, re-based so it stays continuous.

    Blocks are stitched on RETURNS, not on raw prices. Concatenating raw price
    blocks creates a huge artificial gap at every seam -- the strategy would
    trade those gaps and the result would measure the seams, not the strategy.

    `align_starts` restricts block starts to session boundaries, so a
    "one session" block is a real session with an open, a range and a close
    rather than a window beginning at a random point in the day.

    `returns` decides what is resampled, and for a tick-denominated strategy it
    is the setting that matters most:

    - "diff" resamples ARITHMETIC price changes, preserving absolute point
      volatility. A 60-tick stop means the same thing everywhere in the
      synthetic series as it did in the real one.
    - "log" resamples log returns, preserving PERCENTAGE volatility. The
      synthetic path then drifts to price levels the original never visited
      (measured: 1.9x to 8.6x the start against the real 3.8x), and because
      point volatility scales with price, a fixed 15-point stop becomes far
      tighter in volatility terms than it ever was in the real data. The
      strategy stops out constantly and the test reports a catastrophe that is
      an artefact of the rescaling, not a property of the strategy.

    Default is "diff". Use "log" only for a strategy whose risk is expressed in
    percent.
    """
    n = len(bars["close"])
    if block_len < 2 or block_len >= n:
        return dict(bars)

    close = bars["close"]
    use_log = returns == "log"
    rets = (np.diff(np.log(np.maximum(close, 1e-9))) if use_log
            else np.diff(close))
    n_blocks = int(np.ceil((n - 1) / block_len))
    if align_starts is not None and len(align_starts) > 2:
        pool = align_starts[align_starts < len(rets) - block_len]
        if len(pool) == 0:
            pool = np.array([0])
        starts = rng.choice(pool, n_blocks, replace=True)
    else:
        starts = rng.integers(0, max(1, len(rets) - block_len), n_blocks)
    drawn = np.concatenate([rets[s:s + block_len] for s in starts])[: n - 1]

    new_close = np.empty(n)
    new_close[0] = close[0]
    if use_log:
        new_close[1:] = close[0] * np.exp(np.cumsum(drawn))
    else:
        new_close[1:] = close[0] + np.cumsum(drawn)
    # A price series cannot go non-positive; clamp rather than emit bars the
    # engine would reject.
    new_close = np.maximum(new_close, close.min() * 0.1)

    # Carry each bar's internal shape onto the new close. Under "diff" the
    # offset is added so the bar's absolute range is preserved exactly; under
    # "log" it is scaled so its percentage range is.
    out = dict(bars)
    out["close"] = new_close
    if use_log:
        scale = new_close / np.maximum(close, 1e-9)
        for key in ("open", "high", "low"):
            out[key] = bars[key] * scale
    else:
        offset = new_close - close
        for key in ("open", "high", "low"):
            out[key] = bars[key] + offset
    return out


def session_block_bootstrap(
    bars: dict[str, np.ndarray],
    rng: np.random.Generator,
    returns: str = "diff",
) -> dict[str, np.ndarray]:
    """Resample whole sessions by copying real bars, level-shifted to join.

    Every synthetic session is a REAL session's bars -- open, high, low and
    close together -- offset by a single constant so the series stays
    continuous. Nothing about a bar's internal shape or its relationship to the
    next bar is synthesised.

    The two wrong ways to do this, both tried and measured:

    - Resampling close-to-close steps and shifting each original bar by its own
      offset keeps every bar internally valid (no high < close anywhere) while
      still manufacturing gaps: a bar can be given a 10-point close-to-close
      move whose own high/low range is 2 points. The engine correctly treats
      that as a gap through the stop and fills at the next open, so the
      strategy bleeds on fills that never existed. It measured -283k against a
      +36k baseline, which is a property of the construction, not the strategy.
    - Resampling log returns rescales absolute point volatility with the price
      level, so a tick-denominated stop silently changes meaning.

    Copying whole bars avoids both. A 60-tick stop means the same thing it did
    in the real data, and an opening range is a real opening range.
    """
    n = len(bars["close"])
    if "sess" not in bars or n < 3:
        return dict(bars)

    starts = session_starts(bars["sess"])
    bounds = [(a, b) for a, b in zip(starts, list(starts[1:]) + [n]) if b - a >= 2]
    if len(bounds) < 3:
        return dict(bars)

    keys = ("open", "high", "low", "close")
    out = {k: np.empty(n) for k in keys}
    for k, v in bars.items():
        if k not in keys:
            out[k] = v

    level = float(bars["close"][0])
    picks = rng.integers(0, len(bounds), len(bounds))
    for (a, b), src in zip(bounds, picks):
        sa, sb = bounds[src]
        want = b - a
        have = sb - sa
        take = min(want, have)
        shift = level - float(bars["open"][sa])
        for k in keys:
            out[k][a:a + take] = bars[k][sa:sa + take] + shift
        if take < want:
            # Source session shorter than the slot: hold the last bar flat
            # rather than splicing in a second session, which would put a seam
            # in the middle of a trading day.
            for k in keys:
                out[k][a + take:b] = out["close"][a + take - 1]
        level = float(out["close"][b - 1])

    del returns          # whole-bar copying is scale-free; nothing to choose
    return out


def run_resample(
    evaluate,
    bars: dict[str, np.ndarray],
    method: str,
    iterations: int = 500,
    seed: int = 0,
    price_ticks: float = 1.0,
    shift_bars: int = 0,
    block_len: int = 0,          # 0 = whole sessions, the correct default
    align_sessions: bool = True,
    returns: str = "diff",
    tick_size: float = 0.25,
    progress=None,
    cancel=None,
) -> ResampleResult:
    """Re-run `evaluate(bars) -> net pnl` against altered price series.

    `cancel()` is polled every iteration: the block bootstrap is the most
    expensive method in the suite and the UI must be able to stop it mid-run.
    """
    if method not in ("noise", "block"):
        raise ValueError(f"unknown method {method!r}; expected 'noise' or 'block'")

    rng = np.random.default_rng(seed)
    starts = (
        session_starts(bars["sess"])
        if align_sessions and "sess" in bars else None
    )
    baseline = float(evaluate(bars))

    values: list[float] = []
    for i in range(iterations):
        if cancel is not None and cancel():
            break
        if method == "noise":
            altered = inject_noise(bars, rng, price_ticks, tick_size, shift_bars)
        elif block_len <= 0:
            altered = session_block_bootstrap(bars, rng, returns)
        else:
            altered = block_bootstrap(bars, rng, block_len, starts, returns)
        values.append(float(evaluate(altered)))
        if progress:
            progress(i + 1, iterations)

    arr = np.asarray(values, dtype="float64")
    notes: list[str] = []
    if len(arr) < iterations:
        notes.append(f"cancelled after {len(arr)} of {iterations} iterations")
    if baseline <= 0:
        notes.append("baseline is not profitable, so degradation is not meaningful")

    median = float(np.median(arr)) if len(arr) else float("nan")
    degradation = median / baseline if baseline else float("nan")
    beat = float(np.mean(arr >= baseline)) if len(arr) else float("nan")

    return ResampleResult(
        method=method, iterations=len(arr), baseline=baseline, values=arr,
        degradation=degradation, beat_baseline=beat, notes=notes,
    )
