"""CSV schema sniffing.

The user confirms a column mapping once per vendor format, never twice. A
mapping is keyed by a structural signature of the file (delimiter, column count,
header names, timestamp shape) rather than by path, so a new export in the same
format is recognised without asking again.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Timestamp layouts seen in retail futures exports, tried in order.
TS_FORMATS = (
    ("%Y%m%d %H%M%S", re.compile(r"^\d{8} \d{6}$")),
    ("%Y%m%d %H%M", re.compile(r"^\d{8} \d{4}$")),
    ("%Y-%m-%d %H:%M:%S", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("%Y-%m-%d %H:%M", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")),
    ("%m/%d/%Y %H:%M:%S", re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2}$")),
    ("%m/%d/%Y %H:%M", re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}$")),
    ("%Y-%m-%dT%H:%M:%S", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")),
)

DATE_ONLY = (
    ("%Y%m%d", re.compile(r"^\d{8}$")),
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("%m/%d/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
)

TIME_ONLY = (
    ("%H%M%S", re.compile(r"^\d{6}$")),
    ("%H:%M:%S", re.compile(r"^\d{1,2}:\d{2}:\d{2}$")),
    ("%H:%M", re.compile(r"^\d{1,2}:\d{2}$")),
)

HEADER_ALIASES = {
    "open": {"open", "o", "op"},
    "high": {"high", "h", "hi"},
    "low": {"low", "l", "lo"},
    "close": {"close", "c", "last", "cl"},
    "volume": {"volume", "vol", "v", "qty"},
}


@dataclass
class SchemaGuess:
    delimiter: str
    has_header: bool
    n_columns: int
    ts_cols: list[int]                 # one combined, or [date, time]
    ts_format: str                     # combined format, or "date|time"
    cols: dict[str, int] = field(default_factory=dict)   # open/high/low/close/volume -> index
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    sample: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(self.ts_cols) and all(k in self.cols for k in ("open", "high", "low", "close"))

    def signature(self) -> str:
        """Structural fingerprint used to reuse a confirmed mapping."""
        payload = json.dumps(
            {
                "d": self.delimiter,
                "h": self.has_header,
                "n": self.n_columns,
                "t": self.ts_cols,
                "f": self.ts_format,
                "c": self.cols,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def describe(self) -> str:
        order = sorted(self.cols.items(), key=lambda kv: kv[1])
        mapping = ", ".join(f"col{i}={k}" for k, i in order)
        ts = f"col{self.ts_cols[0]}" if len(self.ts_cols) == 1 else f"col{self.ts_cols[0]}+col{self.ts_cols[1]}"
        return f"{ts} as {self.ts_format} | {mapping}"


def _match(value: str, table) -> str | None:
    for fmt, rx in table:
        if rx.match(value):
            return fmt
    return None


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def sniff(path: str | Path, n_sample: int = 60) -> SchemaGuess:
    """Infer delimiter, header and column roles from the first rows of a file.

    Returns a guess with a confidence score. A guess is a proposal for the user
    to confirm, never an assumption acted on silently.
    """
    path = Path(path)
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
        lines = [next(f, "").rstrip("\r\n") for _ in range(n_sample)]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        raise ValueError(f"{path.name} is empty")

    counts = {d: lines[0].count(d) for d in (",", "\t", ";", "|")}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        raise ValueError(f"{path.name}: no delimiter found in first line")

    rows = list(csv.reader(lines, delimiter=delimiter))
    width = max(len(r) for r in rows)
    rows = [r for r in rows if len(r) == width]

    # A header row has non-numeric cells where later rows hold numbers.
    first, rest = rows[0], rows[1:]
    has_header = bool(rest) and sum(_is_number(c) for c in first) < sum(_is_number(c) for c in rest[0])
    data = rest if has_header else rows
    if not data:
        raise ValueError(f"{path.name}: no data rows")
    probe = data[0]

    notes: list[str] = []
    ts_cols: list[int] = []
    ts_format = ""

    # Prefer a single combined timestamp column.
    for i, cell in enumerate(probe):
        fmt = _match(cell.strip(), TS_FORMATS)
        if fmt:
            ts_cols, ts_format = [i], fmt
            break
    # Otherwise look for adjacent date + time columns.
    if not ts_cols:
        for i, cell in enumerate(probe):
            dfmt = _match(cell.strip(), DATE_ONLY)
            if dfmt and i + 1 < len(probe):
                tfmt = _match(probe[i + 1].strip(), TIME_ONLY)
                if tfmt:
                    ts_cols, ts_format = [i, i + 1], f"{dfmt}|{tfmt}"
                    break
    if not ts_cols:
        notes.append("no recognisable timestamp column")

    cols: dict[str, int] = {}
    if has_header:
        for i, name in enumerate(first):
            key = name.strip().lower()
            for role, aliases in HEADER_ALIASES.items():
                if key in aliases:
                    cols[role] = i
    if not all(k in cols for k in ("open", "high", "low", "close")):
        # Positional fallback: OHLC[V] are the numeric columns after the stamp.
        start = (ts_cols[-1] + 1) if ts_cols else 0
        numeric = [i for i in range(start, len(probe)) if _is_number(probe[i].strip())]
        if len(numeric) >= 4:
            cols = dict(zip(("open", "high", "low", "close"), numeric[:4]))
            if len(numeric) >= 5:
                cols["volume"] = numeric[4]
            notes.append("column roles inferred by position, not by header")
        else:
            notes.append(f"expected >=4 numeric columns after the timestamp, found {len(numeric)}")

    guess = SchemaGuess(
        delimiter=delimiter,
        has_header=has_header,
        n_columns=width,
        ts_cols=ts_cols,
        ts_format=ts_format,
        cols=cols,
        sample=lines[:3],
        notes=notes,
    )

    conf = 0.0
    if ts_cols:
        conf += 0.5
    if all(k in cols for k in ("open", "high", "low", "close")):
        conf += 0.3
    if "volume" in cols:
        conf += 0.1
    # Sanity: high >= low on the probe row is cheap and catches a bad mapping.
    if guess.complete:
        try:
            if float(probe[cols["high"]]) >= float(probe[cols["low"]]):
                conf += 0.1
            else:
                notes.append("high < low on the sample row -- mapping is probably wrong")
        except (ValueError, IndexError):
            pass
    guess.confidence = round(conf, 2)
    return guess


class MappingStore:
    """Confirmed mappings, keyed by structural signature. Never ask twice."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def get(self, guess: SchemaGuess) -> dict | None:
        return self._data.get(guess.signature())

    def confirm(self, guess: SchemaGuess, label: str = "") -> None:
        self._data[guess.signature()] = {"label": label, **asdict(guess)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))
