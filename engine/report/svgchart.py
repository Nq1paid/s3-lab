"""SVG chart primitives for the HTML report.

The report is where the house style can be rendered exactly: real type scale,
a serif mark, anti-aliased strokes. The TUI approximates those with bold,
brightness and block art because a terminal has one font -- this does not.
"""

from __future__ import annotations

import math

import numpy as np


def _path(xs, ys) -> str:
    return "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))


def line_chart(
    series: list[tuple[np.ndarray, str, float]],
    width: int = 1040,
    height: int = 380,
    baseline: float | None = None,
    boundaries: list[float] | None = None,
    log: bool = False,
    pad_left: int = 8,
    pad_right: int = 8,
) -> tuple[str, float]:
    """Return (svg_inner, peak_of_first_series).

    `series` is (values, stroke colour, stroke width). A log scale is offered
    because the buy-and-hold baseline can be an order of magnitude larger than
    the strategy, and on a linear axis that flattens the strategy into a
    straight line -- legible only as "it lost", which hides its actual shape.
    """
    prepared = []
    for values, colour, w in series:
        v = np.asarray(values, dtype="float64")
        prepared.append((np.log10(np.maximum(v, 1.0)) if log else v, colour, w))

    lo = min(float(v.min()) for v, _, _ in prepared)
    hi = max(float(v.max()) for v, _, _ in prepared)
    if baseline is not None:
        b = math.log10(max(baseline, 1.0)) if log else baseline
        lo, hi = min(lo, b), max(hi, b)
    if hi <= lo:
        hi = lo + 1.0

    inner_w = width - pad_left - pad_right

    def y_of(v: float) -> float:
        return height - (v - lo) / (hi - lo) * height

    out: list[str] = []

    if boundaries:
        for frac in boundaries:
            x = pad_left + frac * inner_w
            out.append(
                f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
                f'stroke="#1C1C1F" stroke-width="1"/>'
            )

    if baseline is not None:
        by = y_of(math.log10(max(baseline, 1.0)) if log else baseline)
        out.append(
            f'<line x1="0" y1="{by:.1f}" x2="{width}" y2="{by:.1f}" '
            f'stroke="#2E2E33" stroke-width="1" stroke-dasharray="2 4"/>'
        )

    for v, colour, w in prepared:
        n = min(len(v), inner_w * 2)
        idx = np.linspace(0, len(v) - 1, n).astype(int)
        xs = pad_left + np.linspace(0, inner_w, n)
        ys = [y_of(float(x)) for x in v[idx]]
        out.append(
            f'<path d="{_path(xs, ys)}" fill="none" stroke="{colour}" '
            f'stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"/>'
        )

    peak = float(np.asarray(series[0][0]).max())
    return "\n".join(out), peak


def bar_chart(counts, width: int = 1040, height: int = 340, gap: int = 3) -> str:
    """Vertical bars with a per-bar gradient, bright at the top of each bar."""
    counts = np.asarray(counts, dtype="float64")
    peak = counts.max() or 1.0
    n = len(counts)
    bw = max(1.0, (width - gap * (n - 1)) / n)
    out = [
        '<defs><linearGradient id="barfade" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#FFFFFF"/>'
        '<stop offset="100%" stop-color="#141416"/></linearGradient></defs>'
    ]
    for i, c in enumerate(counts):
        h = c / peak * height
        x = i * (bw + gap)
        out.append(
            f'<rect x="{x:.2f}" y="{height - h:.2f}" width="{bw:.2f}" '
            f'height="{h:.2f}" fill="url(#barfade)"/>'
        )
    return "\n".join(out)


def diverging_rows(labels, values, width: int = 1040, row_h: int = 26) -> str:
    """Losing left of centre, winning right. Position, never colour."""
    values = list(values)
    scale = max((abs(v) for v in values), default=1.0) or 1.0
    axis = width * 0.42
    half = width * 0.36
    out = [
        f'<line x1="{axis}" y1="0" x2="{axis}" y2="{len(values)*row_h}" '
        f'stroke="#2E2E33" stroke-width="1"/>'
    ]
    for i, (lab, v) in enumerate(values and zip(labels, values) or []):
        y = i * row_h + 5
        w = abs(v) / scale * half
        if v < 0:
            out.append(
                f'<rect x="{axis - w:.1f}" y="{y}" width="{w:.1f}" height="{row_h-10}" '
                f'fill="#8E8E93"/>'
                f'<text x="{axis - w - 10:.1f}" y="{y + row_h - 13}" fill="#8E8E93" '
                f'text-anchor="end" class="mono sm">{v:,.0f}</text>'
            )
        else:
            out.append(
                f'<rect x="{axis:.1f}" y="{y}" width="{w:.1f}" height="{row_h-10}" '
                f'fill="#F2F2F3"/>'
                f'<text x="{axis + w + 10:.1f}" y="{y + row_h - 13}" fill="#F2F2F3" '
                f'class="mono sm">{v:,.0f}</text>'
            )
        out.append(
            f'<text x="{axis - half - 24:.1f}" y="{y + row_h - 13}" fill="#5A5A5F" '
            f'text-anchor="end" class="mono sm">{lab}</text>'
        )
    return "\n".join(out)
