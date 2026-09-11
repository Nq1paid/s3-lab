"""Walk-forward slicing tests.

Fold boundaries are where look-ahead comes back if you are careless, so the
no-overlap guarantee is tested directly rather than trusted to the arithmetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.walkforward.splitter import (  # noqa: E402
    Fold, WalkForwardConfig, make_folds, slice_bars,
)


def sessions(n_sessions: int, bars_each: int = 10) -> np.ndarray:
    """Session id array with a fixed number of bars per session."""
    return np.repeat(np.arange(n_sessions, dtype=np.int32), bars_each)


def test_oos_never_overlaps_in_sample():
    sess = sessions(1000)
    for mode in ("rolling", "anchored"):
        cfg = WalkForwardConfig(mode=mode, is_sessions=200, oos_sessions=50, step_sessions=50)
        folds = make_folds(sess, cfg)
        assert folds
        for f in folds:
            assert f.oos_start >= f.is_end
            assert f.is_start < f.is_end < f.oos_end


def test_fold_rejects_an_overlapping_construction():
    """The dataclass refuses to exist in a leaking state."""
    with pytest.raises(ValueError, match="leak"):
        Fold(index=0, is_start=0, is_end=100, oos_start=50, oos_end=150)


def test_folds_start_and_end_on_session_boundaries():
    bars_each = 7
    sess = sessions(600, bars_each)
    cfg = WalkForwardConfig(is_sessions=100, oos_sessions=25, step_sessions=25)
    for f in make_folds(sess, cfg):
        for edge in (f.is_start, f.is_end, f.oos_start, f.oos_end):
            assert edge % bars_each == 0, "fold edge fell inside a session"


def test_rolling_drops_old_data_and_anchored_does_not():
    sess = sessions(1000)
    roll = make_folds(sess, WalkForwardConfig(mode="rolling", is_sessions=200,
                                              oos_sessions=50, step_sessions=50))
    anch = make_folds(sess, WalkForwardConfig(mode="anchored", is_sessions=200,
                                              oos_sessions=50, step_sessions=50))
    assert len(roll) == len(anch)
    assert roll[-1].is_start > roll[0].is_start, "rolling window should advance"
    assert all(f.is_start == 0 for f in anch), "anchored window should stay pinned"
    assert anch[-1].is_bars > anch[0].is_bars, "anchored in-sample should grow"


def test_rolling_in_sample_length_is_constant():
    sess = sessions(1000)
    folds = make_folds(sess, WalkForwardConfig(mode="rolling", is_sessions=200,
                                               oos_sessions=50, step_sessions=50))
    assert len({f.is_bars for f in folds}) == 1


def test_trailing_partial_window_is_dropped_not_reported_short():
    sess = sessions(260)
    folds = make_folds(sess, WalkForwardConfig(is_sessions=200, oos_sessions=50,
                                               step_sessions=50))
    assert len(folds) == 1
    assert folds[0].oos_end <= len(sess)
    assert len({f.oos_bars for f in folds}) == 1


def test_no_folds_when_data_is_too_short():
    assert make_folds(sessions(100), WalkForwardConfig(is_sessions=200, oos_sessions=50)) == []


def test_step_smaller_than_oos_produces_overlapping_oos_windows():
    """Allowed and sometimes wanted, but IS/OOS within a fold still cannot overlap."""
    sess = sessions(1000)
    folds = make_folds(sess, WalkForwardConfig(is_sessions=200, oos_sessions=100,
                                               step_sessions=50))
    assert folds[1].oos_start < folds[0].oos_end
    for f in folds:
        assert f.oos_start >= f.is_end


def test_slice_bars_keeps_arrays_aligned():
    n = 100
    bars = {
        "ts": np.arange(n, dtype="int64"),
        "close": np.arange(n, dtype="float64"),
        "sess": sessions(10, 10),
    }
    out = slice_bars(bars, 30, 50)
    assert all(len(v) == 20 for v in out.values())
    assert out["ts"][0] == 30 and out["ts"][-1] == 49


# --- grid construction ------------------------------------------------------

from engine.walkforward.runner import build_grid, full_range  # noqa: E402

SPECS = [
    {"name": "a", "type": "int", "default": 30, "min": 5, "max": 120, "step": 5},
    {"name": "b", "type": "int", "default": 40, "min": 10, "max": 200, "step": 5},
    {"name": "c", "type": "int", "default": 2, "min": 0, "max": 20, "step": 1},
    {"name": "d", "type": "int", "default": 1, "min": 1, "max": 10, "step": 1},
]


def test_unswept_parameters_are_held_at_their_default():
    """Expanding them silently turns a 4-combo sweep into thousands."""
    grid = build_grid(SPECS, {"a": [15, 30], "b": [40, 60]})
    assert len(grid) == 4
    assert {g["c"] for g in grid} == {2}
    assert {g["d"] for g in grid} == {1}


def test_no_overrides_means_one_combination_not_the_whole_space():
    assert len(build_grid(SPECS)) == 1


def test_sweep_all_restores_the_exhaustive_grid():
    expected = 1
    for s in SPECS:
        expected *= len(full_range(s))
    assert len(build_grid(SPECS, sweep_all=True)) == expected
    assert expected > 10_000, "fixture should show why this is not the default"


def test_overrides_win_over_sweep_all():
    grid = build_grid(SPECS, {"a": [15]}, sweep_all=True)
    assert {g["a"] for g in grid} == {15}


def test_grid_values_keep_their_declared_type():
    specs = [{"name": "x", "type": "float", "default": 2.0,
              "min": 0.5, "max": 3.0, "step": 0.5}]
    assert all(isinstance(v, float) for v in full_range(specs[0]))


# ---- the baseline must never be computed over a window it does not have

def test_buy_and_hold_refuses_a_window_the_data_does_not_cover():
    """A short baseline reads as "the" baseline and is not.

    Found by shipping a 4-year data sample beside a run that started in 2019:
    the comparison silently reported +340% where the truth over the full
    series was +447%, and nothing on screen said the window had moved.
    """
    import numpy as np
    import pytest

    from engine.core.baseline import InsufficientCoverage, buy_and_hold

    day = 24 * 3600
    # Two years of daily closes ending "now".
    ts = np.arange(1_700_000_000 - 730 * day, 1_700_000_000, day, dtype="int64")
    close = np.linspace(10_000.0, 20_000.0, len(ts))

    # A window inside the data is fine.
    inside = buy_and_hold(ts, close, int(ts[10]), int(ts[-10]), 20.0)
    assert len(inside) >= 2

    # A window starting years before the first bar is not.
    with pytest.raises(InsufficientCoverage) as err:
        buy_and_hold(ts, close, int(ts[0]) - 1500 * day, int(ts[-1]), 20.0)
    assert "cached series covers" in str(err.value)

    # Nor one ending after the last bar.
    with pytest.raises(InsufficientCoverage):
        buy_and_hold(ts, close, int(ts[0]), int(ts[-1]) + 400 * day, 20.0)


def test_a_missing_baseline_reaches_the_view_model_as_none_not_zero():
    """Absent must not render as 0.00, which reads as a real measurement."""
    import json
    import numpy as np
    import pathlib
    import tempfile

    from engine.analysis.view import summarise

    src = pathlib.Path("runs/last_oos_trades.json")
    if not src.exists():
        import pytest
        pytest.skip("no run on disk")

    d = json.loads(src.read_text(encoding="utf-8"))
    # Shift the run a decade into the future; no cached series covers it.
    shift = 10 * 365 * 24 * 3600
    d["entry_ts"] = [t + shift for t in d["entry_ts"]]
    d["exit_ts"] = [t + shift for t in d["exit_ts"]]
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "shifted.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        v = summarise(str(p), iterations=200)

    assert v["buy_hold_return_pct"] is None
    assert v["excess_pp"] is None
    assert v["corr_to_long"] is None
    assert v["buy_hold"] is None
    assert "cached series covers" in v["baseline_note"]
    # everything that does not depend on the baseline still works
    assert v["n_trades"] > 0 and not np.isnan(v["mar"])
