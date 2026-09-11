"""Money-code tests: fill semantics, costs, P&L accounting, look-ahead.

Look-ahead is the failure mode that would make every downstream number
worthless, so it is tested directly rather than assumed from the design.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.simulator import ExecConfig, Simulator  # noqa: E402
from engine.core.types import ExitReason, Order, OrderType, Side  # noqa: E402
from engine.data.canonical import Instrument  # noqa: E402

NQ = Instrument("NQ", "test", tick_size=0.25, tick_value=5.0)   # $20 / point
TICK = 0.25


def make_bars(rows: list[tuple[float, float, float, float]], start_ts: int = 1_500_000_000):
    """rows are (open, high, low, close); one bar per minute."""
    n = len(rows)
    return {
        "ts": np.arange(start_ts, start_ts + n * 60, 60, dtype="int64"),
        "open": np.array([r[0] for r in rows], dtype="float64"),
        "high": np.array([r[1] for r in rows], dtype="float64"),
        "low": np.array([r[2] for r in rows], dtype="float64"),
        "close": np.array([r[3] for r in rows], dtype="float64"),
        "volume": np.ones(n, dtype="int64"),
    }


class Scripted:
    """Emits pre-programmed orders at given bar indices."""

    def __init__(self, script: dict[int, list[Order]]):
        self.script = script
        self.seen: list[tuple[int, int]] = []      # (batch start, batch end)

    def on_batch(self, batch, position, equity):
        self.seen.append((batch.start, batch.start + len(batch)))
        out = []
        for bar, orders in self.script.items():
            if batch.start <= bar < batch.start + len(batch):
                out.extend(orders)
        return out


def run(bars, script, cfg=None):
    sim = Simulator(NQ, cfg or ExecConfig())
    return sim.run(bars, Scripted(script))


# ----------------------------------------------------------- look-ahead

def test_order_never_fills_on_the_bar_it_was_decided():
    """The single most important guarantee in the engine."""
    bars = make_bars([(100.0, 101.0, 99.0, 100.5), (200.0, 201.0, 199.0, 200.5)])
    res = run(bars, {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)]})
    assert len(res.fills) >= 1
    entry = res.fills[0]
    assert entry.bar == 1, "market order decided on bar 0 must fill on bar 1"
    assert entry.price == pytest.approx(200.0 + TICK)


def test_strategy_is_never_shown_a_future_bar():
    bars = make_bars([(100.0, 101.0, 99.0, 100.0)] * 1200)
    strat = Scripted({})
    Simulator(NQ, ExecConfig(batch_size=500)).run(bars, strat)
    assert strat.seen == [(0, 500), (500, 1000), (1000, 1200)]
    assert all(end <= 1200 for _, end in strat.seen)


def test_order_tagged_with_a_bar_outside_its_batch_is_rejected():
    bars = make_bars([(100.0, 101.0, 99.0, 100.0)] * 10)
    res = run(bars, {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=9999)]})
    assert res.rejected and "rejected" in res.rejected[0]
    assert res.n_trades == 0


# ---------------------------------------------------------------- fills

def test_stop_buy_fills_at_stop_plus_slippage_when_traded_through():
    bars = make_bars([(100.0, 100.5, 99.5, 100.0), (100.0, 105.0, 99.0, 104.0)])
    res = run(bars, {0: [Order(Side.BUY, OrderType.STOP, 1, bar=0, price=102.0)]})
    assert res.fills[0].price == pytest.approx(102.0 + TICK)


def test_stop_buy_gapped_through_fills_at_the_open_not_the_stop():
    """A gap through the stop cannot fill at the stop -- it fills worse."""
    bars = make_bars([(100.0, 100.5, 99.5, 100.0), (110.0, 112.0, 109.0, 111.0)])
    res = run(bars, {0: [Order(Side.BUY, OrderType.STOP, 1, bar=0, price=102.0)]})
    assert res.fills[0].price == pytest.approx(110.0 + TICK)


def test_stop_sell_gapped_through_fills_at_the_open():
    bars = make_bars([(100.0, 100.5, 99.5, 100.0), (90.0, 91.0, 88.0, 89.0)])
    res = run(bars, {0: [Order(Side.SELL, OrderType.STOP, 1, bar=0, price=98.0)]})
    assert res.fills[0].price == pytest.approx(90.0 - TICK)


def test_stop_that_is_not_reached_does_not_fill():
    bars = make_bars([(100.0, 100.5, 99.5, 100.0), (100.0, 101.0, 99.0, 100.0)])
    res = run(bars, {0: [Order(Side.BUY, OrderType.STOP, 1, bar=0, price=105.0)]})
    assert res.fills == []


def test_limit_touched_exactly_does_not_fill():
    """Touching the limit is not trading through it."""
    bars = make_bars([(100.0, 100.5, 99.5, 100.0), (100.0, 101.0, 98.0, 99.0)])
    res = run(bars, {0: [Order(Side.BUY, OrderType.LIMIT, 1, bar=0, price=98.0)]})
    assert res.fills == [], "limit needs one tick of penetration, not a touch"


def test_limit_fills_when_price_trades_one_tick_beyond():
    bars = make_bars(
        [(100.0, 100.5, 99.5, 100.0), (100.0, 101.0, 97.75, 99.0), (99.0, 99.5, 98.5, 99.0)]
    )
    res = run(bars, {0: [Order(Side.BUY, OrderType.LIMIT, 1, bar=0, price=98.0)]})
    entry = res.fills[0]
    assert entry.bar == 1 and entry.side is Side.BUY
    assert entry.price == pytest.approx(98.0)


# -------------------------------------------------------------- brackets

def test_stop_loss_wins_when_one_bar_could_have_hit_both():
    bars = make_bars(
        [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),     # entry here
            (100.0, 120.0, 80.0, 100.0),     # hits both target and stop
        ]
    )
    res = run(
        bars,
        {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0, stop_loss=90.0, take_profit=110.0)]},
    )
    assert res.n_trades == 1
    assert res.trades[0].exit_reason == ExitReason.STOP_LOSS.value


def test_bracket_is_not_live_on_the_entry_bar():
    """The path after the fill is unknown, so the entry bar's range is not used."""
    bars = make_bars(
        [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 130.0, 70.0, 100.0),     # entry bar, huge range
            (100.0, 100.5, 99.5, 100.0),
        ]
    )
    res = run(
        bars,
        {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0, stop_loss=90.0, take_profit=110.0)]},
    )
    assert res.trades[0].exit_reason == ExitReason.END_OF_DATA.value


