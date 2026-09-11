"""One numeric summary of a walk-forward, shared by every front end.

The terminal draws it in braille, the browser draws it in SVG, and the HTML
report writes it to a file. All three read this. Two surfaces that computed
their own MAR would eventually disagree, and a backtester that reports two
different answers for the same run is worse than one that reports none.

Everything here is a plain number or a list of numbers -- no Rich, no colours,
no formatting. Presentation belongs to whoever is drawing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

STARTING_EQUITY = 100_000.0


def breakeven_per_side(pnl) -> float | None:
    """Per-side cost, in dollars per contract, that takes net P&L to zero.

    This engine charges no commission, so every net figure is before brokerage
    and the reader has to apply their own rate. This is the number that makes
    that possible without arithmetic: if a broker charges more than this per
    side, the strategy loses money at that desk. Two sides per round trip.

    None for a strategy that is already losing -- it has no break-even cost,
    and saying so is more use than a negative number.
    """
    n = len(pnl)
    if n == 0:
        return None
    net = float(np.sum(pnl))
    return net / (2 * n) if net > 0 else None


def month(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def summarise(path: str = "runs/last_oos_trades.json", *,
              iterations: int = 10_000, seed: int = 42) -> dict:
    """Every headline number for a completed walk-forward, computed once."""
    from engine.core.baseline import InsufficientCoverage, buy_and_hold
    from engine.data.canonical import DEFAULT_INSTRUMENTS
    from engine.data.loader import load
    from engine.montecarlo.bootstrap import bootstrap_trades

    d = json.loads(Path(path).read_text(encoding="utf-8"))
    pnl = np.asarray(d["net_pnl"], dtype="float64")
    equity = STARTING_EQUITY + np.cumsum(pnl)

    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    dd = float(drawdown.min())
    dd_pct = abs(dd) / STARTING_EQUITY * 100.0
    ret_pct = (equity[-1] - STARTING_EQUITY) / STARTING_EQUITY * 100.0
    start_ts, end_ts = int(d["entry_ts"][0]), int(d["exit_ts"][-1])
    years = (end_ts - start_ts) / (365.25 * 24 * 3600)
    mar = (ret_pct / years) / dd_pct if dd_pct and years else float("nan")

    boot = bootstrap_trades(pnl, iterations=iterations, method="resample", seed=seed)
    final = np.asarray(boot.final_equity, dtype="float64")

    # The baseline is required beside every result, but a baseline computed
    # over a shorter window than the run is worse than none: it reads as the
    # comparison and is not. If the cached series cannot cover the run, say so
    # and let the UI leave the row empty.
    nq = DEFAULT_INSTRUMENTS["NQ"]
    bars = load("data/cache/NQ_1m_eth.parquet", columns=("ts", "close"))
    try:
        bh = buy_and_hold(bars["ts"].to_numpy(), bars["close"].to_numpy(),
                          start_ts, end_ts, nq.point_value, samples=len(equity))
        bh_ret = (bh[-1] - STARTING_EQUITY) / STARTING_EQUITY * 100.0
        # A strategy highly correlated to simply being long is not a strategy,
        # so the correlation travels next to the excess return, never buried.
        corr = float(np.corrcoef(np.diff(equity), np.diff(bh))[0, 1])
        baseline_note = ""
    except InsufficientCoverage as exc:
        bh, bh_ret, corr = None, None, None
        baseline_note = str(exc)

    all_folds = d.get("folds", [])
    folds = [f for f in all_folds if not f["disqualified"]]
    nets = [f["oos_net"] for f in folds]
    wfes = [f["oos_mar"] / f["is_mar"] for f in folds if f["is_mar"]]
    stability = d.get("stability", {})
    worst_param, worst_frac = (
        min(stability.items(), key=lambda kv: kv[1]) if stability else ("n/a", 1.0)
    )

    per_fold = max(1, len(pnl) // max(len(folds), 1))
    boundaries = [min(1.0, (i * per_fold) / len(pnl)) for i in range(1, len(folds))]

    return {
        "n_trades": int(len(pnl)),
        "net_pnl": float(np.sum(pnl)),
        "return_pct": float(ret_pct),
        "max_drawdown": dd,
        "max_drawdown_pct": float(dd_pct),
        "mar": float(mar),
        "years": float(years),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "span": month(start_ts) + " → " + month(end_ts),
        "wfe_mean": float(np.mean(wfes)) if wfes else float("nan"),
        "breakeven_per_side": breakeven_per_side(pnl),
        "buy_hold_return_pct": None if bh_ret is None else float(bh_ret),
        "excess_pp": None if bh_ret is None else float(ret_pct - bh_ret),
        "corr_to_long": corr,
        "baseline_note": baseline_note,
        "mc_p5": float(np.percentile(final, 5)),
        "mc_p50": float(np.percentile(final, 50)),
        "mc_p95": float(np.percentile(final, 95)),
        "mc_iterations": int(iterations),
        "n_folds_total": len(all_folds),
        "n_folds_qualified": len(folds),
        "n_folds_profitable": sum(1 for x in nets if x > 0),
        "worst_param": worst_param,
        "worst_param_frac": float(worst_frac),
        "median_fold": int(np.argsort(nets)[len(nets) // 2]) if nets else 0,
        "stability": stability,
        # ---- series, for whoever is drawing
        "equity": [float(x) for x in equity],
        "drawdown": [float(x) for x in drawdown],
        "buy_hold": None if bh is None else [float(x) for x in bh],
        "final_equity": [float(x) for x in final],
        "fold_boundaries": boundaries,
        "folds": [
            {"index": f["index"],
             "oos_from": month(int(f.get("oos_start_ts") or start_ts)),
             "params": f["params"],
             "is_mar": f["is_mar"],
             "oos_mar": f["oos_mar"],
             "oos_net": f["oos_net"],
             "oos_n": f["oos_n"],
             "oos_ret_pct": f["oos_net"] / STARTING_EQUITY * 100.0,
             "wfe": (f["oos_mar"] / f["is_mar"]) if f["is_mar"] else 0.0}
            for f in folds
        ],
    }
