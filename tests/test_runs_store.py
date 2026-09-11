"""Run history: what is stored, and what must travel with a result."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.store import runs as store  # noqa: E402
from tui.state import AppState  # noqa: E402


# --- minimal stand-ins for a completed walk-forward -------------------------

@dataclass
class FakeMetrics:
    n_trades: int = 100
    net_pnl: float = 5000.0
    return_pct: float = 5.0
    max_drawdown: float = 1200.0
    mar: float = 0.42
    sharpe: float = 0.8


@dataclass
class FakeTrade:
    entry_ts: int = 1_600_000_000
    exit_ts: int = 1_600_001_800
    direction: int = 1
    qty: int = 1
    entry_price: float = 15000.0
    exit_price: float = 15010.0
    net_pnl: float = 192.8
    exit_reason: str = "take_profit"
    bars_held: int = 30
    mae: float = -50.0
    mfe: float = 220.0
    fold: int = 0


@dataclass
class FakeFold:
    index: int = 0
    params: dict = field(default_factory=lambda: {"stop_ticks": 40})
    disqualified: str = ""
    is_metrics: FakeMetrics = field(default_factory=FakeMetrics)
    oos_metrics: FakeMetrics = field(default_factory=FakeMetrics)
    oos_bar_ts: list = field(default_factory=lambda: [1_600_000_000])


@dataclass
class FakeStitched:
    trades: list = field(default_factory=lambda: [FakeTrade(), FakeTrade()])


@dataclass
class FakeResult:
    folds: list = field(default_factory=lambda: [FakeFold(), FakeFold(index=1)])
    stitched: FakeStitched = field(default_factory=FakeStitched)
    metrics: FakeMetrics = field(default_factory=FakeMetrics)
    grid_size: int = 64
    elapsed_s: float = 31.0
    warnings: list = field(default_factory=list)

    @property
    def qualified(self):
        return [f for f in self.folds if not f.disqualified]


@pytest.fixture
def db(tmp_path):
    return tmp_path / "history.sqlite"


@pytest.fixture
def state():
    s = AppState()
    s.slippage_ticks = 1.0
    return s


def test_a_run_is_saved_and_read_back(db, state):
    rec = store.save(FakeResult(), state, label="first", db=db)
    got = store.get(rec.run_id, db=db)
    assert got is not None
    assert got.label == "first"
    assert got.n_trades == 100
    assert got.n_folds == 2


def test_execution_settings_travel_with_the_run(db, state):
    """A result is only meaningful next to the settings it was computed with."""
    rec = store.save(FakeResult(), state, db=db)
    # Change the settings afterwards; the stored run must not move.
    state.slippage_ticks = 9.0
    state.starting_equity = 1.0
    got = store.get(rec.run_id, db=db)
    assert got.slippage_ticks == 1.0
    assert got.starting_equity == 100_000.0


def test_a_database_written_before_commission_was_removed_still_opens(db):
    """Old history survives the cost model being deleted.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    without a migration the retired NOT NULL columns would reject every new
    insert and the user would lose their run history to a schema change.
    """
    import sqlite3

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    for col in store.RETIRED_COLUMNS:
        conn.execute("ALTER TABLE runs ADD COLUMN " + col + " REAL NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()

    rec = store.save(FakeResult(), AppState(), db=db)      # would fail unmigrated
    assert store.get(rec.run_id, db=db) is not None
    assert not (set(store.RETIRED_COLUMNS)
                & set(store.RunRecord.__dataclass_fields__))


def test_trades_are_written_beside_the_database_and_reload(db, state):
    rec = store.save(FakeResult(), state, db=db)
    assert Path(rec.trades_path).exists()
    df = store.load_trades(rec)
    assert len(df) == 2
    assert set(["entry_ts", "net_pnl", "exit_reason", "fold"]) <= set(df.columns)


def test_history_is_newest_first(db, state):
    a = store.save(FakeResult(), state, label="older", db=db)
    b = store.save(FakeResult(), state, label="newer", db=db)
    got = store.recent(db=db)
    assert [r.run_id for r in got][:2] == [b.run_id, a.run_id]


def test_nan_metrics_are_stored_as_null_not_nan(db, state):
    """NaN is not orderable in SQL and is not a result."""
    result = FakeResult()
    result.metrics.mar = float("nan")
    result.metrics.sharpe = float("nan")
    rec = store.save(result, state, db=db)
    got = store.get(rec.run_id, db=db)
    assert got.mar is None and got.sharpe is None


def test_deleting_a_run_removes_its_trade_file(db, state):
    rec = store.save(FakeResult(), state, db=db)
    path = Path(rec.trades_path)
    assert store.delete(rec.run_id, db=db) is True
    assert not path.exists()
    assert store.get(rec.run_id, db=db) is None
    assert store.delete(rec.run_id, db=db) is False


# --- comparison -------------------------------------------------------------

def test_compare_reports_deltas(db, state):
    a = store.save(FakeResult(), state, label="a", db=db)
    result = FakeResult()
    result.metrics.net_pnl = 9000.0
    b = store.save(result, state, label="b", db=db)
    cmp = store.compare(a, b)
    net = [r for r in cmp.rows if r[0] == "net P&L"][0]
    assert net[3].startswith("+4,000")


def test_compare_calls_out_a_slippage_difference(db, state):
    """Two runs computed with different slippage are not comparable."""
    a = store.save(FakeResult(), state, db=db)
    state.slippage_ticks = 5.0
    b = store.save(FakeResult(), state, db=db)
    cmp = store.compare(a, b)
    assert any("slippage" in d for d in cmp.differences)


def test_compare_says_so_when_runs_are_directly_comparable(db, state):
    a = store.save(FakeResult(), state, db=db)
    b = store.save(FakeResult(), state, db=db)
    cmp = store.compare(a, b)
    assert cmp.differences == ["none - these two runs are directly comparable"]
    assert "configuration differences" in cmp.render()


def test_compare_survives_a_missing_metric(db, state):
    result = FakeResult()
    result.metrics.mar = float("nan")
    a = store.save(result, state, db=db)
    b = store.save(FakeResult(), state, db=db)
    out = store.compare(a, b).render()
    assert "MAR" in out            # rendered, not crashed
