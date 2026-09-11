"""Order, fill and trade records.

The engine owns every number here. A strategy emits Orders and nothing else --
it never computes a price it gets filled at, a cost, or a P&L.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    SIGNAL = "signal"
    SESSION_END = "session_end"
    ROLL = "roll"
    END_OF_DATA = "end_of_data"


@dataclass
class Order:
    """An intent, submitted at the close of `bar`.

    `bar` is the absolute index of the bar the strategy was looking at when it
    decided. The engine will not fill it earlier than `bar + 1`; that rule is
    the whole of the look-ahead defence and is tested directly.
    """

    side: Side
    type: OrderType
    qty: int
    bar: int
    price: float | None = None          # limit or stop trigger
    stop_loss: float | None = None
    take_profit: float | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        self.side = Side(self.side)
        self.type = OrderType(self.type)
        if self.qty <= 0:
            raise ValueError(f"order qty must be positive, got {self.qty}")
        if self.type is not OrderType.MARKET and self.price is None:
            raise ValueError(f"{self.type.value} order requires a price")


@dataclass
class Fill:
    ts: int
    bar: int
    side: Side
    qty: int
    price: float
    tag: str = ""
    reason: str = ""


@dataclass
class Trade:
    """A completed round trip. Long and short are never netted together."""

    entry_ts: int
    exit_ts: int
    entry_bar: int
    exit_bar: int
    direction: int                       # +1 long, -1 short
    qty: int
    entry_price: float
    exit_price: float
    net_pnl: float
    exit_reason: str
    tag: str = ""
    mae: float = 0.0                     # worst adverse excursion, dollars
    mfe: float = 0.0                     # best favourable excursion, dollars
    fold: int = -1                       # set by the walk-forward stitcher

    @property
    def bars_held(self) -> int:
        return self.exit_bar - self.entry_bar


@dataclass
class RunResult:
    trades: list[Trade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    bar_ts: list[int] = field(default_factory=list)
    starting_equity: float = 0.0
    rejected: list[str] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def n_trades(self) -> int:
        return len(self.trades)