# --------------------------------------------------------------- P&L

def test_long_pnl_uses_point_value():
    inst = Instrument("NQ", "t", 0.25, 5.0)
    bars = make_bars(
        [
            (15000.0, 15000.0, 15000.0, 15000.0),
            (15000.0, 15000.0, 15000.0, 15000.0),   # entry at open + slip
            (15010.0, 15010.0, 15010.0, 15010.0),
        ]
    )
    sim = Simulator(inst, ExecConfig())
    res = sim.run(bars, Scripted({0: [Order(Side.BUY, OrderType.MARKET, 2, bar=0)]}))
    t = res.trades[0]
    assert t.entry_price == pytest.approx(15000.25)
    assert t.exit_price == pytest.approx(15010.0)
    # 9.75 points * $20 * 2 contracts. No commission is charged: this engine
    # models slippage only, which is already in the entry price above.
    assert t.net_pnl == pytest.approx(9.75 * 20.0 * 2)


def test_short_pnl_is_signed_correctly():
    bars = make_bars(
        [
            (15000.0, 15000.0, 15000.0, 15000.0),
            (15000.0, 15000.0, 15000.0, 15000.0),
            (14990.0, 14990.0, 14990.0, 14990.0),
        ]
    )
    res = run(bars, {0: [Order(Side.SELL, OrderType.MARKET, 1, bar=0)]})
    t = res.trades[0]
    assert t.direction == -1
    assert t.entry_price == pytest.approx(15000.0 - TICK)
    assert t.net_pnl == pytest.approx((14999.75 - 14990.0) * 20.0)
    assert t.net_pnl > 0


def test_slippage_is_always_against_the_strategy():
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 3)
    long_res = run(bars, {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)]})
    short_res = run(bars, {0: [Order(Side.SELL, OrderType.MARKET, 1, bar=0)]})
    assert long_res.fills[0].price > 100.0, "buy pays up"
    assert short_res.fills[0].price < 100.0, "sell gets less"


def test_zero_slippage_and_zero_cost_round_trip_is_exactly_flat():
    inst = Instrument("NQ", "t", 0.25, 5.0)
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 4)
    sim = Simulator(inst, ExecConfig(slippage_ticks=0.0))
    res = sim.run(bars, Scripted({0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)]}))
    assert res.net_pnl == pytest.approx(0.0)
    assert res.equity[-1] == pytest.approx(res.starting_equity)


def test_final_equity_equals_starting_plus_net_pnl():
    inst = Instrument("NQ", "t", 0.25, 5.0)
    bars = make_bars(
        [(100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 99.0, 100.0),
         (105.0, 106.0, 104.0, 105.0), (110.0, 111.0, 109.0, 110.0)]
    )
    sim = Simulator(inst, ExecConfig())
    res = sim.run(bars, Scripted({0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)]}))
    assert res.equity[-1] == pytest.approx(res.starting_equity + res.net_pnl)


def test_opposing_order_of_equal_size_leaves_you_flat():
    """Selling 1 while long 1 closes the long. It does not open a short."""
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 6)
    res = run(
        bars,
        {
            0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)],
            2: [Order(Side.SELL, OrderType.MARKET, 1, bar=2)],
        },
    )
    assert res.n_trades == 1
    assert res.trades[0].direction == 1
    assert res.trades[0].exit_reason == ExitReason.SIGNAL.value


def test_reversal_needs_more_qty_than_the_open_position():
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 6)
    res = run(
        bars,
        {
            0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)],
            2: [Order(Side.SELL, OrderType.MARKET, 3, bar=2)],
        },
    )
    assert res.n_trades == 2
    assert (res.trades[0].direction, res.trades[0].qty) == (1, 1)
    assert (res.trades[1].direction, res.trades[1].qty) == (-1, 2)


def test_opposing_order_smaller_than_position_reduces_it():
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 6)
    res = run(
        bars,
        {
            0: [Order(Side.BUY, OrderType.MARKET, 3, bar=0)],
            2: [Order(Side.SELL, OrderType.MARKET, 1, bar=2)],
        },
    )
    assert res.trades[0].qty == 1, "partial close of 1 of 3 contracts"
    assert res.trades[-1].qty == 2, "remaining 2 close at end of data"
    assert all(t.direction == 1 for t in res.trades)


def test_open_position_is_closed_at_end_of_data():
    bars = make_bars([(100.0, 100.0, 100.0, 100.0)] * 3)
    res = run(bars, {0: [Order(Side.BUY, OrderType.MARKET, 1, bar=0)]})
    assert res.n_trades == 1
    assert res.trades[0].exit_reason == ExitReason.END_OF_DATA.value
