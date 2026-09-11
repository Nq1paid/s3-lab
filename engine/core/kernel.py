"""numba kernel -- the fast path.

Simulates fills and accounting over a precomputed order stream. It must produce
a trade log identical to `simulator.Simulator`; `tests/test_kernel_equivalence.py`
asserts that on randomised data and is the only reason to trust this file.

The order stream is precomputed because, under the batch protocol, a strategy's
orders for a batch already depend only on the bars in that batch plus the
position and equity at its start -- never on fills inside it. So order
generation and fill simulation separate cleanly, and only the second half needs
to be fast.
"""

from __future__ import annotations

import numpy as np
from numba import njit

from .types import ExitReason, Order, OrderType, RunResult, Side, Trade

# Exit reason codes, mirrored in ExitReason on the way out.
R_STOP_LOSS = 0
R_TAKE_PROFIT = 1
R_SIGNAL = 2
R_END_OF_DATA = 3
R_SESSION_END = 4

# Order type / side codes.
T_MARKET, T_LIMIT, T_STOP = 0, 1, 2
S_BUY, S_SELL = 1, -1


@njit(cache=True)
def simulate(
    ts, op, hi, lo, cl, flat,
    o_bar, o_side, o_type, o_price, o_sl, o_tp, o_qty,
    tick, pv, slip, start_eq, brackets_next_bar,
):
    n = len(ts)
    n_ord = len(o_bar)
    max_trades = 2 * n_ord + 2

    t_entry_bar = np.empty(max_trades, np.int64)
    t_exit_bar = np.empty(max_trades, np.int64)
    t_entry_ts = np.empty(max_trades, np.int64)
    t_exit_ts = np.empty(max_trades, np.int64)
    t_dir = np.empty(max_trades, np.int64)
    t_qty = np.empty(max_trades, np.int64)
    t_entry_px = np.empty(max_trades, np.float64)
    t_exit_px = np.empty(max_trades, np.float64)
    t_pnl = np.empty(max_trades, np.float64)
    t_reason = np.empty(max_trades, np.int64)
    t_mae = np.empty(max_trades, np.float64)
    t_mfe = np.empty(max_trades, np.float64)
    n_trades = 0

    equity = np.empty(n, np.float64)

    pending = np.empty(n_ord, np.int32)
    n_pending = 0
    next_ord = 0

    cash = start_eq
    pos = 0
    entry_px = 0.0
    entry_ts = 0
    entry_bar = -1
    sl = np.nan
    tp = np.nan
    mae = 0.0
    mfe = 0.0

    for i in range(n):
        o = op[i]
        h = hi[i]
        l = lo[i]

        # --- 1. bracket exit on an already-open position
        if pos != 0 and (brackets_next_bar == 0 or i > entry_bar):
            direction = 1 if pos > 0 else -1
            aq = abs(pos)
            if direction > 0:
                adverse = l - entry_px
                favour = h - entry_px
            else:
                adverse = entry_px - h
                favour = entry_px - l
            if adverse * pv * aq < mae:
                mae = adverse * pv * aq
            if favour * pv * aq > mfe:
                mfe = favour * pv * aq

            hit = False
            px = 0.0
            reason = -1
            if direction > 0:
                if not np.isnan(sl) and l <= sl:
                    px = (sl if sl < o else o) - slip
                    reason = R_STOP_LOSS
                    hit = True
                elif not np.isnan(tp) and h >= tp:
                    px = tp
                    reason = R_TAKE_PROFIT
                    hit = True
            else:
                if not np.isnan(sl) and h >= sl:
                    px = (sl if sl > o else o) + slip
                    reason = R_STOP_LOSS
                    hit = True
                elif not np.isnan(tp) and l <= tp:
                    px = tp
                    reason = R_TAKE_PROFIT
                    hit = True

            if hit:
                pnl = direction * (px - entry_px) * pv * aq
                cash += pnl
                t_entry_bar[n_trades] = entry_bar
                t_exit_bar[n_trades] = i
                t_entry_ts[n_trades] = entry_ts
                t_exit_ts[n_trades] = ts[i]
                t_dir[n_trades] = direction
                t_qty[n_trades] = aq
                t_entry_px[n_trades] = entry_px
                t_exit_px[n_trades] = px
                t_pnl[n_trades] = pnl
                t_reason[n_trades] = reason
                t_mae[n_trades] = mae
                t_mfe[n_trades] = mfe
                n_trades += 1
                pos = 0
                sl = np.nan
                tp = np.nan
                mae = 0.0
                mfe = 0.0

        # --- 2. admit newly eligible orders (queue stays sorted by bar)
        while next_ord < n_ord and o_bar[next_ord] < i:
            pending[n_pending] = next_ord
            n_pending += 1
            next_ord += 1

        # --- 3. try to fill pending orders, in queue order
        w = 0
        for p in range(n_pending):
            k = pending[p]
            side = o_side[k]
            otype = o_type[k]
            oprice = o_price[k]
            filled = False
            px = 0.0

            if otype == T_MARKET:
                px = o + slip if side == S_BUY else o - slip
                filled = True
            elif otype == T_STOP:
                if side == S_BUY:
                    if h >= oprice:
                        px = (oprice if oprice > o else o) + slip
                        filled = True
                else:
                    if l <= oprice:
                        px = (oprice if oprice < o else o) - slip
                        filled = True
            else:  # limit -- needs a full tick beyond
                if side == S_BUY:
                    if l <= oprice - tick:
                        px = oprice
                        filled = True
                else:
                    if h >= oprice + tick:
                        px = oprice
                        filled = True

            if not filled:
                pending[w] = k
                w += 1
                continue

            signed = o_qty[k] if side == S_BUY else -o_qty[k]

            if pos != 0 and ((pos > 0) != (signed > 0)):
                direction = 1 if pos > 0 else -1
                cq = abs(pos) if abs(pos) < o_qty[k] else o_qty[k]
                pnl = direction * (px - entry_px) * pv * cq
                cash += pnl
                t_entry_bar[n_trades] = entry_bar
                t_exit_bar[n_trades] = i
                t_entry_ts[n_trades] = entry_ts
                t_exit_ts[n_trades] = ts[i]
                t_dir[n_trades] = direction
                t_qty[n_trades] = cq
                t_entry_px[n_trades] = entry_px
                t_exit_px[n_trades] = px
                t_pnl[n_trades] = pnl
                t_reason[n_trades] = R_SIGNAL
                t_mae[n_trades] = mae
                t_mfe[n_trades] = mfe
                n_trades += 1

                before = pos
                pos -= direction * cq
                if pos == 0:
                    sl = np.nan
                    tp = np.nan
                    mae = 0.0
                    mfe = 0.0
                remainder = before + signed
                if remainder != 0 and ((remainder > 0) == (signed > 0)):
                    pos = remainder
                    entry_px = px
                    entry_ts = ts[i]
                    entry_bar = i
                    sl = o_sl[k]
                    tp = o_tp[k]
                    mae = 0.0
                    mfe = 0.0
            elif pos == 0:
                pos = signed
                entry_px = px
                entry_ts = ts[i]
                entry_bar = i
                sl = o_sl[k]
                tp = o_tp[k]
                mae = 0.0
                mfe = 0.0
            else:
                total = pos + signed
                entry_px = (entry_px * pos + px * signed) / total
                pos = total

        n_pending = w

        # --- 3b. session-end flatten, and cancel resting day orders
        if flat[i] != 0:
            if pos != 0:
                direction = 1 if pos > 0 else -1
                aq = abs(pos)
                px = cl[i] - slip if direction > 0 else cl[i] + slip
                pnl = direction * (px - entry_px) * pv * aq
                cash += pnl
                t_entry_bar[n_trades] = entry_bar
                t_exit_bar[n_trades] = i
                t_entry_ts[n_trades] = entry_ts
                t_exit_ts[n_trades] = ts[i]
                t_dir[n_trades] = direction
                t_qty[n_trades] = aq
                t_entry_px[n_trades] = entry_px
                t_exit_px[n_trades] = px
                t_pnl[n_trades] = pnl
                t_reason[n_trades] = R_SESSION_END
                t_mae[n_trades] = mae
                t_mfe[n_trades] = mfe
                n_trades += 1
                pos = 0
                sl = np.nan
                tp = np.nan
                mae = 0.0
                mfe = 0.0
            n_pending = 0

        # --- 4. mark to market
        equity[i] = cash + ((cl[i] - entry_px) * pv * pos if pos != 0 else 0.0)

    if pos != 0:
        direction = 1 if pos > 0 else -1
        aq = abs(pos)
        px = cl[n - 1]
        pnl = direction * (px - entry_px) * pv * aq
        cash += pnl
        t_entry_bar[n_trades] = entry_bar
        t_exit_bar[n_trades] = n - 1
        t_entry_ts[n_trades] = entry_ts
        t_exit_ts[n_trades] = ts[n - 1]
        t_dir[n_trades] = direction
        t_qty[n_trades] = aq
        t_entry_px[n_trades] = entry_px
        t_exit_px[n_trades] = px
        t_pnl[n_trades] = pnl
        t_reason[n_trades] = R_END_OF_DATA
        t_mae[n_trades] = mae
        t_mfe[n_trades] = mfe
        n_trades += 1
        equity[n - 1] = cash

    return (
        t_entry_bar[:n_trades], t_exit_bar[:n_trades],
        t_entry_ts[:n_trades], t_exit_ts[:n_trades],
        t_dir[:n_trades], t_qty[:n_trades],
        t_entry_px[:n_trades], t_exit_px[:n_trades],
        t_pnl[:n_trades],
        t_reason[:n_trades], t_mae[:n_trades], t_mfe[:n_trades],
        equity,
    )


