"""CME equity-index session model, and timezone inference from it.

The timezone of a vendor export cannot be trusted to a filename or to a single
global setting -- on this machine the 5-minute @NQ export is America/New_York
while the 1-minute ETH exports are America/Chicago. Every file is fingerprinted
independently.

Inference is done against the exchange session calendar rather than against a
volume profile. The busiest minute of an index-future day is the cash CLOSE, not
the open (ES averages ~49.9k contracts at 14:59 CT against ~12.7k at the 08:30
open), so a naive "peak volume is the open" heuristic picks the wrong anchor and
lands an hour off. Session boundaries are unambiguous; volume peaks are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EXCHANGE_TZ = "America/Chicago"

# CME Globex equity index, expressed in exchange time.
SUNDAY_OPEN = (17, 0)      # Sunday 17:00 CT reopen
DAILY_HALT = (16, 0)       # Mon-Thu 16:00-17:00 CT break; last bar stamped 15:59
DAILY_RESUME = (17, 0)
WEEK_CLOSE = (16, 0)       # Friday 16:00 CT
EARLY_CLOSE = (12, 15)     # holiday-eve close; last bar stamped 12:14
RTH_OPEN = (8, 30)         # 09:30 ET cash open
RTH_CLOSE = (15, 0)        # 16:00 ET cash close

# Candidate export timezones, most plausible first.
CANDIDATE_TZS = (
    "America/Chicago",
    "America/New_York",
    "UTC",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
)


@dataclass
class TimezoneEvidence:
    """Why a timezone was chosen. Surfaced in the UI for confirmation."""

    tz: str
    score: float
    anchor: str                 # "daily halt" or "RTH open"
    detail: str                 # human-readable observation backing the score
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{self.tz:<22} score {self.score:6.3f}   [{self.anchor}]  {self.detail}"


def _localize(naive: pd.Series, tz: str) -> pd.Series:
    """Interpret naive wall-clock stamps as `tz` and return UTC instants.

    DST-ambiguous and DST-nonexistent stamps become NaT rather than raising --
    a handful of dropped bars must not veto an otherwise correct candidate.
    """
    idx = pd.DatetimeIndex(naive)
    if tz == "UTC":
        return pd.Series(idx.tz_localize("UTC"))
    return pd.Series(idx.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")).dt.tz_convert("UTC")


def has_overnight_session(utc: pd.Series, n_dates: int) -> tuple[bool, int]:
    """Does this file contain the overnight session, i.e. a daily ~60m halt?

    An RTH-only export has no 16:00-17:00 CT break to measure, so the halt
    anchor is unavailable and a different one must be used. Discriminating on
    halts-per-date rather than a raw count keeps this independent of bar size
    and sample length: a real ETH file lands near 0.8, an RTH file near 0.002.
    """
    delta = utc.reset_index(drop=True).diff().dt.total_seconds().div(60)
    n_halts = int(((delta >= 55) & (delta <= 65)).sum())
    return (n_dates > 0 and n_halts / n_dates >= 0.5), n_halts


def _score_halt_anchor(utc: pd.Series, ct: pd.Series) -> tuple[float, str]:
    """Anchor on the daily 16:00-17:00 CT Globex break.

    Read in the correct zone, the last bar before every halt sits at 15:59 and
    the first after it at 17:00, consistently across DST. A fixed-offset
    misread such as UTC matches in one half of the year only, so requiring
    consistency across the whole sample is what discriminates.
    """
    delta = utc.reset_index(drop=True).diff().dt.total_seconds().div(60)
    is_halt = (delta >= 55) & (delta <= 65)
    if not is_halt.any():
        return 0.0, "no halt found"

    lb = ct.shift(1)[is_halt].dt.strftime("%H:%M")
    fa = ct[is_halt].dt.strftime("%H:%M")
    want_last, want_first = f"{DAILY_HALT[0] - 1:02d}:59", f"{DAILY_RESUME[0]:02d}:{DAILY_RESUME[1]:02d}"
    match = float(((lb == want_last) & (fa == want_first)).mean())

    sun = ct[ct.dt.dayofweek == 6]
    sunday = float((sun.groupby(sun.dt.date).min().dt.strftime("%H:%M") == want_first).mean()) if len(sun) else 0.0

    observed = f"{lb.mode().iat[0] if len(lb) else '-'}->{fa.mode().iat[0] if len(fa) else '-'}"
    detail = f"halt {observed} ({match:5.1%} of {int(is_halt.sum())}), sunday open {sunday:5.1%}"
    return 0.75 * match + 0.25 * sunday, detail


def _score_rth_anchor(ct: pd.Series) -> tuple[float, str]:
    """Anchor on the RTH open for files with no overnight session.

    The cash open is 08:30 CT and is the first bar of every regular session in
    an RTH-only export -- stable enough to anchor on (2,371 of 2,401 dates in
    the vendor's 5-minute file). Half-days and holidays supply the residual.

    Deliberately NOT anchored on peak volume: the busiest minute is the cash
    close, not the open, and anchoring there lands an hour off.
    """
    first = ct.groupby(ct.dt.date).min().dt.strftime("%H:%M")
    want_open = f"{RTH_OPEN[0]:02d}:{RTH_OPEN[1]:02d}"
    match = float((first == want_open).mean())
    modal = first.mode().iat[0] if len(first) else "-"
    return match, f"first bar of day {modal} ({match:5.1%} of {len(first)} dates)"


def score_timezone(naive: pd.Series, tz: str) -> TimezoneEvidence:
    """Score how well `naive` stamps fit the CME calendar if read as `tz`."""
    utc = _localize(naive, tz)
    ok = utc.notna()
    utc = utc[ok]
    if len(utc) < 100:
        return TimezoneEvidence(tz, 0.0, "none", "too few resolvable stamps")

    ct = utc.dt.tz_convert(EXCHANGE_TZ).reset_index(drop=True)
    utc = utc.reset_index(drop=True)

    overnight, _ = has_overnight_session(utc, ct.dt.date.nunique())
    if overnight:
        score, detail = _score_halt_anchor(utc, ct)
        anchor = "daily halt"
    else:
        score, detail = _score_rth_anchor(ct)
        anchor = "RTH open"

    notes: list[str] = []
    dropped = int((~ok).sum())
    if dropped:
        notes.append(f"{dropped} stamps unresolvable in this zone (DST)")
    return TimezoneEvidence(tz, score, anchor, detail, notes)


def detect_timezone(
    naive: pd.Series,
    candidates: tuple[str, ...] = CANDIDATE_TZS,
    sample: int = 400_000,
) -> list[TimezoneEvidence]:
    """Rank candidate timezones best-first. Never guesses silently.

    Returns every candidate with its evidence so the UI can show the runner-up
    and the margin. A thin or inconclusive margin is a prompt to the user, not
    something to resolve by picking the top row.
    """
    if len(naive) > sample:
        naive = naive.iloc[-sample:]
    out = [score_timezone(naive, tz) for tz in candidates]
    out.sort(key=lambda e: e.score, reverse=True)
    return out


def is_conclusive(ranked: list[TimezoneEvidence], min_score: float = 0.90, min_margin: float = 0.25) -> bool:
    """True only when the winner is both strong and clearly ahead."""
    if not ranked or ranked[0].score < min_score:
        return False
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    return (ranked[0].score - runner_up) >= min_margin


def session_date(ct: pd.Series) -> pd.Series:
    """Trade date: the Globex session starting 17:00 CT belongs to the NEXT day.

    A bar at 18:30 CT Sunday is part of Monday's session. Getting this wrong
    misassigns every overnight trade to the previous weekday and corrupts the
    day-of-week attribution slice.
    """
    shifted = ct + pd.Timedelta(hours=7)  # 17:00 -> 00:00 next day
    return shifted.dt.date


def is_rth(ct: pd.Series) -> pd.Series:
    """Regular trading hours in exchange time: 08:30 <= t < 15:00 CT."""
    mins = ct.dt.hour * 60 + ct.dt.minute
    return (mins >= RTH_OPEN[0] * 60 + RTH_OPEN[1]) & (mins < RTH_CLOSE[0] * 60 + RTH_CLOSE[1])
