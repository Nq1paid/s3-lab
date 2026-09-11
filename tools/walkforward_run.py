"""Real walk-forward on real NQ data."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.simulator import ExecConfig
from engine.data.annotate import RTH_CLOSE_MIN
from engine.data.canonical import Instrument
from engine.walkforward.runner import build_grid, run_walkforward
from engine.walkforward.splitter import WalkForwardConfig
from strategies.reference import orb

NQ = Instrument("NQ", "E-mini Nasdaq-100", 0.25, 5.0)

grid = build_grid(orb.DESCRIBE["params"], overrides={
    "or_minutes":   [15, 30, 45, 60],
    "stop_ticks":   [20, 40, 60, 80],
    "target_r":     [1.0, 1.5, 2.0, 3.0],
    "buffer_ticks": [2], "no_new_after": [840], "qty": [1],
})

wf = WalkForwardConfig(mode="rolling", is_sessions=500, oos_sessions=125,
                       step_sessions=125, min_trades_per_fold=30)

def prog(done, total):
    print(f"    fold {done}/{total}", end="\r", flush=True)

if __name__ == "__main__":
    print(f"grid: {len(grid)} combos   objective: MAR   mode: {wf.mode}")
    r = run_walkforward(
        "data/cache/NQ_1m_eth.parquet", "strategies.reference.orb:generate_orders",
        NQ, grid, wf_cfg=wf, exec_cfg=ExecConfig(slippage_ticks=1.0),
        objective="mar", flatten_minute=RTH_CLOSE_MIN, progress=prog,
    )
    print(" " * 30, end="\r")
    print("=" * 78)
    print(f"  WALK-FORWARD   {len(r.folds)} folds   {r.grid_size} combos   "
          f"{len(r.folds)*r.grid_size:,} runs in {r.elapsed_s}s")
    print("=" * 78)
    for w in r.warnings:
        print(f"  ! {w}")
    print(f"\n{'FOLD':>4} {'OR':>4} {'STOP':>5} {'R':>4} {'IS MAR':>8} {'OOS MAR':>8} "
          f"{'WFE':>7} {'OOS N':>6} {'OOS P&L':>11}")
    for f in r.folds:
        if f.disqualified:
            print(f"{f.index:>4}  -- disqualified: {f.disqualified}")
            continue
        p = f.params
        print(f"{f.index:>4} {p['or_minutes']:>4} {p['stop_ticks']:>5} {p['target_r']:>4.1f} "
              f"{f.is_metrics.mar:>8.2f} {f.oos_metrics.mar:>8.2f} {f.wfe:>7.2f} "
              f"{f.oos_metrics.n_trades:>6} {f.oos_metrics.net_pnl:>11,.0f}")
    print("\n  STITCHED OUT-OF-SAMPLE")
    print(r.metrics.render())
    print("\n  PARAMETER STABILITY across qualified folds")
    for k, v in r.parameter_stability().items():
        print(f"    {k:<14} {v:>5.0%} agree on the modal value")

    # Cost sensitivity. The engine charges no commission, and with 1,800+ round
    # trips a real brokerage rate dominates the result, so show where the sign
    # flips instead of picking a number and presenting it as fact.
    print("\n  COST SENSITIVITY  (your own brokerage, per side, per contract)")
    n = r.metrics.n_trades
    print(f"    {'per side':>10} {'total cost':>12} {'net P&L':>12}   verdict")
    for per_side in (0.0, 1.0, 2.0, 3.0, 3.60, 5.0):
        total = per_side * 2 * n
        net = r.metrics.net_pnl - total
        print(f"    {per_side:>10,.2f} {total:>12,.0f} {net:>12,.0f}   "
              f"{'profitable' if net > 0 else 'LOSS'}")
    be = r.metrics.net_pnl / (2 * n) if n else 0.0
    print(f"\n    break-even cost is {be:,.2f} per side "
          f"({be*2:,.2f} round turn) across {n:,} trades")

    # --- Monte Carlo method 1 on the out-of-sample trades
    import numpy as np, json
    from engine.montecarlo.bootstrap import bootstrap_trades
    pnl = np.array([t.net_pnl for t in r.stitched.trades], dtype="float64")
    Path("runs").mkdir(exist_ok=True)
    Path("runs/last_oos_trades.json").write_text(json.dumps({
        "net_pnl": pnl.tolist(),
        "direction": [t.direction for t in r.stitched.trades],
        "entry_ts": [t.entry_ts for t in r.stitched.trades],
        "exit_ts": [t.exit_ts for t in r.stitched.trades],
        "exit_reason": [t.exit_reason for t in r.stitched.trades],
        "bars_held": [t.bars_held for t in r.stitched.trades],
        "fold": [t.fold for t in r.stitched.trades],
        "entry_price": [t.entry_price for t in r.stitched.trades],
        "folds": [
            {"index": f.index, "params": f.params, "disqualified": f.disqualified,
             "is_mar": f.is_metrics.mar, "oos_mar": f.oos_metrics.mar,
             "oos_net": f.oos_metrics.net_pnl, "oos_n": f.oos_metrics.n_trades,
             "oos_start_ts": (f.oos_bar_ts[0] if f.oos_bar_ts else 0)}
            for f in r.folds
        ],
        "stability": r.parameter_stability(),
    }))
    print()
    for method in ("resample", "shuffle"):
        t0 = time.perf_counter()
        b = bootstrap_trades(pnl, starting_equity=100_000.0, iterations=10_000,
                             method=method, ruin_fraction=0.5, seed=42)
        el = time.perf_counter() - t0
        print("=" * 78)
        print(b.render())
        print(f"  {b.iterations:,} iterations in {el:.2f}s")