# ---------------------------------------------------------------- wrapper

_SIDE = {Side.BUY: S_BUY, Side.SELL: S_SELL}
_TYPE = {OrderType.MARKET: T_MARKET, OrderType.LIMIT: T_LIMIT, OrderType.STOP: T_STOP}
_REASON = {
    R_STOP_LOSS: ExitReason.STOP_LOSS,
    R_TAKE_PROFIT: ExitReason.TAKE_PROFIT,
    R_SIGNAL: ExitReason.SIGNAL,
    R_END_OF_DATA: ExitReason.END_OF_DATA,
    R_SESSION_END: ExitReason.SESSION_END,
}


def orders_to_arrays(orders: list[Order]):
    """Pack orders into the kernel's columnar form, in canonical queue order."""
    ordered = sorted(orders, key=lambda o: o.bar)
    m = len(ordered)
    a_bar = np.empty(m, np.int64)
    a_side = np.empty(m, np.int64)
    a_type = np.empty(m, np.int64)
    a_price = np.empty(m, np.float64)
    a_sl = np.empty(m, np.float64)
    a_tp = np.empty(m, np.float64)
    a_qty = np.empty(m, np.int64)
    for i, od in enumerate(ordered):
        a_bar[i] = od.bar
        a_side[i] = _SIDE[od.side]
        a_type[i] = _TYPE[od.type]
        a_price[i] = np.nan if od.price is None else od.price
        a_sl[i] = np.nan if od.stop_loss is None else od.stop_loss
        a_tp[i] = np.nan if od.take_profit is None else od.take_profit
        a_qty[i] = od.qty
    return a_bar, a_side, a_type, a_price, a_sl, a_tp, a_qty


