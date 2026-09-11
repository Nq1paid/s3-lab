"""The numba kernel must produce the reference simulator's trade log exactly.

This is the only reason to trust the fast path. It is also the in-process
fast-path equivalence proof the spec asks for: same bars, same orders, same
money, one implementation readable and one fast.

Randomised across many seeds rather than a handful of hand-built cases, because
the interesting disagreements live in combinations nobody thinks to write down --
a gap through a stop on the same bar a bracket fires, a reversal landing on a
partial close, an order that stays unfilled for hundreds of bars.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.kernel import ReplayStrategy, run_kernel  # noqa: E402
from engine.core.simulator import ExecConfig, Simulator  # noqa: E402
from engine.core.types import Order, OrderType, Side  # noqa: E402
from engine.data.canonical import Instrument  # noqa: E402

NQ = Instrument("NQ", "test", 0.25, 5.0)
TICK = 0.25


def random_bars(rng: np.random.Generator, n: int, start: float = 15000.0):
    """Tick-rounded random walk with realistic bar structure."""
    steps = rng.normal(0, 2.0, n).cumsum()
    close = np.round((start + steps) / TICK) * TICK
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] + np.round(rng.normal(0, 0.5, n - 1) / TICK) * TICK
    span = np.abs(rng.normal(0, 3.0, n)) + TICK
    high = np.maximum(open_, close) + np.round(span / TICK) * TICK
    low = np.minimum(open_, close) - np.round(span / TICK) * TICK
    return {
        "ts": np.arange(1_500_000_000, 1_500_000_000 + n * 60, 60, dtype="int64"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(1, 1000, n).astype("int64"),
    }


def random_orders(rng: np.random.Generator, bars: dict, n_orders: int) -> list[Order]:
    n = len(bars["ts"])
    out = []
    for _ in range(n_orders):
        bar = int(rng.integers(0, n - 2))
        ref = float(bars["close"][bar])
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        otype = [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP][int(rng.integers(0, 3))]
        price = None
        if otype is not OrderType.MARKET:
            offset = np.round(rng.normal(0, 8.0) / TICK) * TICK
            price = ref + offset
        sl = tp = None
        if rng.random() < 0.6:
            dist = np.round(abs(rng.normal(20, 10)) / TICK) * TICK + TICK
            sl = ref - dist if side is Side.BUY else ref + dist
            tp = ref + dist * 1.5 if side is Side.BUY else ref - dist * 1.5
        out.append(
            Order(side, otype, int(rng.integers(1, 4)), bar=bar,
                  price=price, stop_loss=sl, take_profit=tp)
        )
    return out


def compare(ref, fast, seed):
    assert len(fast.trades) == len(ref.trades), (
        f"seed {seed}: kernel produced {len(fast.trades)} trades, reference {len(ref.trades)}"
    )
    for i, (a, b) in enumerate(zip(ref.trades, fast.trades)):
        ctx = f"seed {seed} trade {i}"
        assert (a.entry_bar, a.exit_bar) == (b.entry_bar, b.exit_bar), f"{ctx}: bars"
        assert (a.entry_ts, a.exit_ts) == (b.entry_ts, b.exit_ts), f"{ctx}: timestamps"
        assert (a.direction, a.qty) == (b.direction, b.qty), f"{ctx}: direction/qty"
        assert a.exit_reason == b.exit_reason, f"{ctx}: exit reason"
        assert a.entry_price == pytest.approx(b.entry_price, rel=1e-12), f"{ctx}: entry price"
        assert a.exit_price == pytest.approx(b.exit_price, rel=1e-12), f"{ctx}: exit price"
        assert a.net_pnl == pytest.approx(b.net_pnl, rel=1e-12), f"{ctx}: net"
        assert a.net_pnl == pytest.approx(b.net_pnl, rel=1e-12), f"{ctx}: net"
        assert a.mae == pytest.approx(b.mae, rel=1e-12), f"{ctx}: MAE"
        assert a.mfe == pytest.approx(b.mfe, rel=1e-12), f"{ctx}: MFE"
    assert len(fast.equity) == len(ref.equity)
    np.testing.assert_allclose(fast.equity, ref.equity, rtol=1e-12, atol=1e-9)


@pytest.mark.parametrize("seed", range(30))
def test_kernel_matches_reference(seed):
    rng = np.random.default_rng(seed)
    bars = random_bars(rng, 600)
    orders = random_orders(rng, bars, 40)
    cfg = ExecConfig(batch_size=int(rng.choice([100, 250, 500])))

    ref = Simulator(NQ, cfg).run(bars, ReplayStrategy(orders))
    fast = run_kernel(bars, orders, NQ, cfg)
    compare(ref, fast, seed)


def test_kernel_matches_reference_with_no_orders():
    rng = np.random.default_rng(99)
    bars = random_bars(rng, 200)
    cfg = ExecConfig()
    ref = Simulator(NQ, cfg).run(bars, ReplayStrategy([]))
    fast = run_kernel(bars, [], NQ, cfg)
    compare(ref, fast, 99)
    assert fast.n_trades == 0


def test_kernel_is_materially_faster():
    """Not a strict threshold -- a regression to parity means the JIT is off."""
    rng = np.random.default_rng(7)
    bars = random_bars(rng, 200_000)
    orders = random_orders(rng, bars, 4000)
    cfg = ExecConfig()

    run_kernel(bars, orders, NQ, cfg)          # warm the JIT

    t0 = time.perf_counter()
    ref = Simulator(NQ, cfg).run(bars, ReplayStrategy(orders))
    t_ref = time.perf_counter() - t0

    t0 = time.perf_counter()
    fast = run_kernel(bars, orders, NQ, cfg)
    t_fast = time.perf_counter() - t0

    compare(ref, fast, 7)
    speedup = t_ref / t_fast
    print(
        f"\n  reference {t_ref:7.3f}s  ({len(bars['ts'])/t_ref:12,.0f} bars/s)"
        f"\n  kernel    {t_fast:7.3f}s  ({len(bars['ts'])/t_fast:12,.0f} bars/s)"
        f"\n  speedup   {speedup:7.1f}x   trades {len(ref.trades):,}"
    )
    assert speedup > 5.0, f"kernel only {speedup:.1f}x faster -- JIT may not be active"


@pytest.mark.parametrize("seed", range(10))
def test_kernel_matches_reference_with_session_flatten(seed):
    """Flatten points must close and cancel identically in both engines."""
    rng = np.random.default_rng(1000 + seed)
    bars = random_bars(rng, 800)
    # A flatten every ~90 bars, standing in for a session close.
    flat = np.zeros(len(bars["ts"]), np.int8)
    flat[np.arange(89, len(flat), 90)] = 1
    bars["flatten"] = flat

    orders = random_orders(rng, bars, 60)
    cfg = ExecConfig(batch_size=int(rng.choice([100, 250, 500])))

    ref = Simulator(NQ, cfg).run(bars, ReplayStrategy(orders))
    fast = run_kernel(bars, orders, NQ, cfg)
    compare(ref, fast, 1000 + seed)
    assert any(t.exit_reason == "session_end" for t in ref.trades), "flatten never fired"
