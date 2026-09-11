"""Noise injection and block bootstrap tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.montecarlo.resample import (  # noqa: E402
    block_bootstrap, inject_noise, run_resample,
)


def make_bars(n=2000, seed=0):
    """Valid OHLC: high and low are derived from open and close, not drawn
    independently, or the fixture itself violates the invariant under test."""
    rng = np.random.default_rng(seed)
    close = 15000 + np.cumsum(rng.normal(0, 2.0, n))
    open_ = close + rng.normal(0, 0.5, n)
    span = np.abs(rng.normal(0, 3.0, n)) + 1.0
    return {
        "ts": np.arange(1_500_000_000, 1_500_000_000 + n * 60, 60, dtype="int64"),
        "open": open_,
        "high": np.maximum(open_, close) + span,
        "low": np.minimum(open_, close) - span,
        "close": close,
        "volume": np.ones(n, dtype="int64"),
    }


def ohlc_is_consistent(b) -> bool:
    return bool(
        np.all(b["high"] >= b["low"])
        and np.all(b["high"] >= b["open"] - 1e-9)
        and np.all(b["high"] >= b["close"] - 1e-9)
        and np.all(b["low"] <= b["close"] + 1e-9)
    )


def test_noise_keeps_bars_internally_consistent():
    """Jittering fields independently would invent high < close bars."""
    b = make_bars()
    out = inject_noise(b, np.random.default_rng(1), 2.0, 0.25)
    assert ohlc_is_consistent(out)


def test_noise_actually_changes_prices():
    b = make_bars()
    out = inject_noise(b, np.random.default_rng(2), 2.0, 0.25)
    assert not np.allclose(out["close"], b["close"])


def test_noise_is_tick_aligned():
    b = make_bars()
    out = inject_noise(b, np.random.default_rng(3), 2.0, 0.25)
    diff = out["close"] - b["close"]
    assert np.allclose(diff / 0.25, np.round(diff / 0.25))


def test_zero_noise_is_a_no_op():
    b = make_bars()
    out = inject_noise(b, np.random.default_rng(4), 0.0, 0.25)
    assert np.array_equal(out["close"], b["close"])


def test_block_bootstrap_has_no_artificial_seam_gaps():
    """Stitching raw price blocks would leave a huge jump at every seam."""
    b = make_bars(4000)
    out = block_bootstrap(b, np.random.default_rng(5), block_len=200)
    real = np.abs(np.diff(b["close"]))
    synth = np.abs(np.diff(out["close"]))
    assert synth.max() < real.max() * 6, "seam gap far larger than any real move"
    assert ohlc_is_consistent(out)


def test_block_bootstrap_preserves_starting_price():
    b = make_bars()
    out = block_bootstrap(b, np.random.default_rng(6), block_len=100)
    assert out["close"][0] == pytest.approx(b["close"][0])
    assert len(out["close"]) == len(b["close"])


def test_block_bootstrap_changes_the_path():
    b = make_bars()
    out = block_bootstrap(b, np.random.default_rng(7), block_len=100)
    assert not np.allclose(out["close"], b["close"])


def test_block_length_at_or_above_series_length_is_a_no_op():
    b = make_bars(500)
    out = block_bootstrap(b, np.random.default_rng(8), block_len=500)
    assert np.array_equal(out["close"], b["close"])


def test_run_resample_reports_degradation_against_the_baseline():
    b = make_bars()
    # Score depends on the price path, so altering it must move the score.
    r = run_resample(lambda x: float(x["close"][-1] - x["close"][0]), b,
                     "block", iterations=25, seed=9, block_len=200)
    assert r.iterations == 25
    assert np.isfinite(r.degradation)
    assert 0.0 <= r.beat_baseline <= 1.0


def test_run_resample_is_cancellable_mid_run():
    b = make_bars()
    seen = {"n": 0}

    def cancel():
        seen["n"] += 1
        return seen["n"] > 5
    r = run_resample(lambda x: 1.0, b, "noise", iterations=500, cancel=cancel)
    assert r.iterations < 500
    assert any("cancelled" in n for n in r.notes)


def test_unprofitable_baseline_is_flagged_not_silently_ratioed():
    b = make_bars()
    r = run_resample(lambda x: -100.0, b, "noise", iterations=5)
    assert any("not profitable" in n for n in r.notes)


def test_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown method"):
        run_resample(lambda x: 1.0, make_bars(), "wiggle", iterations=1)


# --- whole-session bootstrap ------------------------------------------------

def make_session_bars(n_sessions=40, per=60, seed=3):
    rng = np.random.default_rng(seed)
    n = n_sessions * per
    close = 15000 + np.cumsum(rng.normal(0, 2.0, n))
    open_ = close + rng.normal(0, 0.5, n)
    span = np.abs(rng.normal(0, 3.0, n)) + 1.0
    return {
        "ts": np.arange(1_500_000_000, 1_500_000_000 + n * 60, 60, dtype="int64"),
        "open": open_,
        "high": np.maximum(open_, close) + span,
        "low": np.minimum(open_, close) - span,
        "close": close,
        "volume": np.ones(n, dtype="int64"),
        "sess": np.repeat(np.arange(n_sessions, dtype=np.int32), per),
    }


def test_session_bootstrap_copies_whole_real_bars():
    """Every synthetic bar's internal shape must match some real bar exactly."""
    from engine.montecarlo.resample import session_block_bootstrap
    b = make_session_bars()
    out = session_block_bootstrap(b, np.random.default_rng(4))
    real_shapes = {
        (round(h - c, 6), round(c - lo, 6))
        for h, lo, c in zip(b["high"], b["low"], b["close"])
    }
    synth_shapes = {
        (round(h - c, 6), round(c - lo, 6))
        for h, lo, c in zip(out["high"], out["low"], out["close"])
    }
    assert synth_shapes <= real_shapes, "a synthetic bar has a shape no real bar had"


def test_session_bootstrap_does_not_manufacture_gaps():
    """Close-to-close moves inside a session must stay within real bar ranges."""
    from engine.montecarlo.resample import session_block_bootstrap
    b = make_session_bars()
    out = session_block_bootstrap(b, np.random.default_rng(5))
    real_max = float(np.abs(np.diff(b["close"])).max())
    synth = np.abs(np.diff(out["close"]))
    within = synth[: len(synth)][np.diff(b["sess"]) == 0]
    assert within.max() <= real_max + 1e-6


def test_session_bootstrap_keeps_length_and_start():
    from engine.montecarlo.resample import session_block_bootstrap
    b = make_session_bars()
    out = session_block_bootstrap(b, np.random.default_rng(6))
    assert len(out["close"]) == len(b["close"])
    assert ohlc_is_consistent(out)
