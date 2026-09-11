"""The contract: every implementation of a strategy must trade identically.

This is what makes "the engine does not care which language" a fact rather than
an aspiration. The in-process Python strategy is vectorised over whole arrays;
the CLI strategy sees bars in batches over a pipe and shares no code with it --
it does not import the engine at all. If they agree trade for trade, the
protocol is a real boundary.

"Identical" is defined as identical after canonical serialisation with fixed
decimal quantisation: prices to the tick, quantities as integers, timestamps as
epoch seconds. Raw float equality across runtimes is not achievable and is not
the contract.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.adapters.line_protocol import SubprocessStrategy  # noqa: E402
from engine.core.kernel import run_kernel  # noqa: E402
from engine.core.simulator import ExecConfig, Simulator  # noqa: E402
from engine.data.annotate import RTH_CLOSE_MIN, annotate  # noqa: E402
from engine.data.canonical import DEFAULT_INSTRUMENTS  # noqa: E402
from strategies.reference import orb  # noqa: E402

NQ = DEFAULT_INSTRUMENTS["NQ"]
ORB_CLI = [sys.executable, str(ROOT / "strategies" / "reference" / "orb_cli.py")]
ORB_JAR = ROOT / "strategies" / "reference" / "java" / "orb.jar"
ORB_JAVA = ["java", "-jar", str(ORB_JAR)]
CACHE = ROOT / "data" / "cache" / "NQ_1m_eth.parquet"

needs_java = pytest.mark.skipif(
    not ORB_JAR.exists() or shutil.which("java") is None,
    reason="orb.jar not built or no JRE on PATH",
)


def canonical(trades, tick: float) -> list[tuple]:
    """Quantise a trade log so two runtimes can be compared exactly."""
    return [
        (
            int(t.entry_ts), int(t.exit_ts), int(t.direction), int(t.qty),
            round(t.entry_price / tick), round(t.exit_price / tick),
            round(t.net_pnl, 2), t.exit_reason,
        )
        for t in trades
    ]


def slice_of_real_data(n_sessions: int = 60) -> dict:
    """A fixed slice of real NQ bars, aligned to session boundaries."""
    from engine.data.loader import load

    df = load(str(CACHE))
    bars = annotate(
        {k: df[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "volume")},
        flatten_minute=RTH_CLOSE_MIN,
    )
    starts = np.concatenate(([0], np.flatnonzero(np.diff(bars["sess"])) + 1))
    lo, hi = int(starts[5]), int(starts[5 + n_sessions])
    return {k: v[lo:hi] for k, v in bars.items() if isinstance(v, np.ndarray)}


needs_data = pytest.mark.skipif(not CACHE.exists(), reason="NQ cache not imported")

PARAMS = {"or_minutes": 15, "buffer_ticks": 2, "stop_ticks": 60,
          "target_r": 3.0, "no_new_after": 840, "qty": 1}


@needs_data
@pytest.mark.parametrize("batch_size", [250, 500, 1000])
def test_subprocess_matches_in_process_trade_for_trade(batch_size):
    """Batch size must not change a single trade -- it is a transport detail."""
    bars = slice_of_real_data()
    cfg = ExecConfig(slippage_ticks=1.0, batch_size=batch_size)

    in_proc = run_kernel(bars, orb.generate_orders(bars, NQ.tick_size, **PARAMS), NQ, cfg)

    with SubprocessStrategy(ORB_CLI, PARAMS, NQ, timeout=120.0) as s:
        sub = Simulator(NQ, cfg).run(bars, s)
        assert not s.rejected, f"orders rejected: {s.rejected[:3]}"

    a, b = canonical(in_proc.trades, NQ.tick_size), canonical(sub.trades, NQ.tick_size)
    assert len(a) > 20, "slice produced too few trades to be a meaningful check"
    assert len(a) == len(b), f"in-process {len(a)} trades, subprocess {len(b)}"
    for i, (x, y) in enumerate(zip(a, b)):
        assert x == y, f"trade {i} differs:\n  in-process {x}\n  subprocess {y}"


@needs_data
def test_both_paths_agree_on_net_pnl_to_the_cent():
    bars = slice_of_real_data()
    cfg = ExecConfig(slippage_ticks=1.0, batch_size=500)
    in_proc = run_kernel(bars, orb.generate_orders(bars, NQ.tick_size, **PARAMS), NQ, cfg)
    with SubprocessStrategy(ORB_CLI, PARAMS, NQ, timeout=120.0) as s:
        sub = Simulator(NQ, cfg).run(bars, s)
    assert round(in_proc.net_pnl, 2) == round(sub.net_pnl, 2)


@needs_data
def test_the_cli_strategy_shares_no_code_with_the_engine():
    """If it imported the engine, agreement would prove nothing."""
    src = (ROOT / "strategies" / "reference" / "orb_cli.py").read_text(encoding="utf-8")
    for banned in ("from engine", "import engine", "from strategies", "import numpy"):
        assert banned not in src, f"orb_cli.py imports {banned!r}"


# ---- third implementation: Java, over the same protocol ---------------------

@needs_data
@needs_java
def test_java_describes_the_same_schema_as_python():
    from engine.adapters.line_protocol import describe

    py = describe(ORB_CLI)
    jv = describe(ORB_JAVA)
    assert (jv.proto, jv.name) == (py.proto, py.name)
    assert jv.defaults == py.defaults, "defaults differ between implementations"
    assert {p["name"] for p in jv.params} == {p["name"] for p in py.params}
    for a, b in zip(sorted(py.params, key=lambda p: p["name"]),
                    sorted(jv.params, key=lambda p: p["name"])):
        assert a.get("step") == b.get("step"), f"{a['name']} step differs"


@needs_data
@needs_java
def test_java_matches_python_trade_for_trade():
    """The contract: three implementations, one trade log."""
    bars = slice_of_real_data()
    cfg = ExecConfig(slippage_ticks=1.0, batch_size=500)

    in_proc = run_kernel(bars, orb.generate_orders(bars, NQ.tick_size, **PARAMS), NQ, cfg)
    with SubprocessStrategy(ORB_CLI, PARAMS, NQ, timeout=180.0) as s:
        py_sub = Simulator(NQ, cfg).run(bars, s)
    with SubprocessStrategy(ORB_JAVA, PARAMS, NQ, timeout=180.0) as s:
        java = Simulator(NQ, cfg).run(bars, s)
        assert not s.rejected, f"java orders rejected: {s.rejected[:3]}"

    a = canonical(in_proc.trades, NQ.tick_size)
    b = canonical(py_sub.trades, NQ.tick_size)
    c = canonical(java.trades, NQ.tick_size)
    assert len(a) > 20
    assert len(a) == len(b) == len(c), (
        f"trade counts differ: in-process {len(a)}, python {len(b)}, java {len(c)}"
    )
    for i, (x, y, z) in enumerate(zip(a, b, c)):
        assert x == y == z, (
            f"trade {i} differs:\n"
            f"  in-process {x}\n"
            f"  python-sub {y}\n"
            f"  java       {z}"
        )
