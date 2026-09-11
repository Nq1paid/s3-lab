"""Benchmark the line protocol against the in-process fast path on real data."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from engine.adapters.line_protocol import SubprocessStrategy
from engine.core.kernel import run_kernel
from engine.core.simulator import ExecConfig, Simulator
from engine.data.annotate import RTH_CLOSE_MIN, annotate
from engine.data.canonical import DEFAULT_INSTRUMENTS
from engine.data.loader import load
from strategies.reference import orb

NQ = DEFAULT_INSTRUMENTS["NQ"]
CLI = [sys.executable, str(Path(__file__).resolve().parents[1] /
                           "strategies" / "reference" / "orb_cli.py")]
PARAMS = {"or_minutes": 15, "buffer_ticks": 2, "stop_ticks": 60,
          "target_r": 3.0, "no_new_after": 840, "qty": 1}

df = load("data/cache/NQ_1m_eth.parquet")
full = annotate({k: df[k].to_numpy() for k in ("ts","open","high","low","close","volume")},
                flatten_minute=RTH_CLOSE_MIN)
N = int(__import__('sys').argv[1]) if len(__import__('sys').argv) > 1 else 400_000
bars = {k: v[:N] for k, v in full.items() if isinstance(v, np.ndarray)}
print(f"{N:,} real NQ bars\n")

cfg = ExecConfig(slippage_ticks=1.0)
run_kernel(bars, orb.generate_orders(bars, NQ.tick_size, **PARAMS), NQ, cfg)  # warm JIT

t0 = time.perf_counter()
res = run_kernel(bars, orb.generate_orders(bars, NQ.tick_size, **PARAMS), NQ, cfg)
t_fast = time.perf_counter() - t0
print(f"  in-process fast path   {t_fast:7.2f}s   {N/t_fast:>12,.0f} bars/s   "
      f"{res.n_trades} trades")

print(f"\n  {'batch':>7}  {'seconds':>8}  {'bars/s':>12}  {'msgs':>7}  {'vs fast':>8}")
sizes = (1, 5, 25, 100, 500, 2000) if N <= 60_000 else (100, 500, 2000, 10000)
for batch in sizes:
    c = ExecConfig(slippage_ticks=1.0, batch_size=batch)
    with SubprocessStrategy(CLI, PARAMS, NQ, timeout=300.0) as s:
        t0 = time.perf_counter()
        r = Simulator(NQ, c).run(bars, s)
        el = time.perf_counter() - t0
    print(f"  {batch:>7}  {el:>8.2f}  {N/el:>12,.0f}  "
          f"{int(np.ceil(N/batch)):>7,}  {el/t_fast:>7.0f}x")

print(f"\n  trades identical across all batch sizes: {r.n_trades == res.n_trades}")
