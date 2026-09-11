"""Data quality report.

Everything here is descriptive. Nothing is repaired silently -- in particular
absent bars are never forward-filled. A minute in which nothing traded has no
bar, and inventing one would manufacture fills that were never available. The
report says what is missing; the engine is built to cope with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .sessions import EXCHANGE_TZ


@dataclass
class Gap:
    start: str
    end: str
    hours: float


@dataclass
class QualityReport:
    n_bars: int
    first: str
    last: str
    years: float
    bar_seconds: int
    n_dates: int
    median_bars_per_date: float
    duplicate_timestamps: int
    out_of_order: int
    ohlc_violations: int
    non_positive_prices: int
    nan_cells: int
    zero_volume_bars: int
    session_minutes_present: int
    weekend_gaps: int
    halt_gaps: int
    other_gaps: int
    largest_gaps: list[Gap] = field(default_factory=list)
    roll_dates: list[str] = field(default_factory=list)
    price_low: float = 0.0
    price_high: float = 0.0
    has_overnight: bool = False
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Survives a round trip through asdict()/JSON, which flattens the
        # nested Gap dataclasses into plain dicts.
        self.largest_gaps = [Gap(**g) if isinstance(g, dict) else g for g in self.largest_gaps]

    def render(self) -> str:
        pct = self.zero_volume_bars / self.n_bars * 100 if self.n_bars else 0.0
        lines = [
            f"  bars                 {self.n_bars:>12,}",
            f"  span                 {self.first}  ->  {self.last}   ({self.years:.2f} yr)",
            f"  bar size             {self.bar_seconds // 60} min",
            f"  session              {'ETH (overnight present)' if self.has_overnight else 'RTH only'}"
            f"   {self.session_minutes_present}/1440 minutes of day present",
            f"  dates                {self.n_dates:>12,}   median {self.median_bars_per_date:.0f} bars/date",
            f"  price range          {self.price_low:,.2f}  ->  {self.price_high:,.2f}",
            "",
            f"  duplicate stamps     {self.duplicate_timestamps:>12,}",
            f"  out of order         {self.out_of_order:>12,}",
            f"  OHLC violations      {self.ohlc_violations:>12,}",
            f"  non-positive prices  {self.non_positive_prices:>12,}",
            f"  NaN cells            {self.nan_cells:>12,}",
            f"  zero-volume bars     {self.zero_volume_bars:>12,}   ({pct:.3f}%)",
            "",
            f"  gaps  weekend {self.weekend_gaps:,}   halt {self.halt_gaps:,}   other {self.other_gaps:,}",
        ]
        for g in self.largest_gaps:
            lines.append(f"        {g.start}  ->  {g.end}   {g.hours:.1f} h")
        if self.roll_dates:
            lines.append(f"  contract rolls       {len(self.roll_dates)} (CME calendar convention)"
                         f"   ({', '.join(self.roll_dates[:4])}{' ...' if len(self.roll_dates) > 4 else ''})")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


def roll_dates_for(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    """Quarterly roll dates from the CME calendar, not from price.

    Equity-index futures roll on the Thursday one week before the third-Friday
    expiry, in March, June, September and December. That is deterministic, so
    it is computed rather than inferred.

    Inferring rolls from price does not work on this data and is deliberately
    not attempted: an unadjusted splice jumps by roughly the quarterly cost of
    carry (~0.5-1%), which is the same size as an ordinary overnight move on
    NQ. Picking the largest gap in a roll window finds the biggest news gap of
    that week, and lands on a different weekday nearly every quarter -- an
    answer that looks precise and is not.

    The vendor's actual roll rule is unverified (it may be volume- or
    open-interest-triggered rather than calendar-fixed), so these dates are
    reported as the convention used, overridable in the UI.
    """
    out: list[str] = []
    for year in range(start.year, end.year + 1):
        for month in (3, 6, 9, 12):
            third_fri = next(
                d for d in range(15, 22) if pd.Timestamp(year, month, d).dayofweek == 4
            )
            roll = pd.Timestamp(year, month, third_fri) - pd.Timedelta(days=8)
            if start <= roll <= end:
                out.append(str(roll.date()))
    return out


def assess(df: pd.DataFrame, tz: str = EXCHANGE_TZ) -> QualityReport:
    """Build a quality report from a canonical frame (ts int64 epoch seconds)."""
    utc = pd.to_datetime(df["ts"], unit="s", utc=True)
    ct = utc.dt.tz_convert(tz)
    n = len(df)

    delta = utc.diff().dt.total_seconds()
    positive = delta[delta > 0]
    bar_seconds = int(positive.mode().iat[0]) if len(positive) else 60

    mins_of_day = ct.dt.hour * 60 + ct.dt.minute
    minutes_present = int(mins_of_day.nunique())

    gap_mins = delta.div(60)
    is_gap = gap_mins > (bar_seconds / 60)
    weekend = int((gap_mins >= 40 * 60).sum())
    halt = int((is_gap & (gap_mins <= 90)).sum())
    other = int((gap_mins > 90).sum()) - weekend

    largest: list[Gap] = []
    mid = gap_mins[(gap_mins > 90) & (gap_mins < 40 * 60)]
    for idx in mid.nlargest(5).index:
        pos = df.index.get_loc(idx)
        largest.append(
            Gap(
                start=str(ct.iloc[pos - 1]).replace("+00:00", ""),
                end=str(ct.iloc[pos]).replace("+00:00", ""),
                hours=round(float(gap_mins.loc[idx]) / 60, 1),
            )
        )

    dates = ct.dt.date
    ohlc_bad = int(
        ((df["high"] < df["low"])
         | (df["open"] > df["high"]) | (df["open"] < df["low"])
         | (df["close"] > df["high"]) | (df["close"] < df["low"])).sum()
    )
    has_overnight = minutes_present > 900

    warnings: list[str] = []
    if ohlc_bad:
        warnings.append(f"{ohlc_bad:,} bars violate OHLC ordering -- these are unusable, not repairable")
    dupes = int(df["ts"].duplicated().sum())
    if dupes:
        warnings.append(f"{dupes:,} duplicate timestamps")
    ooo = int((delta.dropna() < 0).sum())
    if ooo:
        warnings.append(f"{ooo:,} out-of-order timestamps")
    if not has_overnight:
        warnings.append("RTH-only file: overnight gaps cannot be modelled, stops may fill unrealistically")

    return QualityReport(
        n_bars=n,
        first=str(ct.iloc[0]),
        last=str(ct.iloc[-1]),
        years=round((utc.iloc[-1] - utc.iloc[0]).days / 365.25, 2),
        bar_seconds=bar_seconds,
        n_dates=int(dates.nunique()),
        median_bars_per_date=float(df.groupby(dates).size().median()),
        duplicate_timestamps=dupes,
        out_of_order=ooo,
        ohlc_violations=ohlc_bad,
        non_positive_prices=int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        nan_cells=int(df.isna().sum().sum()),
        zero_volume_bars=int((df["volume"] == 0).sum()),
        session_minutes_present=minutes_present,
        weekend_gaps=weekend,
        halt_gaps=halt,
        other_gaps=max(other, 0),
        largest_gaps=largest,
        roll_dates=roll_dates_for(ct.iloc[0].tz_localize(None), ct.iloc[-1].tz_localize(None)),
        price_low=float(df["low"].min()),
        price_high=float(df["high"].max()),
        has_overnight=has_overnight,
        warnings=warnings,
    )
