"""Run history.

Every completed run is recorded with the configuration and the costs it was
computed with. A result is only meaningful alongside the assumptions behind it,
so those travel with it rather than being re-derived from whatever the settings
happen to say later.

Trades live in a Parquet file beside the database; the row carries the path.
Putting a 2,000-row trade log in a SQLite blob makes the history unreadable and
the trade log unusable by anything else.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DB = Path("runs/history.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    label         TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    bar_minutes   INTEGER NOT NULL,
    session       TEXT NOT NULL,
    mode          TEXT NOT NULL,
    is_sessions   INTEGER NOT NULL,
    oos_sessions  INTEGER NOT NULL,
    step_sessions INTEGER NOT NULL,
    objective     TEXT NOT NULL,
    grid_size     INTEGER NOT NULL,
    n_folds       INTEGER NOT NULL,
    n_trades      INTEGER NOT NULL,
    net_pnl       REAL NOT NULL,
    return_pct    REAL NOT NULL,
    max_dd        REAL NOT NULL,
    mar           REAL,
    sharpe        REAL,
    wfe_mean      REAL,
    elapsed_s     REAL NOT NULL,
    slippage_ticks  REAL NOT NULL,
    starting_equity REAL NOT NULL,
    grid_json     TEXT NOT NULL,
    folds_json    TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    trades_path   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
"""


@dataclass
class RunRecord:
    run_id: str
    created_at: float
    label: str
    strategy: str
    instrument: str
    bar_minutes: int
    session: str
    mode: str
    is_sessions: int
    oos_sessions: int
    step_sessions: int
    objective: str
    grid_size: int
    n_folds: int
    n_trades: int
    net_pnl: float
    return_pct: float
    max_dd: float
    mar: float | None
    sharpe: float | None
    wfe_mean: float | None
    elapsed_s: float
    slippage_ticks: float
    starting_equity: float
    grid_json: str
    folds_json: str
    warnings_json: str
    trades_path: str

    @property
    def folds(self) -> list[dict]:
        return json.loads(self.folds_json)

    @property
    def warnings(self) -> list[str]:
        return json.loads(self.warnings_json)

    @property
    def short_id(self) -> str:
        return self.run_id[:8]


def connect(db: Path = DB) -> sqlite3.Connection:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


#: Columns dropped from the schema, newest first. CREATE TABLE IF NOT EXISTS
#: does nothing to a table that already exists, so a database written before a
#: column was removed would keep a NOT NULL column no insert ever fills again.
RETIRED_COLUMNS = ("commission", "fees", "costs_confirmed")


