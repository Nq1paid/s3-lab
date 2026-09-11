"""Parameter perturbation tests -- plateau versus spike."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.montecarlo.perturbation import perturb  # noqa: E402

SPECS = [
    {"name": "a", "type": "int", "default": 30, "min": 5, "max": 120, "step": 5},
    {"name": "b", "type": "int", "default": 40, "min": 10, "max": 200, "step": 5},
]
BASE = {"a": 30, "b": 40}


def test_a_broad_optimum_is_called_a_plateau():
    # Score barely varies with the parameters.
    r = perturb(lambda p: 2.0 - abs(p["a"] - 30) * 0.002, BASE, SPECS, steps=2)
    assert r.verdict == "PLATEAU"
    assert r.survival > 0.9


def test_an_isolated_optimum_is_called_a_spike():
    # Only the exact winner scores; everything around it collapses.
    def score(p):
        return 5.0 if (p["a"], p["b"]) == (30, 40) else 0.05
    r = perturb(score, BASE, SPECS, steps=2)
    assert r.verdict == "SPIKE"
    assert r.survival < 0.1
    assert "fit this history" in r.reason


def test_level_holding_but_sign_flipping_is_mixed():
    rng = np.random.default_rng(0)

    def score(p):
        # Same magnitude, but half the neighbourhood is negative.
        return 1.0 if rng.random() < 0.45 else -0.9
    r = perturb(score, BASE, SPECS, steps=2)
    assert r.verdict in ("MIXED", "SPIKE")


def test_offsets_move_by_the_declared_step_not_raw_units():
    seen = []

    def score(p):
        seen.append((p["a"], p["b"]))
        return 1.0
    perturb(score, BASE, SPECS, steps=1)
    assert (25, 35) in seen and (35, 45) in seen
    assert (29, 39) not in seen, "offset must use the parameter's step"


def test_values_are_clamped_to_the_declared_range():
    seen = []

    def score(p):
        seen.append(p["a"])
        return 1.0
    perturb(score, {"a": 5, "b": 40}, SPECS, steps=2)
    assert min(seen) >= 5, "must not propose a value below the declared minimum"


def test_run_count_is_the_full_neighbourhood():
    calls = []
    perturb(lambda p: calls.append(1) or 1.0, BASE, SPECS, steps=2)
    assert len(calls) == 25       # (2*2+1) ** 2


def test_two_parameters_produce_a_surface():
    r = perturb(lambda p: float(p["a"]), BASE, SPECS, steps=2)
    assert r.surface is not None and r.surface.shape == (5, 5)
    assert "across" in r.render_surface()


def test_undefined_winner_is_inconclusive_not_a_pass():
    r = perturb(lambda p: float("nan") if p == BASE else 1.0, BASE, SPECS, steps=1)
    assert r.verdict == "INCONCLUSIVE"


def test_rejects_parameters_with_no_step():
    with pytest.raises(ValueError, match="no numeric parameters"):
        perturb(lambda p: 1.0, {"x": 1}, [{"name": "x", "type": "choice", "options": [1]}])
