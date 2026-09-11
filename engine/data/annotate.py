"""Session annotation: turn canonical bars into arrays a strategy can reason with.

Computed once per dataset and cached alongside it. Doing this here rather than
inside the engine or a strategy means there is exactly one definition of "which
session is this bar in", so the engine, the strategy and the attribution slicer
cannot disagree about it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import EXCHANGE_TZ, RTH_CLOSE, RTH_OPEN

RTH_OPEN_MIN = RTH_OPEN[0] * 60 + RTH_OPEN[1]     # 510  = 08:30 CT
RTH_CLOSE_MIN = RTH_CLOSE[0] * 60 + RTH_CLOSE[1]  # 900  = 15:00 CT


def annotate(
    bars: dict[str, np.ndarray],
    tz: str = EXCHANGE_TZ,
    flatten_minute: int | None = None,
) -> dict[str, np.ndarray]:
    """Add minute-of-day, session id, RTH mask and optional flatten flags.

    `flatten_minute` marks the LAST bar of each session at or before that
    minute. It is the last bar, not the first one past the cutoff, so the
    flatten always lands on a bar that actually exists -- on a half-day, or in a
    thin overnight stretch where the cutoff minute never traded, a "first bar
    after" rule would silently skip the flatten entirely.
    """
    out = dict(bars)
    ct = pd.to_datetime(bars["ts"], unit="s", utc=True).tz_convert(tz)

    mod = (ct.hour * 60 + ct.minute).to_numpy().astype(np.int16)
    out["mod"] = mod

    # Trade date: the session opening 17:00 CT belongs to the next day.
    # Cast to day resolution explicitly rather than dividing an integer by a
    # nanosecond constant -- pandas may back the datetime with microseconds.
    sess = (ct + pd.Timedelta(hours=7)).tz_localize(None)
    out["sess"] = sess.to_numpy().astype("datetime64[D]").astype(np.int32)

    out["rth"] = ((mod >= RTH_OPEN_MIN) & (mod < RTH_CLOSE_MIN)).astype(np.int8)

    if flatten_minute is not None:
        flat = np.zeros(len(mod), np.int8)
        eligible = mod <= flatten_minute
        if eligible.any():
            df = pd.DataFrame({"sess": out["sess"], "i": np.arange(len(mod))})[eligible]
            flat[df.groupby("sess")["i"].max().to_numpy()] = 1
        out["flatten"] = flat
    return out


def session_bounds(sess: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First and last bar index of each session, in order."""
    change = np.flatnonzero(np.diff(sess)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change - 1, [len(sess) - 1]))
    return starts, ends
