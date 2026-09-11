"""Parameter perturbation on the real walk-forward winner."""
import json, sys, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from engine.core.kernel import run_kernel
from engine.core.metrics import compute
from engine.core.simulator import ExecConfig
from engine.data.annotate import RTH_CLOSE_MIN, annotate
from engine.data.canonical import DEFAULT_INSTRUMENTS
from engine.data.loader import load
from engine.montecarlo.perturbation import perturb
from engine.walkforward.runner import objective_value
from strategies.reference import orb

NQ = DEFAULT_INSTRUMENTS["NQ"]
CFG = ExecConfig(slippage_ticks=1.0)

df = load("data/cache/NQ_1m_eth.parquet")
bars = annotate({k: df[k].to_numpy() for k in ("ts","open","high","low","close","volume")},
                flatten_minute=RTH_CLOSE_MIN)

d = json.loads(Path("runs/last_oos_trades.json").read_text())
folds = [f for f in d["folds"] if not f["disqualified"]]
# The winner is the parameter set the search chose most often across folds --
# a single fold's winner would be the very cherry-pick this test exists to expose.
modal = Counter(tuple(sorted(f["params"].items())) for f in folds).most_common(1)[0]
base = dict(modal[0])
print(f"most-chosen parameter set across {len(folds)} folds "
      f"(picked {modal[1]}x): {base}\n")

def evaluate(params):
    orders = orb.generate_orders(bars, NQ.tick_size, **params)
    return objective_value(compute(run_kernel(bars, orders, NQ, CFG)), "mar")

t0 = time.perf_counter()
r = perturb(evaluate, base, orb.DESCRIBE["params"], steps=2, objective="mar",
            vary=["or_minutes", "stop_ticks"])
el = time.perf_counter() - t0

print("=" * 74)
print(r.render())
print()
print(r.render_surface())
print(f"\n  {len(r.scores)} full-history simulations in {el:.1f}s "
      f"({el/len(r.scores):.2f}s each)")