def _migrate(conn: sqlite3.Connection) -> None:
    """Drop retired columns from an existing database.

    Commission was removed from the engine, so these three columns describe a
    cost model that no longer exists. Dropping them keeps old runs readable
    rather than discarding history -- the numbers in those runs never included
    commission anyway, because it was always 0.00 and never confirmed.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    for col in RETIRED_COLUMNS:
        if col in have:
            conn.execute("ALTER TABLE runs DROP COLUMN " + col)


def _clean(x):
    """NaN is not a number SQLite can order by, and it is not a result."""
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    return float(x)


def save(result, state, label: str = "", db: Path = DB) -> RunRecord:
    """Record a completed walk-forward and its trades."""
    import numpy as np
    import pandas as pd

    run_id = uuid.uuid4().hex
    t, m = state.test, result.metrics
    trades_dir = Path(db).parent / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)
    trades_path = trades_dir / (run_id + ".parquet")

    rows = [
        {"entry_ts": tr.entry_ts, "exit_ts": tr.exit_ts, "direction": tr.direction,
         "qty": tr.qty, "entry_price": tr.entry_price, "exit_price": tr.exit_price,
         "net_pnl": tr.net_pnl,
         "exit_reason": tr.exit_reason, "bars_held": tr.bars_held,
         "mae": tr.mae, "mfe": tr.mfe, "fold": tr.fold}
        for tr in result.stitched.trades
    ]
    pd.DataFrame(rows).to_parquet(trades_path, index=False)

    wfes = [f.oos_metrics.mar / f.is_metrics.mar
            for f in result.qualified
            if f.is_metrics.mar and not math.isnan(f.is_metrics.mar)]

    folds = [
        {"index": f.index, "params": f.params, "disqualified": f.disqualified,
         "is_mar": _clean(f.is_metrics.mar), "oos_mar": _clean(f.oos_metrics.mar),
         "oos_net": f.oos_metrics.net_pnl, "oos_n": f.oos_metrics.n_trades,
         "oos_start_ts": (f.oos_bar_ts[0] if f.oos_bar_ts else 0)}
        for f in result.folds
    ]

    rec = RunRecord(
        run_id=run_id, created_at=time.time(),
        label=label or (t.strategy_name + " " + t.instrument),
        strategy=t.strategy_name, instrument=t.instrument,
        bar_minutes=t.bar_minutes, session=t.session, mode=t.mode,
        is_sessions=t.is_sessions, oos_sessions=t.oos_sessions,
        step_sessions=t.step_sessions, objective=t.objective,
        grid_size=result.grid_size, n_folds=len(result.folds),
        n_trades=m.n_trades, net_pnl=m.net_pnl, return_pct=m.return_pct,
        max_dd=m.max_drawdown, mar=_clean(m.mar), sharpe=_clean(m.sharpe),
        wfe_mean=_clean(float(np.mean(wfes))) if wfes else None,
        elapsed_s=result.elapsed_s,
        slippage_ticks=state.slippage_ticks,
        starting_equity=state.starting_equity,
        grid_json=json.dumps(t.grid),
        folds_json=json.dumps(folds),
        warnings_json=json.dumps(result.warnings),
        trades_path=str(trades_path),
    )

    cols = list(rec.__dict__.keys())
    placeholders = ",".join(["?"] * len(cols))
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO runs (" + ",".join(cols) + ") VALUES (" + placeholders + ")",
            [getattr(rec, k) for k in cols],
        )
    return rec


def recent(limit: int = 50, db: Path = DB) -> list[RunRecord]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [RunRecord(**dict(r)) for r in rows]


def get(run_id: str, db: Path = DB) -> RunRecord | None:
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return RunRecord(**dict(row)) if row else None


def load_trades(rec: RunRecord):
    import pandas as pd

    return pd.read_parquet(rec.trades_path)


def delete(run_id: str, db: Path = DB) -> bool:
    rec = get(run_id, db)
    if rec is None:
        return False
    Path(rec.trades_path).unlink(missing_ok=True)
    with connect(db) as conn:
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    return True


@dataclass
class Comparison:
    a: RunRecord
    b: RunRecord
    rows: list = field(default_factory=list)
    differences: list = field(default_factory=list)

    def render(self) -> str:
        out = ["  " + "".ljust(22) + self.a.short_id.rjust(16) + self.b.short_id.rjust(16), ""]
        for label, av, bv, delta in self.rows:
            out.append("  " + label.ljust(22) + av.rjust(16) + bv.rjust(16) + delta.rjust(12))
        if self.differences:
            out.append("")
            out.append("  configuration differences")
            for d in self.differences:
                out.append("    " + d)
        return "\n".join(out)


def compare(a: RunRecord, b: RunRecord) -> Comparison:
    """Side by side, with the configuration differences called out.

    Two results are only comparable if the assumptions behind them match, so
    any difference in costs, windows or objective is listed rather than left
    for the reader to notice.
    """
    def fmt(x, dp=2):
        return "-" if x is None else format(x, ",." + str(dp) + "f")

    def delta(x, y, dp=2):
        if x is None or y is None:
            return "-"
        d = y - x
        sign = "+" if d >= 0 else ""
        return sign + format(d, ",." + str(dp) + "f")

    rows = [
        ("net P&L", fmt(a.net_pnl, 0), fmt(b.net_pnl, 0), delta(a.net_pnl, b.net_pnl, 0)),
        ("return %", fmt(a.return_pct), fmt(b.return_pct), delta(a.return_pct, b.return_pct)),
        ("max drawdown", fmt(a.max_dd, 0), fmt(b.max_dd, 0), delta(a.max_dd, b.max_dd, 0)),
        ("MAR", fmt(a.mar), fmt(b.mar), delta(a.mar, b.mar)),
        ("Sharpe", fmt(a.sharpe), fmt(b.sharpe), delta(a.sharpe, b.sharpe)),
        ("WF efficiency", fmt(a.wfe_mean), fmt(b.wfe_mean), delta(a.wfe_mean, b.wfe_mean)),
        ("OOS trades", format(a.n_trades, ","), format(b.n_trades, ","),
         format(b.n_trades - a.n_trades, "+,")),
        ("folds", str(a.n_folds), str(b.n_folds), format(b.n_folds - a.n_folds, "+d")),
        ("grid", format(a.grid_size, ","), format(b.grid_size, ","),
         format(b.grid_size - a.grid_size, "+,")),
    ]

    differences = []
    pairs = (
        ("strategy", a.strategy, b.strategy),
        ("instrument", a.instrument + " " + str(a.bar_minutes) + "m " + a.session,
         b.instrument + " " + str(b.bar_minutes) + "m " + b.session),
        ("mode", a.mode, b.mode),
        ("objective", a.objective, b.objective),
        ("IS/OOS/step",
         str(a.is_sessions) + "/" + str(a.oos_sessions) + "/" + str(a.step_sessions),
         str(b.is_sessions) + "/" + str(b.oos_sessions) + "/" + str(b.step_sessions)),
        ("slippage ticks", format(a.slippage_ticks, "g"), format(b.slippage_ticks, "g")),
        ("starting equity", format(a.starting_equity, ",.0f"),
         format(b.starting_equity, ",.0f")),
    )
    for label, x, y in pairs:
        if x != y:
            differences.append(label + ": " + x + "  ->  " + y)

    if not differences:
        differences.append("none - these two runs are directly comparable")
    return Comparison(a=a, b=b, rows=rows, differences=differences)
