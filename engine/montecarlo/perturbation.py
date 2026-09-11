"""Monte Carlo method 2 -- parameter perturbation.

The question this answers: is the winning parameter set sitting on a PLATEAU or
on a SPIKE? A plateau means neighbouring settings perform similarly, so the
edge survives being slightly wrong about the parameters. A spike means the
winner is an artefact of the search -- it found the one combination that fit
this particular history, and any real deployment will miss it.

This is the main defence against curve-fitting, so the verdict is stated in
plain language rather than left as a number to interpret.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PerturbationResult:
    base_params: dict
    base_score: float
    scores: dict[tuple, float]          # offset tuple -> objective
    param_names: list[str]
    steps: int
    objective: str
    neighbour_median: float
    neighbour_positive: float
    survival: float
    verdict: str
    reason: str
    surface: np.ndarray | None = None   # 2-D when exactly two params vary
    axes: list[list] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  PARAMETER PERTURBATION   +/-{self.steps} steps, {len(self.scores):,} runs",
            f"  objective                {self.objective}",
            "",
            f"  winner                   {self.base_score:>10.3f}   {self.base_params}",
            f"  neighbour median         {self.neighbour_median:>10.3f}",
            f"  survival ratio           {self.survival:>10.1%}   "
            "(neighbour median / winner)",
            f"  neighbours still positive{self.neighbour_positive:>10.1%}",
            "",
            f"  VERDICT  {self.verdict}",
            f"           {self.reason}",
        ]
        return "\n".join(lines)

    def render_surface(self, width: int = 9) -> str:
        """Text heat grid for the two-parameter case. Brightness, not colour."""
        if self.surface is None:
            return "  (surface shown only when exactly two parameters vary)"
        shades = " .:-=+*#@"
        finite = self.surface[np.isfinite(self.surface)]
        if finite.size == 0:
            return "  (no finite scores)"
        lo, hi = float(finite.min()), float(finite.max())
        rows = [f"  {self.param_names[1]} across, {self.param_names[0]} down"]
        for r in range(self.surface.shape[0]):
            cells = []
            for c in range(self.surface.shape[1]):
                v = self.surface[r, c]
                if not np.isfinite(v):
                    cells.append(" ")
                else:
                    f = 0.0 if hi <= lo else (v - lo) / (hi - lo)
                    cells.append(shades[int(f * (len(shades) - 1))])
            rows.append("    " + " ".join(cells) + f"   {self.axes[0][r]}")
        rows.append("    " + " ".join(str(a)[:1] for a in self.axes[1]))
        del width
        return "\n".join(rows)


def _clamp(value, spec: dict):
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def perturb(
    evaluate,
    base_params: dict,
    param_specs: list[dict],
    steps: int = 2,
    objective: str = "mar",
    vary: list[str] | None = None,
) -> PerturbationResult:
    """Jitter each parameter +/- `steps` of its own step size and re-evaluate.

    `evaluate(params) -> float` returns the objective. Offsets are taken in the
    parameter's declared step, not in raw units, so a parameter with a step of 5
    moves by 5 -- perturbing by a raw 1 would test a change the search grid
    could never have made.
    """
    specs = {s["name"]: s for s in param_specs}
    names = vary or [
        n for n in base_params
        if n in specs and specs[n].get("step") and specs[n]["type"] in ("int", "float")
    ]
    if not names:
        raise ValueError("no numeric parameters with a declared step to perturb")

    offsets = range(-steps, steps + 1)
    scores: dict[tuple, float] = {}
    for combo in itertools.product(offsets, repeat=len(names)):
        params = dict(base_params)
        for name, off in zip(names, combo):
            spec = specs[name]
            value = base_params[name] + off * spec["step"]
            value = _clamp(value, spec)
            params[name] = int(value) if spec["type"] == "int" else float(value)
        scores[combo] = float(evaluate(params))

    centre = tuple([0] * len(names))
    base_score = scores[centre]
    neighbours = [v for k, v in scores.items() if k != centre]
    finite = [v for v in neighbours if np.isfinite(v)]

    median = float(np.median(finite)) if finite else float("nan")
    positive = float(np.mean([v > 0 for v in finite])) if finite else 0.0
    survival = median / base_score if base_score not in (0.0,) and np.isfinite(base_score) else float("nan")

    # Thresholds are judgement, so they are stated rather than hidden: a
    # neighbourhood keeping at least half the winner's objective, with most of
    # it still profitable, is a plateau. Anything less is a spike -- the search
    # found one lucky cell.
    if not np.isfinite(survival):
        verdict, reason = "INCONCLUSIVE", "the winner's objective is undefined or zero"
    elif survival >= 0.5 and positive >= 0.7:
        verdict = "PLATEAU"
        reason = (f"neighbours keep {survival:.0%} of the winner and {positive:.0%} stay "
                  "positive, so the edge survives being slightly wrong about the parameters")
    elif survival >= 0.5:
        verdict = "MIXED"
        reason = (f"neighbours keep {survival:.0%} of the winner but only {positive:.0%} stay "
                  "positive, so the level holds while the sign does not")
    else:
        verdict = "SPIKE"
        reason = (f"neighbours keep only {survival:.0%} of the winner, so it is an isolated "
                  "cell -- the search fit this history rather than finding an edge")

    surface = None
    axes: list[list] = []
    if len(names) == 2:
        n = 2 * steps + 1
        surface = np.full((n, n), np.nan)
        for (a, b), v in scores.items():
            surface[a + steps, b + steps] = v
        axes = [
            [base_params[names[0]] + o * specs[names[0]]["step"] for o in offsets],
            [base_params[names[1]] + o * specs[names[1]]["step"] for o in offsets],
        ]

    return PerturbationResult(
        base_params=base_params, base_score=base_score, scores=scores,
        param_names=names, steps=steps, objective=objective,
        neighbour_median=median, neighbour_positive=positive, survival=survival,
        verdict=verdict, reason=reason, surface=surface, axes=axes,
    )
