"""Walk-forward fold generation.

Folds are cut on SESSION boundaries, never on raw bar counts or calendar dates.
A fold that starts mid-session would hand a strategy a partial opening range and
let in-sample data bleed across the boundary -- the exact leak walk-forward
exists to prevent.

Windows are measured in trading sessions rather than calendar days so that
holidays and half-days do not silently shrink a fold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal

import numpy as np

Mode = Literal["rolling", "anchored"]


@dataclass(frozen=True)
class Fold:
    index: int
    is_start: int      # bar indices, half-open [start, end)
    is_end: int
    oos_start: int
    oos_end: int

    @property
    def is_bars(self) -> int:
        return self.is_end - self.is_start

    @property
    def oos_bars(self) -> int:
        return self.oos_end - self.oos_start

    def __post_init__(self) -> None:
        # The guarantee the whole method rests on.
        if self.oos_start < self.is_end:
            raise ValueError(
                f"fold {self.index}: OOS starts at {self.oos_start} before IS ends "
                f"at {self.is_end} -- in-sample data would leak into the test"
            )


@dataclass
class WalkForwardConfig:
    mode: Mode = "rolling"
    is_sessions: int = 500        # ~2 years of trading days
    oos_sessions: int = 125       # ~6 months
    step_sessions: int = 125      # non-overlapping OOS by default
    min_trades_per_fold: int = 30


def _session_starts(sess: np.ndarray) -> np.ndarray:
    """Index of the first bar of each session, plus a trailing sentinel."""
    change = np.flatnonzero(np.diff(sess)) + 1
    return np.concatenate(([0], change, [len(sess)]))


def make_folds(sess: np.ndarray, cfg: WalkForwardConfig) -> list[Fold]:
    """Cut folds over the session index.

    Rolling drops the oldest sessions as it advances; anchored keeps the start
    fixed and lets in-sample grow. A trailing partial OOS window is dropped
    rather than reported short -- a fold tested on half the intended period is
    not comparable to the others.
    """
    starts = _session_starts(sess)
    n_sessions = len(starts) - 1
    if cfg.is_sessions < 1 or cfg.oos_sessions < 1 or cfg.step_sessions < 1:
        raise ValueError("walk-forward windows must be at least one session")

    folds: list[Fold] = []
    anchor = 0
    is_begin = 0
    idx = 0
    while True:
        is_finish = is_begin + cfg.is_sessions
        oos_finish = is_finish + cfg.oos_sessions
        if oos_finish > n_sessions:
            break
        begin = anchor if cfg.mode == "anchored" else is_begin
        folds.append(
            Fold(
                index=idx,
                is_start=int(starts[begin]),
                is_end=int(starts[is_finish]),
                oos_start=int(starts[is_finish]),
                oos_end=int(starts[oos_finish]),
            )
        )
        idx += 1
        is_begin += cfg.step_sessions
    return folds


def slice_bars(bars: dict[str, np.ndarray], start: int, end: int) -> dict[str, np.ndarray]:
    """Slice every parallel array. Order indices become slice-relative."""
    return {k: v[start:end] for k, v in bars.items() if isinstance(v, np.ndarray)}


def iter_folds(bars: dict[str, np.ndarray], cfg: WalkForwardConfig) -> Iterator[Fold]:
    yield from make_folds(bars["sess"], cfg)
