"""Generate the HTML report from the run on disk."""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from engine.core.baseline import buy_and_hold
from engine.data.canonical import DEFAULT_INSTRUMENTS
from engine.data.loader import load
from engine.montecarlo.bootstrap import bootstrap_trades
from engine.report.html import render


def month(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def _breakeven(pnl) -> str:
    """Per-side cost that takes this result to zero. Shared with both UIs."""
    from engine.analysis.view import breakeven_per_side

    be = breakeven_per_side(pnl)
    return "none -- already losing" if be is None else f"${be:,.2f}/side"


def build(path="runs/last_oos_trades.json", log=True):
    d = json.loads(Path(path).read_text())
    pnl = np.asarray(d["net_pnl"], dtype="float64")
    eq = 100_000.0 + np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min())
    dd_pct = abs(dd) / 1000.0
    ret_pct = (eq[-1] - 100_000.0) / 1000.0
    start_ts, end_ts = d["entry_ts"][0], d["exit_ts"][-1]
    years = (end_ts - start_ts) / (365.25 * 24 * 3600)
    mar = (ret_pct / years) / dd_pct if dd_pct and years else float("nan")

    b = bootstrap_trades(pnl, iterations=10_000, method="resample", seed=42)
    p5 = float(np.percentile(b.final_equity, 5))

    nq = DEFAULT_INSTRUMENTS["NQ"]
    bars = load("data/cache/NQ_1m_eth.parquet", columns=("ts", "close"))
    from engine.core.baseline import InsufficientCoverage
    try:
        bh = buy_and_hold(bars["ts"].to_numpy(), bars["close"].to_numpy(),
                          start_ts, end_ts, nq.point_value, samples=len(eq))
        bh_ret = (bh[-1] - 100_000.0) / 1000.0
        corr = float(np.corrcoef(np.diff(eq), np.diff(bh))[0, 1])
        baseline_note = ""
    except InsufficientCoverage as exc:
        # An exported report is read without the engine to hand, so a wrong
        # baseline in it can never be questioned. Better to have none.
        bh, bh_ret, corr, baseline_note = None, None, None, str(exc)

    all_folds = d.get("folds", [])
    folds = [f for f in all_folds if not f["disqualified"]]
    wfes = [f["oos_mar"] / f["is_mar"] for f in folds if f["is_mar"]]
    scale = max((abs(f["oos_net"]) for f in folds), default=1.0) or 1.0
    stability = d.get("stability", {})
    worst_param, worst_frac = min(stability.items(), key=lambda kv: kv[1])

    per = max(1, len(pnl) // max(len(folds), 1))
    boundaries = [min(1.0, (i * per) / len(pnl)) for i in range(1, len(folds))]

    robustness, attribution = _robustness_and_attribution(d, pnl, b, folds)

    return {
        "robustness": robustness,
        "attribution": attribution,
        "strategy": "orb_v1", "run_id": "0147", "state": "done",
        "clock": datetime.now().strftime("%H:%M:%S"),
        "instrument": "NQ", "barsize": "1m",
        "view": "Curve", "views": ("Curve", "Drawdown", "Diverge", "Histogram"),
        "net_oos": f"{ret_pct:+.1f}%",
        "title": "Stitched equity",
        "subtitle": "Out-of-sample segments only, joined end to end.",
        "fold_filter": "All folds", "slice_filter": "None",
        "chart_series": ([(eq, "#F2F2F3", 1.6)] if bh is None
                         else [(eq, "#F2F2F3", 1.6), (bh, "#8E8E93", 1.2)]),
        "chart_baseline": 100_000.0,
        "chart_log": log,
        "boundaries": boundaries,
        "x_start": month(start_ts), "x_end": month(end_ts),
        "rail": {
            "heading": "All folds",
            "meta": f"{month(start_ts)} → {month(end_ts)}",
            "rows": [
                ("Max drawdown", f"-{dd_pct:.1f}%"),
                ("MAR", f"{mar:.2f}"),
                ("WF efficiency", f"{np.mean(wfes):.2f}"),
                ("OOS trades", f"{len(pnl):,}"),
                # The report is the copy that gets sent to someone else, so it
                # carries the number that lets a stranger judge the result
                # against their own broker rather than trusting ours.
                ("Break-even cost", _breakeven(pnl)),
                ("MC 5th pctile", f"${p5:,.0f}"),
                ("vs buy & hold", "--" if bh_ret is None else f"{ret_pct - bh_ret:+.0f} pp"),
                ("Corr to long", "--" if corr is None else f"{corr:+.2f}"),
            ],
            "note": "Before brokerage -- slippage only, no commission.",
        },
        "footnote": (
            f"{len(pnl):,} out-of-sample trades across {len(all_folds)} folds. "
            f"Buy & hold returned {bh_ret:+.0f}% over the same window; the chart is "
            "log-scaled so both curves stay readable. Folds under the minimum-trade "
            "threshold are excluded, not zero-filled. Winning parameters agree on "
            f"{worst_param} across only {worst_frac*100:.0f}% of folds."
        ),
        "table": {
            "columns": ("fold", "oos from", "or / stop", "is mar", "oos ret",
                        "oos mar", "wfe", "trades"),
            "rows": [
                {"index": f["index"], "oos_from": month(f.get("oos_start_ts") or start_ts),
                 "or": f["params"].get("or_minutes", 0),
                 "stop": f["params"].get("stop_ticks", 0),
                 "is_mar": f["is_mar"], "oos_ret": f["oos_net"] / 1000.0,
                 "oos_mar": f["oos_mar"],
                 "wfe": (f["oos_mar"] / f["is_mar"]) if f["is_mar"] else 0.0,
                 "oos_n": f["oos_n"], "spark": abs(f["oos_net"]) / scale}
                for f in folds
            ],
        },
    }


def _robustness_and_attribution(d, pnl, boot, folds):
    """All four Monte Carlo methods and the attribution slices, for the report.

    Methods 3 and 4 re-run the strategy, so they are summarised from the fast
    methods plus the stored fold spread rather than re-simulated on every
    export -- an export must not silently start an hour of computation. The
    report says which numbers came from where.
    """
    import numpy as np
    import pandas as pd

    from engine.analysis.attribution import DIMENSIONS, attribute, build_table
    from engine.montecarlo.bootstrap import bootstrap_trades
    from engine.montecarlo.perturbation import perturb

    shuffle = bootstrap_trades(pnl, iterations=10_000, method="shuffle", seed=7)
    dd = boot.bands(boot.max_drawdown)
    sh = shuffle.bands(shuffle.max_drawdown)

    robustness = [
        {"name": "Trade bootstrap (resample)", "iterations": "10,000",
         "headline": (f"final P5 {np.percentile(boot.final_equity, 5):,.0f} · "
                      f"P50 {np.percentile(boot.final_equity, 50):,.0f} · "
                      f"P95 {np.percentile(boot.final_equity, 95):,.0f}"),
         "verdict": f"risk of ruin {boot.risk_of_ruin:.2%}",
         "notes": [f"Observed max drawdown {boot.observed_max_dd:,.0f} against a "
                   f"simulated median of {dd[50]:,.0f} and a P95 of {dd[95]:,.0f}."]},
        {"name": "Trade bootstrap (shuffle)", "iterations": "10,000",
         "headline": f"max drawdown P50 {sh[50]:,.0f} · P95 {sh[95]:,.0f}",
         "verdict": "sequence risk",
         "notes": ["Final equity is identical in every shuffled path by "
                   "construction; read the drawdown and streak bands, not final."]},
    ]

    stability = d.get("stability", {})
    if stability:
        worst_param, worst_frac = min(stability.items(), key=lambda kv: kv[1])
        robustness.append({
            "name": "Parameter stability (across folds)",
            "iterations": f"{len(folds)} folds",
            "headline": f"{worst_param} agrees on {worst_frac:.0%} of folds",
            "verdict": "UNSTABLE" if worst_frac < 0.6 else "stable",
            "notes": ["Winning parameters that change every fold mean the strategy "
                      "was curve-fitted once per fold, not validated once."]})

    nets = [f["oos_net"] for f in folds]
    if nets:
        wins = sum(1 for x in nets if x > 0)
        robustness.append({
            "name": "Fold dispersion", "iterations": f"{len(nets)} folds",
            "headline": (f"{wins} of {len(nets)} profitable · worst {min(nets):,.0f} "
                         f"· best {max(nets):,.0f}"),
            "verdict": "wide" if max(nets) - min(nets) > 2 * abs(sum(nets)) else "narrow",
            "notes": []})

    tr = pd.DataFrame({k: d[k] for k in
                       ("entry_ts", "exit_ts", "direction", "net_pnl", "bars_held", "fold")})
    table = build_table(tr)
    attribution = []
    for dim in ("time_of_day", "day_of_week", "year", "direction", "duration"):
        a = attribute(table, dim)
        attribution.append({
            "dimension": dim,
            "title": dim.replace("_", " ").title(),
            "concentration": a.concentration,
            "slices": [{"key": s.key, "n_trades": s.n_trades, "net_pnl": s.net_pnl,
                        "expectancy": s.expectancy, "win_rate": s.win_rate,
                        "share": s.share, "significant": s.significant}
                       for s in a.slices],
        })
    del DIMENSIONS
    return robustness, attribution


if __name__ == "__main__":
    out = render(build(), "runs/report.html")
    print("wrote", out, f"({out.stat().st_size/1024:.1f} KB)")
