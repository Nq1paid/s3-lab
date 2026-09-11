"""Attribution on the real out-of-sample trades."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np, pandas as pd
from engine.analysis.attribution import DIMENSIONS, attribute, build_table
from engine.data.loader import load

d = json.loads(Path("runs/last_oos_trades.json").read_text())
tr = pd.DataFrame({k: d[k] for k in
                   ("entry_ts", "exit_ts", "direction", "net_pnl", "bars_held", "fold")})

# Realised volatility at entry: 14-bar ATR proxy from the cached bars.
bars = load("data/cache/NQ_1m_eth.parquet", columns=("ts", "high", "low", "close"))
ts = bars["ts"].to_numpy()
tr_range = (bars["high"].to_numpy() - bars["low"].to_numpy())
atr = pd.Series(tr_range).rolling(14, min_periods=1).mean().to_numpy()
idx = np.clip(np.searchsorted(ts, tr["entry_ts"].to_numpy()), 0, len(atr) - 1)

table = build_table(tr, atr=atr[idx])
print(f"{len(table):,} out-of-sample trades, net {table['net_pnl'].sum():,.0f}\n")

flagged = []
for dim in DIMENSIONS:
    a = attribute(table, dim)
    print("=" * 84)
    print(a.render())
    print()
    if a.concentration:
        flagged.append((dim, a.concentration))

print("=" * 84)
if flagged:
    print("CONCENTRATION WARNINGS")
    for dim, msg in flagged:
        print(f"  [{dim}] {msg}")
else:
    print("No single slice exceeds the 60% concentration threshold on any dimension.")
