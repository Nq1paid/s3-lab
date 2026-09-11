"""Canonical bar schema.

Every ingested file is normalised to this shape before anything else touches it.
The wire and the store are always UTC epoch seconds; timezone is a presentation
concern handled at the edges, never carried around in the data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa

# Canonical column order. `ts` is Unix epoch SECONDS in UTC, marking the bar's
# OPEN. Volume is integer contracts.
COLUMNS = ("ts", "open", "high", "low", "close", "volume")

ARROW_SCHEMA = pa.schema(
    [
        pa.field("ts", pa.int64(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
    ]
)

NUMPY_DTYPE = np.dtype(
    [
        ("ts", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("volume", "<i8"),
    ]
)


@dataclass(frozen=True)
class Instrument:
    """Contract specification.

    No commission or fee field, by the user's decision: this engine does not
    model per-contract costs at all. Slippage is the only cost applied, one
    tick per side against the strategy, and it lives in the fill price rather
    than here. See SPEC.md, "Execution realism", for what that means when
    reading a result.
    """

    symbol: str
    name: str
    tick_size: float
    tick_value: float
    currency: str = "USD"

    @property
    def point_value(self) -> float:
        """Dollar value of a one-point move in one contract."""
        return self.tick_value / self.tick_size

    def ticks_to_dollars(self, ticks: float, qty: int = 1) -> float:
        return ticks * self.tick_value * qty

    def round_to_tick(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size


# Ships with the app; editable in the UI.
DEFAULT_INSTRUMENTS: dict[str, Instrument] = {
    "NQ": Instrument("NQ", "E-mini Nasdaq-100", 0.25, 5.00),
    "MNQ": Instrument("MNQ", "Micro E-mini Nasdaq-100", 0.25, 0.50),
    "ES": Instrument("ES", "E-mini S&P 500", 0.25, 12.50),
    "MES": Instrument("MES", "Micro E-mini S&P 500", 0.25, 1.25),
}
