"""Where the edge actually lives.

On intraday futures the usual discovery is that the result comes from one
narrow condition and the rest of the trades are noise around it. This slices
out-of-sample trades so that is visible rather than inferred.

Two rules protect the conclusions:

- A slice with fewer than `MIN_TRADES` is marked not significant and is
  excluded from anything the app asserts. It is still shown, because hiding it
  would misrepresent where the trades went.
- If any single slice holds more than `CONCENTRATION` of net P&L, that is said
  in plain language at the top of the screen. Concentration is not an edge, and
  it is the single most useful thing this feature can report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_TRADES = 30
#: Marker column proving a table went through build_table.
PREPARED = "_sliced"
CONCENTRATION = 0.60
#: A slice holding this share of ALL trades is the whole book, not a slice.
WHOLE_BOOK = 0.90
SECONDS_PER_YEAR = 365.25 * 24 * 3600

DIMENSIONS = (
    "time_of_day", "day_of_week", "year", "month", "vol_regime",
    "direction", "session", "duration", "fold",
)


@dataclass
class Slice:
    key: str
    n_trades: int
    net_pnl: float
    expectancy: float
    win_rate: float
    mar: float
    share: float
    significant: bool

    def render(self) -> str:
        mark = " " if self.significant else "·"
        mar = "     —" if np.isnan(self.mar) else f"{self.mar:>6.2f}"
        if not np.isnan(self.mar) and abs(self.mar) >= 1000:
            mar = f"{self.mar:>6.0f}"
        return (f"  {mark} {self.key:<18} {self.n_trades:>6}  {self.net_pnl:>11,.0f}  "
                f"{self.expectancy:>9,.1f}  {self.win_rate:>6.1f}%  {mar}  "
                f"{self.share:>7.1%}")


@dataclass
class Attribution:
    dimension: str
    slices: list[Slice]
    total_net: float
    n_trades: int
    concentration: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def significant(self) -> list[Slice]:
        return [s for s in self.slices if s.significant]

    def render(self) -> str:
        head = (f"  {'':2}{'SLICE':<18} {'N':>6}  {'NET P&L':>11}  {'EXPECT':>9}  "
                f"{'WIN':>7}  {'MAR':>6}  {'SHARE':>7}")
        rows = [f"  {self.dimension.replace('_', ' ').upper()}", "", head, ""]
        rows += [s.render() for s in self.slices]
        rows.append("")
        rows.append(f"    · = fewer than {MIN_TRADES} trades, not significant, "
                    "excluded from any conclusion")
        if self.concentration:
            rows += ["", f"  ** {self.concentration}"]
        for n in self.notes:
            rows.append(f"  ! {n}")
        return "\n".join(rows)


def _mar(pnl: np.ndarray, first_ts: float, last_ts: float, starting_equity: float) -> float:
    """MAR on the slice's own equity path. NaN when it has no drawdown.

    Annualised over the slice's CALENDAR span, not the summed duration of its
    trades. Summing durations gives eight trades of thirty minutes a span of
    four hours, and dividing an annual return by 0.0005 years produced MAR
    figures in the tens of thousands -- a number that looks like a finding and
    is an artefact of the denominator.
    """
    if len(pnl) < 2:
        return float("nan")
    equity = starting_equity + np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = float((equity - peak).min())
    years = (last_ts - first_ts) / SECONDS_PER_YEAR
    if dd >= 0 or years <= 0:
        return float("nan")
    total_return = float(pnl.sum()) / starting_equity * 100.0
    return (total_return / years) / (abs(dd) / starting_equity * 100.0)


def build_table(
    trades: pd.DataFrame,
    tz: str = "America/Chicago",
    atr: np.ndarray | None = None,
) -> pd.DataFrame:
    """Add the slice keys to a trade table.

    Expects columns: entry_ts, exit_ts, direction, net_pnl, bars_held, fold.
    `atr` is the realised volatility at each trade's entry, if available.
    """
    df = trades.copy()
    df[PREPARED] = True
    ct = pd.to_datetime(df["entry_ts"], unit="s", utc=True).dt.tz_convert(tz)

    mins = ct.dt.hour * 60 + ct.dt.minute
    # 30-minute buckets across RTH; everything else is one overnight bucket, so
    # thin overnight activity is not split into a dozen unusable slices.
    rth = (mins >= 510) & (mins < 900)
    bucket = ((mins // 30) * 30).astype(int)
    df["time_of_day"] = np.where(
        rth, [f"{b // 60:02d}:{b % 60:02d}" for b in bucket], "overnight"
    )
    df["day_of_week"] = ct.dt.day_name().str.slice(0, 3)
    df["year"] = ct.dt.year.astype(str)
    df["month"] = ct.dt.strftime("%Y-%m")
    df["session"] = np.where(rth, "RTH", "ETH")
    df["direction"] = np.where(df["direction"] > 0, "long", "short")
    df["fold"] = df["fold"].map(lambda f: f"fold {int(f):02d}" if f >= 0 else "unassigned")

    held = df["bars_held"].to_numpy()
    edges = [0, 5, 15, 30, 60, 120, np.inf]
    names = ["0-5", "5-15", "15-30", "30-60", "60-120", "120+"]
    df["duration"] = pd.cut(held, bins=edges, labels=names, right=False).astype(str)

    if atr is not None and len(atr) == len(df):
        # Five equal-population buckets, so each carries a comparable number of
        # trades rather than being split on round ATR numbers nobody chose.
        try:
            df["vol_regime"] = pd.qcut(
                atr, 5, labels=["v1 low", "v2", "v3", "v4", "v5 high"]
            ).astype(str)
        except ValueError:
            df["vol_regime"] = "unavailable"
    else:
        df["vol_regime"] = "unavailable"
    return df


def attribute(
    table: pd.DataFrame,
    dimension: str,
    starting_equity: float = 100_000.0,
) -> Attribution:
    """Slice out-of-sample trades along one dimension."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; expected one of {DIMENSIONS}")
    if PREPARED not in table.columns:
        # A raw trade table already has a numeric `direction` column, so
        # checking only for the dimension name lets an unprepared table through
        # and it groups silently by +1/-1 instead of long/short.
        raise ValueError("trade table is not prepared; call build_table first")
    if dimension not in table.columns:
        raise ValueError(f"table has no column {dimension!r}; call build_table first")

    total = float(table["net_pnl"].sum())
    slices: list[Slice] = []
    for key, grp in table.groupby(dimension, sort=True):
        pnl = grp["net_pnl"].to_numpy(dtype="float64")
        first_ts = float(grp["entry_ts"].min())
        last_ts = float(grp["exit_ts"].max())
        wins = int((pnl > 0).sum())
        # Share is signed against the total, so a losing slice inside a winning
        # book reads as a negative share rather than a positive contribution.
        share = float(pnl.sum() / total) if total else float("nan")
        slices.append(
            Slice(
                key=str(key), n_trades=len(pnl), net_pnl=float(pnl.sum()),
                expectancy=float(pnl.mean()) if len(pnl) else float("nan"),
                win_rate=wins / len(pnl) * 100.0 if len(pnl) else float("nan"),
                mar=_mar(pnl, first_ts, last_ts, starting_equity), share=share,
                significant=len(pnl) >= MIN_TRADES,
            )
        )
    slices.sort(key=lambda s: s.net_pnl, reverse=True)

    notes: list[str] = []
    concentration = ""
    if total > 0:
        # The warning IS a conclusion, so only a significant slice may raise it.
        # Letting a 21-trade month trigger it would contradict the rule stated
        # two lines above it in the same report.
        candidates = [s for s in slices if s.significant]
        top = max(candidates, key=lambda s: s.net_pnl, default=None)
        if top is not None and top.n_trades >= WHOLE_BOOK * len(table):
            # A slice holding nearly every trade is the book, not a
            # concentration within it -- an RTH-only strategy is not
            # "concentrated in RTH", it simply has no other sessions to
            # compare against.
            notes.append(
                f"{top.key} holds {top.n_trades / len(table):.0%} of all trades, so this "
                "dimension does not split the book and no concentration can be assessed"
            )
        elif top is not None and top.share > CONCENTRATION:
            where = f"{dimension.replace('_', ' ')} = {top.key}, {top.n_trades} trades"
            if top.share > 1.0:
                concentration = (
                    f"One slice ({where}) makes {top.net_pnl:,.0f} against a total of "
                    f"{total:,.0f} — MORE than the entire result. Every other slice "
                    "combined is a net loss, so this is not an edge with noise around "
                    "it, it is one condition carrying a book that otherwise loses."
                )
            else:
                concentration = (
                    f"{top.share:.0%} of net P&L comes from a single slice ({where}). "
                    "That is concentration, not an edge — the rest of the book is noise "
                    "around it, and the result depends on that one condition repeating."
                )
    else:
        notes.append(
            "total net P&L is not positive, so contribution shares describe how the "
            "loss is distributed, not where an edge lives"
        )
    if not any(s.significant for s in slices):
        notes.append(f"no slice reaches {MIN_TRADES} trades; nothing here supports a conclusion")

    return Attribution(
        dimension=dimension, slices=slices, total_net=total,
        n_trades=int(len(table)), concentration=concentration, notes=notes,
    )
