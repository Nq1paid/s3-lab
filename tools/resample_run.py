"""Noise injection and block bootstrap on the real strategy."""
import json, sys, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from engine.core.kernel import run_kernel
from engine.core.simulator import ExecConfig
from engine.data.annotate import RTH_CLOSE_MIN, annotate
from engine.data.canonical import DEFAULT_INSTRUMENTS
from engine.data.loader import load
from engine.montecarlo.resample import run_resample
from strategies.reference import orb

NQ = DEFAULT_INSTRUMENTS["NQ"]
CFG = ExecConfig(slippage_ticks=1.0)

df = load("data/cache/NQ_1m_eth.parquet")
bars = annotate({k: df[k].to_numpy() for k in ("ts","open","high","low","close","volume")},
                flatten_minute=RTH_CLOSE_MIN)

d = json.loads(Path("runs/last_oos_trades.json").read_text())
folds = [f for f in d["folds"] if not f["disqualified"]]
base = dict(Counter(tuple(sorted(f["params"].items())) for f in folds).most_common(1)[0][0])
print(f"params: {base}\n")

def evaluate(b):
    orders = orb.generate_orders(b, NQ.tick_size, **base)
    return run_kernel(b, orders, NQ, CFG).net_pnl

ITERS = 300
for method, kw in (("noise", {"price_ticks": 1.0, "shift_bars": 1}),
                   ("block", {"block_len": 0})):
    t0 = time.perf_counter()
    r = run_resample(evaluate, bars, method, iterations=ITERS, seed=11, **kw)
    el = time.perf_counter() - t0
    print("=" * 74)
    print(r.render())
    print(f"  {r.iterations} iterations in {el:.1f}s ({el/max(r.iterations,1):.2f}s each)")
    print()
