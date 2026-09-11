"""In-terminal charts, stripped to the data.

No axes, no gridlines, no tick marks, no legend. A curve is allowed exactly
four annotations: the maximum in the top-left, the start and end dates in the
bottom corners, a hairline at starting equity, and faint verticals at fold
boundaries.

Curves are CONTINUOUS. Every x-column is occupied and consecutive samples are
joined, because a curve drawn as one dot per sample is a scatter and reads as a
dot-matrix printout. A dim halo is drawn in the braille cells adjacent to the
line; a bright stroke with a dim outline is what makes it read as lit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
from rich.style import Style
from rich.text import Text

from ..theme import (
    BAR_BASE, CAPTION, HAIRLINE, HAIRLINE_LIT, HERO, LABEL, TEXT, TEXT_DIM, spaced,
)

# Braille dot bits: column 0 is dots 1,2,3,7 and column 1 is dots 4,5,6,8.
_DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
_BRAILLE_BASE = 0x2800
_BLOCKS = " ▁▂▃▄▅▆▇█"


class BrailleCanvas:
    """A dot grid addressed in dots, rendered in character cells."""

    def __init__(self, cells_w: int, cells_h: int):
        self.cw, self.ch = max(1, cells_w), max(1, cells_h)
        self.w, self.h = self.cw * 2, self.ch * 4
        self._cells = [[0] * self.cw for _ in range(self.ch)]

    def set(self, x: int, y: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self._cells[y // 4][x // 2] |= _DOTS[x % 2][y % 4]

    def line(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Bresenham, so a steep segment stays connected instead of dotted."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set(x0, y0)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def hline(self, y: int) -> None:
        for x in range(self.w):
            self.set(x, y)

    def vline(self, x: int) -> None:
        for y in range(self.h):
            self.set(x, y)

    def char(self, row: int, col: int) -> str:
        bits = self._cells[row][col]
        return chr(_BRAILLE_BASE + bits) if bits else ""

    def occupied(self, row: int, col: int) -> bool:
        return bool(self._cells[row][col])


def _resample(values: np.ndarray, width_dots: int) -> np.ndarray:
    """One y per dot-column, interpolating so no column is ever empty.

    Downsampling by picking every Nth sample leaves gaps at the ends and drops
    turning points; interpolating across the full dot width guarantees a
    continuous stroke at any chart size.
    """
    n = len(values)
    if n == 1:
        return np.repeat(values, width_dots)
    src = np.linspace(0.0, 1.0, n)
    dst = np.linspace(0.0, 1.0, width_dots)
    return np.interp(dst, src, values)


def _plot(canvas: BrailleCanvas, values: np.ndarray, lo: float, hi: float) -> None:
    if len(values) < 2 or hi <= lo:
        return
    vals = _resample(np.asarray(values, dtype="float64"), canvas.w)
    ys = np.clip(((hi - vals) / (hi - lo) * (canvas.h - 1)).astype(int), 0, canvas.h - 1)
    for x in range(canvas.w - 1):
        canvas.line(x, int(ys[x]), x + 1, int(ys[x + 1]))


def equity_curve(
    values,
    width: int = 100,
    height: int = 14,
    start_ts: int | None = None,
    end_ts: int | None = None,
    tz: str = "America/Chicago",
    baseline: float | None = None,
    boundaries: list[float] | None = None,
    comparison=None,
    value_fmt: str = "{:,.0f}",
) -> Text:
    """Continuous braille curve with a dim halo and four annotations.

    `comparison` draws a second, dimmer series -- the buy-and-hold baseline --
    on the same scale, so the two are read against each other rather than from
    separate axes.
    """
    values = np.asarray(values, dtype="float64")
    if len(values) < 2:
        return Text(NO_DATA_TEXT, style=Style(color=LABEL))

    lo, hi = float(values.min()), float(values.max())
    if comparison is not None:
        comparison = np.asarray(comparison, dtype="float64")
        lo, hi = min(lo, float(comparison.min())), max(hi, float(comparison.max()))
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    if hi <= lo:
        hi = lo + 1.0

    main = BrailleCanvas(width, height)
    _plot(main, values, lo, hi)

    # Halo: dilate the stroke outwards so the dim outline lands in the CELLS
    # adjacent to the line. A one-dot offset is not enough -- a cell is four
    # dots tall, so it stays inside the same cell and is masked by the bright
    # stroke that owns it.
    halo = BrailleCanvas(width, height)
    vals = _resample(values, main.w)
    ys = np.clip(((hi - vals) / (hi - lo) * (main.h - 1)).astype(int), 0, main.h - 1)
    for x in range(main.w):
        y = int(ys[x])
        for dx in (-1, 0, 1):
            for dy in (-3, -2, 2, 3):
                halo.set(x + dx, y + dy)

    comp = None
    if comparison is not None:
        comp = BrailleCanvas(width, height)
        _plot(comp, comparison, lo, hi)

    rule = None
    if baseline is not None:
        rule = BrailleCanvas(width, height)
        rule.hline(int((hi - baseline) / (hi - lo) * (rule.h - 1)))

    marks = None
    if boundaries:
        marks = BrailleCanvas(width, height)
        for frac in boundaries:
            marks.vline(int(np.clip(frac, 0.0, 1.0) * (marks.w - 1)))

    st_main = Style(color=TEXT)
    st_halo = Style(color=LABEL)
    st_comp = Style(color=TEXT_DIM)
    st_rule = Style(color=HAIRLINE_LIT)
    st_mark = Style(color=HAIRLINE)
    peak = value_fmt.format(float(values.max()))
    st_label = Style(color=LABEL)

    out = Text()
    for row in range(height):
        for col in range(width):
            if row == 0 and col < len(peak):
                out.append(peak[col], st_label)
                continue
            if ch := main.char(row, col):
                out.append(ch, st_main)
            elif comp is not None and comp.occupied(row, col):
                out.append(comp.char(row, col), st_comp)
            elif halo.occupied(row, col):
                out.append(halo.char(row, col), st_halo)
            elif rule is not None and rule.occupied(row, col):
                out.append(rule.char(row, col), st_rule)
            elif marks is not None and marks.occupied(row, col):
                out.append(marks.char(row, col), st_mark)
            else:
                out.append(" ")
        out.append("\n")

    if start_ts is not None and end_ts is not None:
        zone = ZoneInfo(tz)
        a = datetime.fromtimestamp(start_ts, timezone.utc).astimezone(zone).strftime("%Y-%m-%d")
        b = datetime.fromtimestamp(end_ts, timezone.utc).astimezone(zone).strftime("%Y-%m-%d")
        out.append(a.ljust(max(1, width - len(b))) + b, Style(color=CAPTION))
    return out


NO_DATA_TEXT = "— no data"


def _ramp(top: str, bottom: str, steps: int) -> list[str]:
    tr, tg, tb = (int(top[i:i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    return [
        f"#{int(tr + (br - tr) * i / max(steps - 1, 1)):02x}"
        f"{int(tg + (bg - tg) * i / max(steps - 1, 1)):02x}"
        f"{int(tb + (bb - tb) * i / max(steps - 1, 1)):02x}"
        for i in range(max(steps, 1))
    ]


def distribution(values, bins: int = 40, height: int = 8) -> Text:
    """Vertical bars, bright at each bar's own top fading to near-black at its base.

    The gradient is keyed to each BAR, not to the chart row. Keying it to the
    row leaves every short bar sitting entirely in the dark end of the ramp,
    invisible against black -- which is exactly what a distribution's tails are
    made of.
    """
    values = np.asarray(values, dtype="float64")
    if len(values) == 0:
        return Text(NO_DATA_TEXT, style=Style(color=LABEL))

    counts, _ = np.histogram(values, bins=bins)
    peak = counts.max() or 1
    steps = 64
    ramp = _ramp(HERO, BAR_BASE, steps)

    out = Text()
    for row in range(height):
        for c in counts:
            cells = c / peak * height
            top_row = height - cells
            depth = row - top_row
            if depth <= 0.0 and row + 1 <= top_row:
                out.append("  ")
                continue
            frac = 0.0 if cells <= 1 else min(max(depth / (cells - 1), 0.0), 1.0)
            shade = Style(color=ramp[int(frac * (steps - 1))])
            level = cells - (height - 1 - row)
            if level >= 1.0:
                out.append("█", shade)
            elif level > 0:
                out.append(_BLOCKS[max(1, int(level * 8))], shade)
            else:
                out.append(" ")
            out.append(" ")
        out.append("\n")
    return out


def diverging(
    labels: list[str],
    values: list[float],
    width: int = 100,
    row_markers: dict[str, int] | None = None,
    left_header: str = "LOSING",
    right_header: str = "WINNING",
    value_fmt: str = "{:,.0f}",
) -> Text:
    """Losing rows left of centre, winning right. Position, never colour.

    Labels sit in a centre column with bars growing outward and each value at
    its far edge. Bars carry a horizontal gradient, darkest at the axis and
    brightest at the tip, so length is reinforced by luminance.
    """
    if not values:
        return Text(NO_DATA_TEXT, style=Style(color=LABEL))

    scale = max(abs(v) for v in values) or 1.0
    label_w = max(len(l) for l in labels)
    markers = {v: k for k, v in (row_markers or {}).items()}
    gutter = max(
        len(value_fmt.format(-scale)) + 2,
        max((len(spaced(n)) for n in markers.values()), default=0) + 2,
    )
    half = max(6, (width - label_w - 2 * gutter) // 2)
    ramp = _ramp(HERO, BAR_BASE, 32)

    def bar_style(step: int, total: int, dim: bool) -> Style:
        f = 0.0 if total <= 1 else 1.0 - (step / (total - 1))
        idx = int(f * (len(ramp) - 1))
        return Style(color=ramp[min(idx + (8 if dim else 0), len(ramp) - 1)])

    out = Text()
    if left_header or right_header:
        out.append(spaced(left_header).rjust(gutter + half), Style(color=LABEL))
        out.append(" " * (label_w + 2))
        out.append(spaced(right_header), Style(color=LABEL))
        out.append("\n")

    for row, (lab, v) in enumerate(zip(labels, values)):
        n = int(abs(v) / scale * half)
        marked = row in markers
        rule = Style(color=HAIRLINE_LIT)

        if marked:
            out.append(spaced(markers[row]).ljust(gutter), Style(color=LABEL))
        elif v < 0:
            out.append(value_fmt.format(v).rjust(gutter - 1) + " ", Style(color=TEXT_DIM))
        else:
            out.append(" " * gutter)

        for col in range(half):
            dist = half - col
            if v < 0 and dist <= n:
                out.append("█", bar_style(dist - 1, n, dim=True))
            elif marked:
                out.append("─", rule)
            else:
                out.append(" ")

        out.append(" " + lab.center(label_w) + " ",
                   Style(color=TEXT if marked else LABEL))

        for col in range(half):
            if v > 0 and col < n:
                out.append("█", bar_style(col, n, dim=False))
            elif marked:
                out.append("─", rule)
            else:
                out.append(" ")

        if v > 0:
            out.append(" " + value_fmt.format(v), Style(color=TEXT))
        elif marked:
            out.append(" " + value_fmt.format(v), Style(color=TEXT_DIM))
        out.append("\n")
    return out

