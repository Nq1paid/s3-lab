"""Timezone detection against real vendor exports.

The two files below came from the same vendor on the same machine and are in
DIFFERENT timezones. A detector that returns one answer for both is broken.
"""

import os
import pathlib
import sys
import time

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from engine.data.sessions import detect_timezone, is_conclusive  # noqa: E402

#: Cases are (path relative to data/raw, expected timezone). They are the two
#: exports this detector was built against -- same vendor, same machine,
#: different timezones -- plus a 5-minute export that lives outside the
#: project. Paths are relative and missing files are skipped, because this
#: file ships to other machines and hardcoding one person's folders would make
#: it fail everywhere else.
CASES = [
    ("@NQ - 1 min - ETH.csv", "America/Chicago"),
    ("@ES - 1 min - ETH.csv", "America/Chicago"),
]

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"

#: An extra export outside the project, given as an absolute path in the
#: S3LAB_EXTRA_TZ_CASE environment variable as "path=Area/City".
extra = os.environ.get("S3LAB_EXTRA_TZ_CASE", "")
if "=" in extra:
    path, _, tz = extra.rpartition("=")
    CASES.append((path, tz))

failures = 0
for path, expected in CASES:
    full = pathlib.Path(path)
    if not full.is_absolute():
        full = RAW / path
    if not full.exists():
        print(f"  skip     {path} -- not on this machine")
        continue
    t0 = time.perf_counter()
    ts = pd.read_csv(full, header=None, usecols=[0], names=["ts"], dtype=str)["ts"]
    naive = pd.to_datetime(ts, format="%Y%m%d %H%M%S")

    ranked = detect_timezone(naive)
    winner = ranked[0]
    elapsed = time.perf_counter() - t0

    name = path.rsplit("\\", 1)[-1]
    ok = winner.tz == expected
    failures += not ok
    print(f"\n{name}   ({len(naive):,} bars, {elapsed:.1f}s)")
    print(f"  expected {expected}")
    for e in ranked:
        mark = " <-- chosen" if e is winner else ""
        print(f"     {e.summary()}{mark}")
    print(f"  conclusive: {is_conclusive(ranked)}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}  got {winner.tz}")

print(f"\n{'=' * 70}\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
