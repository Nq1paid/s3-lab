"""Trade-sequence bootstrap tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.montecarlo.bootstrap import bootstrap_trades  # noqa: E402


def test_shuffle_preserves_final_equity_exactly():
    """Reordering the same trades cannot change where you end up."""
    rng = np.random.default_rng(0)
    pnl = rng.normal(50, 500, 300)
    r = bootstrap_trades(pnl, iterations=500, method="shuffle", seed=1)
    assert np.allclose(r.final_equity, r.final_equity[0])
    assert r.final_equity[0] == pytest.approx(100_000 + pnl.sum())


def test_shuffle_still_varies_drawdown():
    """Sequence risk is exactly what shuffling is for."""
    rng = np.random.default_rng(2)
    pnl = rng.normal(0, 500, 400)
    r = bootstrap_trades(pnl, iterations=1000, method="shuffle", seed=3)
    assert r.max_drawdown.std() > 0, "drawdown should depend on trade order"


def test_resample_varies_final_equity():
    rng = np.random.default_rng(4)
    pnl = rng.normal(50, 500, 300)
    r = bootstrap_trades(pnl, iterations=1000, method="resample", seed=5)
    assert r.final_equity.std() > 0


def test_all_winners_never_ruins_and_all_losers_always_does():
    win = bootstrap_trades(np.full(200, 100.0), iterations=200, seed=6)
    assert win.risk_of_ruin == 0.0
    # Each loss is 1% of the account; 200 of them takes it far below half.
    lose = bootstrap_trades(np.full(200, -1000.0), iterations=200, seed=7)
    assert lose.risk_of_ruin == 1.0


def test_ruin_is_detected_mid_path_not_only_at_the_end():
    """A big loss then a bigger recovery still ruins the account on the way."""
    pnl = np.array([-60_000.0, -60_000.0, 200_000.0])
    r = bootstrap_trades(pnl, starting_equity=100_000, iterations=200,
                         method="shuffle", ruin_fraction=0.5, seed=8)
    assert r.final_equity[0] == pytest.approx(180_000.0)
    assert r.risk_of_ruin > 0.0, "ending rich does not undo having been ruined"


def test_longest_losing_streak_is_measured():
    pnl = np.array([-1.0] * 6 + [10.0] * 6)
    r = bootstrap_trades(pnl, iterations=300, method="shuffle", seed=9)
    assert r.longest_losing_streak.max() == 6
    assert r.longest_losing_streak.min() >= 1


def test_observed_path_is_reported_alongside_the_distribution():
    rng = np.random.default_rng(10)
    pnl = rng.normal(20, 300, 250)
    r = bootstrap_trades(pnl, iterations=500, seed=11)
    assert r.observed_final == pytest.approx(100_000 + pnl.sum())
    assert r.observed_max_dd >= 0.0


def test_thin_trade_count_is_flagged():
    r = bootstrap_trades(np.array([100.0, -50.0, 25.0]), iterations=100, seed=12)
    assert any("not a basis for any conclusion" in n for n in r.notes)


def test_percentile_bands_are_ordered():
    rng = np.random.default_rng(13)
    r = bootstrap_trades(rng.normal(10, 400, 300), iterations=2000, seed=14)
    b = r.bands(r.final_equity)
    assert b[5] <= b[25] <= b[50] <= b[75] <= b[95]


def test_empty_and_bad_method_raise():
    with pytest.raises(ValueError, match="no trades"):
        bootstrap_trades(np.array([]))
    with pytest.raises(ValueError, match="unknown method"):
        bootstrap_trades(np.array([1.0]), method="wiggle")


def test_iterations_are_honoured_across_chunk_boundaries():
    r = bootstrap_trades(np.array([10.0, -5.0, 3.0] * 20), iterations=2500,
                         chunk=1000, seed=15)
    assert len(r.final_equity) == 2500
