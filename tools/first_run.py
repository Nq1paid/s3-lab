"""First end-to-end run on real data. Measures, it does not estimate."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from engine.core.kernel import run_kernel
from engine.core.metrics import compute
from engine.core.simulator import ExecConfig
from engine.data.annotate import annotate, RTH_CLOSE_MIN
from engine.data.canonical import Instrument
from engine.data.loader import load
from strategies.reference import orb

NQ = Instrument("NQ", "E-mini Nasdaq-100", 0.25, 5.0)

t0 = time.perf_counter()
bars = load("data/cache/NQ_1m_eth.parquet")
bars = {k: bars[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "volume")}
t_load = time.perf_counter() - t0
n = len(bars["ts"])

t0 = time.perf_counter()
bars = annotate(bars, flatten_minute=RTH_CLOSE_MIN)
t_ann = time.perf_counter() - t0

t0 = time.perf_counter()
orders = orb.generate_orders(bars, NQ.tick_size)
t_gen = time.perf_counter() - t0

cfg = ExecConfig(slippage_ticks=1.0, starting_equity=100_000.0)
run_kernel({k: bars[k] for k in ("ts","open","high","low","close","flatten")}, orders[:1], NQ, cfg)  # warm JIT

t0 = time.perf_counter()
res = run_kernel(bars, orders, NQ, cfg)
t_sim = time.perf_counter() - t0

m = compute(res, bar_seconds=60)
print("=" * 74)
print(f"  NQ 1-minute ETH   {n:,} bars   ORB defaults {orb.DEFAULTS}")
print("=" * 74)
print(m.render())
print()
print(f"  !! slippage only -- this engine models no commission or fees")
print()
print("  TIMING")
print(f"    parquet load       {t_load:7.2f}s")
print(f"    session annotate   {t_ann:7.2f}s")
print(f"    order generation   {t_gen:7.2f}s   {len(orders):,} orders")
print(f"    fill simulation    {t_sim:7.2f}s   {n/t_sim:,.0f} bars/s")
print(f"    END TO END         {t_ann+t_gen+t_sim:7.2f}s (excl. load)   {n/(t_ann+t_gen+t_sim):,.0f} bars/s")
from collections import Counter
print("\n  EXIT REASONS  ", dict(Counter(t.exit_reason for t in res.trades)))
