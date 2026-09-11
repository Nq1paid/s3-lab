"""Self-test: prove the install works before the user ever opens the app.

Checks the things that actually break on a fresh machine -- missing wheels, a
Python without numba, an unreadable data cache -- rather than importing a
module and declaring success.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
results: list[tuple[str, str, str]] = []


def check(name: str, fn, required: bool = True) -> None:
    try:
        detail = fn() or ""
        results.append((OK, name, str(detail)))
    except Exception as exc:
        results.append((FAIL if required else WARN, name, f"{type(exc).__name__}: {exc}"))


def _python():
    v = sys.version_info
    if v < (3, 11):
        raise RuntimeError(f"need 3.11+, have {v.major}.{v.minor}")
    return f"{v.major}.{v.minor}.{v.micro}"


def _imports():
    import numpy, pandas, pyarrow, textual          # noqa: F401
    return f"numpy {numpy.__version__}, pandas {pandas.__version__}"


def _numba():
    import numba
    from engine.core.kernel import simulate         # noqa: F401
    return f"numba {numba.__version__}"


def _engine():
    import numpy as np
    from engine.core.kernel import run_kernel
    from engine.core.simulator import ExecConfig
    from engine.core.types import Order, OrderType, Side
    from engine.data.canonical import DEFAULT_INSTRUMENTS

    n = 200
    bars = {
        "ts": np.arange(1_500_000_000, 1_500_000_000 + n * 60, 60, dtype="int64"),
        "open": np.full(n, 15000.0), "high": np.full(n, 15010.0),
        "low": np.full(n, 14990.0), "close": np.full(n, 15000.0),
    }
    res = run_kernel(bars, [Order(Side.BUY, OrderType.MARKET, 1, bar=0)],
                     DEFAULT_INSTRUMENTS["NQ"], ExecConfig())
    if res.n_trades != 1:
        raise RuntimeError(f"expected 1 trade, got {res.n_trades}")
    return "fills, costs and accounting run"


def _data():
    cache = sorted((ROOT / "data" / "cache").glob("*.parquet"))
    if not cache:
        raise RuntimeError("no normalised data yet -- drop CSVs in data/raw and import")
    from engine.data.loader import load
    df = load(str(cache[0]), columns=("ts",))
    return f"{len(cache)} cached series, newest has {len(df):,} bars"


def _tui():
    from tui.app import S3Lab                        # noqa: F401
    return "imports and themes cleanly"


def _java():
    import subprocess
    out = subprocess.run(["javac", "-version"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError("javac not on PATH")
    return (out.stdout or out.stderr).strip()


def _java_strategy():
    import subprocess
    jar = ROOT / "strategies" / "reference" / "java" / "orb.jar"
    if not jar.exists():
        raise RuntimeError("orb.jar not built -- run strategies/reference/java/build.bat")
    out = subprocess.run(["java", "-jar", str(jar), "--describe"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or '"proto"' not in out.stdout:
        raise RuntimeError("orb.jar did not answer --describe")
    return "reference jar answers --describe"




def main() -> int:
    t0 = time.perf_counter()
    print()
    print("  S3 LAB  self-test")
    print("  " + "-" * 62)
    check("python version", _python)
    check("core packages", _imports)
    check("numba kernel", _numba)
    check("engine end to end", _engine)
    check("tui", _tui)
    check("data cache", _data, required=False)
    check("java toolchain", _java, required=False)
    check("java strategy", _java_strategy, required=False)

    for status, name, detail in results:
        print(f"  [{status}] {name:<22} {detail}")
    print("  " + "-" * 62)

    failed = [r for r in results if r[0] == FAIL]
    warned = [r for r in results if r[0] == WARN]
    print(f"  {len(results) - len(failed) - len(warned)} passed, "
          f"{len(warned)} warnings, {len(failed)} failed "
          f"({time.perf_counter() - t0:.1f}s)")
    if warned and not failed:
        print("  Warnings are optional components. The app will run.")
    if failed:
        print("  The app will not run until the failures above are fixed.")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