def run_kernel(bars: dict, orders: list[Order], instrument, cfg) -> RunResult:
    """Fast path. Same inputs, same trade log as Simulator.run."""
    ts, op, hi, lo, cl = (bars[k] for k in ("ts", "open", "high", "low", "close"))
    flat = bars.get("flatten")
    flat = np.zeros(len(ts), np.int8) if flat is None else flat.astype(np.int8)
    a = orders_to_arrays(orders)
    out = simulate(
        ts, op, hi, lo, cl, flat, *a,
        instrument.tick_size,
        instrument.point_value,
        cfg.slippage_ticks * instrument.tick_size,
        cfg.starting_equity,
        1 if cfg.brackets_live_next_bar else 0,
    )
    (eb, xb, ets, xts, dr, qt, epx, xpx, pnl, rs, mae, mfe, equity) = out

    res = RunResult(starting_equity=cfg.starting_equity)
    for i in range(len(eb)):
        res.trades.append(
            Trade(
                entry_ts=int(ets[i]), exit_ts=int(xts[i]),
                entry_bar=int(eb[i]), exit_bar=int(xb[i]),
                direction=int(dr[i]), qty=int(qt[i]),
                entry_price=float(epx[i]), exit_price=float(xpx[i]),
                net_pnl=float(pnl[i]),
                exit_reason=_REASON[int(rs[i])].value,
                mae=float(mae[i]), mfe=float(mfe[i]),
            )
        )
    res.equity = [float(x) for x in equity]
    res.bar_ts = [int(x) for x in ts]
    return res


class ReplayStrategy:
    """Feeds a fixed order list back through the reference simulator.

    Lets both implementations be driven by exactly the same orders, which is
    what makes the equivalence test a comparison of the fill and accounting
    rules rather than of two different strategies.
    """

    def __init__(self, orders: list[Order]):
        self.orders = orders

    def on_batch(self, batch, position, equity):
        end = batch.start + len(batch)
        return [o for o in self.orders if batch.start <= o.bar < end]
