"""Performance metrics.

Drawdown and MAR are computed on the equity curve, not on the trade list, so an
open position's excursion counts against the strategy the way it would in the
account. Every metric returns NaN rather than a flattering default when it is
undefined -- a strategy with no losses has no profit factor, and reporting
`inf` or `0` for that would be a number the UI could accidentally rank on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class Metrics:
    n_trades: int
    net_pnl: float
    return_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    mar: float
    sharpe: float
    profit_factor: float
    expectancy: float
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    longest_losing_streak: int
    n_long: int
    n_short: int
    years: float

    def render(self) -> str:
        def num(x, w=12, d=2):
            return "—".rjust(w) if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:>{w},.{d}f}"
        return "\n".join(
            [
                f"  trades         {self.n_trades:>12,}   long {self.n_long:,} / short {self.n_short:,}",
                f"  net P&L        {num(self.net_pnl)}",
                f"  return         {num(self.return_pct)} %   over {self.years:.2f} yr",
                f"  max drawdown   {num(self.max_drawdown)}   ({num(self.max_drawdown_pct, 1)} %)",
                f"  MAR            {num(self.mar)}",
                f"  Sharpe         {num(self.sharpe)}",
                f"  profit factor  {num(self.profit_factor)}",
                f"  expectancy     {num(self.expectancy)}   per trade",
                f"  win rate       {num(self.win_rate)} %   avg win {num(self.avg_win, 1)}  avg loss {num(self.avg_loss, 1)}",
                f"  best / worst   {num(self.largest_win, 1)} / {num(self.largest_loss, 1)}",
                f"  longest losing streak  {self.longest_losing_streak}",
            ]
        )


def _drawdown(equity: np.ndarray) -> tuple[float, float]:
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    i = int(np.argmin(dd))
    return float(-dd[i]), float(-dd[i] / peak[i] * 100.0) if peak[i] else float("nan")


def compute(result, bar_seconds: int = 60) -> Metrics:
    trades = result.trades
    equity = np.asarray(result.equity, dtype="float64")
    start_eq = result.starting_equity

    if len(equity) == 0:
        equity = np.array([start_eq])

    span_s = (result.bar_ts[-1] - result.bar_ts[0]) if len(result.bar_ts) > 1 else 0
    years = span_s / SECONDS_PER_YEAR if span_s else 0.0

    pnl = np.array([t.net_pnl for t in trades], dtype="float64")
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]

    max_dd, max_dd_pct = _drawdown(equity)
    net = float(pnl.sum()) if len(pnl) else 0.0
    ret_pct = net / start_eq * 100.0 if start_eq else float("nan")

    # MAR: annualised return over max drawdown. Undefined without a drawdown.
    if max_dd > 0 and years > 0:
        mar = (net / start_eq / years * 100.0) / (max_dd / start_eq * 100.0)
    else:
        mar = float("nan")

    # Sharpe on per-bar equity changes, annualised by bar frequency.
    diffs = np.diff(equity)
    if len(diffs) > 1 and diffs.std(ddof=1) > 0:
        bars_per_year = SECONDS_PER_YEAR / bar_seconds
        sharpe = float(diffs.mean() / diffs.std(ddof=1) * math.sqrt(bars_per_year))
    else:
        sharpe = float("nan")

    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float("nan")

    streak = worst = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
        worst = max(worst, streak)

    return Metrics(
        n_trades=len(trades),
        net_pnl=net,
        return_pct=ret_pct,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        mar=mar,
        sharpe=sharpe,
        profit_factor=pf,
        expectancy=float(pnl.mean()) if len(pnl) else float("nan"),
        win_rate=float(len(wins) / len(pnl) * 100.0) if len(pnl) else float("nan"),
        avg_win=float(wins.mean()) if len(wins) else float("nan"),
        avg_loss=float(losses.mean()) if len(losses) else float("nan"),
        largest_win=float(wins.max()) if len(wins) else float("nan"),
        largest_loss=float(losses.min()) if len(losses) else float("nan"),
        longest_losing_streak=worst,
        n_long=sum(1 for t in trades if t.direction > 0),
        n_short=sum(1 for t in trades if t.direction < 0),
        years=years,
    )
