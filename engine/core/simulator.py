"""Reference simulator -- correctness first, speed later.

This is the readable implementation of the money rules. A numba kernel will
follow and must produce an identical trade log; this file is what that kernel
is checked against, so clarity here is worth more than throughput.

Every ambiguity inside a bar is resolved AGAINST the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .types import ExitReason, Fill, Order, OrderType, RunResult, Side, Trade
from ..data.canonical import Instrument


@dataclass
class BarBatch:
    """A window of bars handed to a strategy. Never contains a future bar."""

    start: int
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)


class Strategy(Protocol):
    def on_batch(self, batch: BarBatch, position: int, equity: float) -> list[Order]: ...

    #: Optional. The protocol sends fills back after each batch so a subprocess
    #: strategy can track its own state; in-process strategies usually ignore
    #: it, so it is not required.
    def on_fills(self, fills: list[Fill]) -> None: ...


@dataclass
class ExecConfig:
    slippage_ticks: float = 1.0
    starting_equity: float = 100_000.0
    batch_size: int = 500
    #: Brackets become live on the bar AFTER entry. On the entry bar itself the
    #: path after the fill is unknown, so testing the bracket against that bar's
    #: full range would be an assumption rather than a measurement. Deferring is
    #: conservative in both directions: a stop that fires late costs more, and a
    #: target that fires late earns less.
    brackets_live_next_bar: bool = True


class Simulator:
    def __init__(self, instrument: Instrument, config: ExecConfig | None = None):
        self.inst = instrument
        self.cfg = config or ExecConfig()
        self.slip = self.cfg.slippage_ticks * instrument.tick_size

    # ---------------------------------------------------------------- fills

    def fill_price(self, order: Order, o: float, h: float, l: float) -> float | None:
        """Price this order fills at on a bar, or None if it does not fill."""
        tick = self.inst.tick_size
        buy = order.side is Side.BUY

        if order.type is OrderType.MARKET:
            return o + self.slip if buy else o - self.slip

        if order.type is OrderType.STOP:
            if buy:
                if h >= order.price:
                    # Gapped through the stop -> filled at the open, not the stop.
                    return max(order.price, o) + self.slip
            else:
                if l <= order.price:
                    return min(order.price, o) - self.slip
            return None

        # Limit: requires price to trade at least one tick BEYOND the limit,
        # so touching the limit exactly is not treated as a fill.
        if buy:
            if l <= order.price - tick:
                return order.price
        else:
            if h >= order.price + tick:
                return order.price
        return None

    def _bracket_exit(
        self, direction: int, sl: float | None, tp: float | None, o: float, h: float, l: float
    ) -> tuple[float, ExitReason] | None:
        """Stop-loss wins when a single bar could have hit both."""
        if direction > 0:
            if sl is not None and l <= sl:
                return (min(sl, o) - self.slip, ExitReason.STOP_LOSS)
            if tp is not None and h >= tp:
                return (tp, ExitReason.TAKE_PROFIT)
        else:
            if sl is not None and h >= sl:
                return (max(sl, o) + self.slip, ExitReason.STOP_LOSS)
            if tp is not None and l <= tp:
                return (tp, ExitReason.TAKE_PROFIT)
        return None

    # ------------------------------------------------------------------ run

    def run(self, bars: dict[str, np.ndarray], strategy: Strategy) -> RunResult:
        ts, op, hi, lo, cl, vol = (bars[k] for k in ("ts", "open", "high", "low", "close", "volume"))
        n = len(ts)
        # Optional session-end flatten. The data layer decides which bars are
        # flatten points and marks them here, so the engine never re-derives
        # session boundaries and cannot disagree with the data layer about them.
        # A flatten also cancels resting orders: a day strategy's orders are day
        # orders, and carrying them overnight would enter positions the strategy
        # never intended to hold.
        flatten = bars.get("flatten")
        pv = self.inst.point_value

        res = RunResult(starting_equity=self.cfg.starting_equity)
        cash = self.cfg.starting_equity

        pos = 0                     # signed contracts
        entry_px = 0.0
        entry_ts = 0
        entry_bar = -1
        entry_tag = ""
        sl: float | None = None
        tp: float | None = None
        mae = mfe = 0.0

        pending: list[Order] = []

        def close_position(bar: int, price: float, reason: ExitReason, qty: int | None = None) -> None:
            """Close `qty` contracts of the open position, or all of it."""
            nonlocal pos, cash, entry_px, sl, tp, mae, mfe
            direction = 1 if pos > 0 else -1
            qty = abs(pos) if qty is None else qty
            pnl = direction * (price - entry_px) * pv * qty
            cash += pnl
            res.trades.append(
                Trade(
                    entry_ts=int(entry_ts), exit_ts=int(ts[bar]),
                    entry_bar=entry_bar, exit_bar=bar,
                    direction=direction, qty=qty,
                    entry_price=entry_px, exit_price=price,
                    net_pnl=pnl,
                    exit_reason=reason.value, tag=entry_tag, mae=mae, mfe=mfe,
                )
            )
            res.fills.append(
                Fill(int(ts[bar]), bar, Side.SELL if direction > 0 else Side.BUY,
                     qty, price, entry_tag, reason.value)
            )
            pos -= direction * qty
            if pos == 0:
                sl, tp, mae, mfe = None, None, 0.0, 0.0

        for start in range(0, n, self.cfg.batch_size):
            end = min(start + self.cfg.batch_size, n)
            batch = BarBatch(start, ts[start:end], op[start:end], hi[start:end],
                             lo[start:end], cl[start:end], vol[start:end])

            batch_fill_mark = len(res.fills)
            new_orders = strategy.on_batch(batch, pos, cash) or []
            # Pending orders are processed in ascending (bar, emission order).
            # Older unfilled orders always carry a lower bar, so appending each
            # batch sorted by bar keeps the whole queue in that order. Fixing
            # this is what lets the numba kernel walk the same queue with a
            # single advancing pointer and still match this implementation.
            new_orders = sorted(new_orders, key=lambda o: o.bar)
            for od in new_orders:
                # A strategy may only act on bars it has actually been shown.
                if not (start <= od.bar < end):
                    res.rejected.append(
                        f"order tagged bar {od.bar} outside batch [{start},{end}) -- rejected"
                    )
                    continue
                pending.append(od)

            for i in range(start, end):
                o, h, l = float(op[i]), float(hi[i]), float(lo[i])

                # 1. Bracket exits on an already-open position.
                if pos != 0 and (not self.cfg.brackets_live_next_bar or i > entry_bar):
                    direction = 1 if pos > 0 else -1
                    adverse = (l - entry_px) if direction > 0 else (entry_px - h)
                    favour = (h - entry_px) if direction > 0 else (entry_px - l)
                    mae = min(mae, adverse * pv * abs(pos))
                    mfe = max(mfe, favour * pv * abs(pos))
                    hit = self._bracket_exit(direction, sl, tp, o, h, l)
                    if hit is not None:
                        close_position(i, hit[0], hit[1])

                # 2. Entries and signal exits from orders decided on earlier bars.
                still: list[Order] = []
                for od in pending:
                    if od.bar >= i:                     # not live until bar+1
                        still.append(od)
                        continue
                    px = self.fill_price(od, o, h, l)
                    if px is None:
                        still.append(od)
                        continue

                    signed = od.qty if od.side is Side.BUY else -od.qty
                    res.fills.append(
                        Fill(int(ts[i]), i, od.side, od.qty, px, od.tag)
                    )

                    if pos != 0 and (pos > 0) != (signed > 0):
                        # Opposing order: reduce first, and only flip if it is
                        # strictly larger than the open position. Selling 1
                        # while long 1 leaves you flat, it does not open a
                        # short -- reversing needs qty greater than the
                        # position, exactly as it would at a broker.
                        before = pos
                        close_position(i, px, ExitReason.SIGNAL, min(abs(pos), od.qty))
                        remainder = before + signed
                        if remainder != 0 and (remainder > 0) == (signed > 0):
                            pos = remainder
                            entry_px, entry_ts, entry_bar, entry_tag = px, int(ts[i]), i, od.tag
                            sl, tp = od.stop_loss, od.take_profit
                            mae = mfe = 0.0
                    elif pos == 0:
                        pos = signed
                        entry_px, entry_ts, entry_bar, entry_tag = px, int(ts[i]), i, od.tag
                        sl, tp = od.stop_loss, od.take_profit
                        mae = mfe = 0.0
                    else:
                        # Adding to a winner/loser: volume-weighted average entry.
                        total = pos + signed
                        entry_px = (entry_px * pos + px * signed) / total
                        pos = total
                pending = still

                # 2b. Session-end flatten: close at this bar's close, against
                #     the strategy, and cancel every resting order.
                if flatten is not None and flatten[i]:
                    if pos != 0:
                        direction = 1 if pos > 0 else -1
                        px = float(cl[i]) - self.slip if direction > 0 else float(cl[i]) + self.slip
                        close_position(i, px, ExitReason.SESSION_END)
                    # Cancel RESTING orders only. This queue is pre-loaded with
                    # the whole batch, including orders for bars the strategy
                    # has not reached yet; those decisions have not been made
                    # and are not the flatten's to cancel.
                    pending = [od for od in pending if od.bar >= i]

                # 3. Mark to market on this bar's close.
                unreal = (float(cl[i]) - entry_px) * pv * pos if pos else 0.0
                res.equity.append(cash + unreal)
                res.bar_ts.append(int(ts[i]))

            # Report this batch's fills back, for strategies that track state.
            on_fills = getattr(strategy, "on_fills", None)
            if on_fills is not None:
                on_fills(res.fills[batch_fill_mark:])

        if pos != 0:
            close_position(n - 1, float(cl[n - 1]), ExitReason.END_OF_DATA)
            res.equity[-1] = cash
        return res
