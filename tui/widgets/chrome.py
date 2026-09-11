"""Chrome: the mark, top bar, controls, right rail, fold table, bottom bar."""

from __future__ import annotations

from datetime import datetime

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ..theme import (
    BAR_BASE, CAPTION, GROUND, HAIRLINE, HERO, LABEL, NO_DATA, ROW_ACTIVE,
    TEXT, TEXT_DIM, spaced,
)

#: A lowercase serif s with the 3 raised on the top row -- the only element
#: permitted to simulate a larger size, and the only serif letterform here.
#:
#: The topology is what makes it read as an s rather than a c: each row carries
#: a bar plus the stroke connecting it to the next, and the strokes ALTERNATE
#: sides. A left-heavy middle row fills the left edge and it becomes a c.
S3_ROWS = ("▛▀▀³", "▀▀▜ ", "▄▄▟ ")


class S3Mark(Static):
    """The mark with version and wordmark. One widget, used everywhere."""

    def __init__(self, version: str = "v0.1", wordmark: str = "LAB", **kw):
        t = Text()
        t.append(S3_ROWS[0], Style(color=TEXT, bold=True))
        t.append(f"  {version}\n", Style(color=CAPTION))
        t.append(S3_ROWS[1], Style(color=TEXT, bold=True))
        t.append(f"  {wordmark}\n", Style(color=CAPTION))
        t.append(S3_ROWS[2], Style(color=TEXT, bold=True))
        super().__init__(t, **kw)


class TopBar(Widget):
    """Mark, instrument tabs, then right-aligned strategy / run / state / clock."""

    DEFAULT_CSS = "TopBar { height: 3; }"

    instruments: reactive[tuple[str, ...]] = reactive(("NQ", "MNQ", "ES", "MES"))
    active_instrument: reactive[str] = reactive("NQ")
    strategy: reactive[str] = reactive("")
    run_id: reactive[str] = reactive("")
    state: reactive[str] = reactive("DONE")
    clock: reactive[str] = reactive("")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)
        self._tick()

    def _tick(self) -> None:
        self.clock = datetime.now().strftime("%H:%M:%S")

    def render(self) -> Text:
        width = max(self.size.width, 80)

        right = Text()
        right.append(spaced("STRATEGY") + "  ", Style(color=LABEL))
        right.append(self.strategy or NO_DATA, Style(color=TEXT))
        right.append("     " + spaced("RUN") + "  /  ", Style(color=LABEL))
        right.append(self.run_id or NO_DATA, Style(color=TEXT))
        right.append("     ●  ", Style(color=TEXT_DIM))
        right.append(spaced(self.state), Style(color=TEXT_DIM))
        right.append("     ")
        right.append(self.clock, Style(color=TEXT_DIM))

        tabs = Text()
        for sym in self.instruments:
            if sym == self.active_instrument:
                tabs.append(sym, Style(color=TEXT, bold=True, underline=True))
            else:
                tabs.append(sym, Style(color=CAPTION))
            tabs.append("   ")

        t = Text()
        t.append(" " + S3_ROWS[0], Style(color=TEXT, bold=True))
        t.append(" v0.1\n", Style(color=CAPTION))
        t.append(" " + S3_ROWS[1], Style(color=TEXT, bold=True))
        t.append("  LAB    ", Style(color=CAPTION))
        t.append_text(tabs)
        t.append(" " * max(1, width - 16 - tabs.cell_len - right.cell_len))
        t.append_text(right)
        t.append("\n")
        t.append(" " + S3_ROWS[2], Style(color=TEXT, bold=True))
        return t


class ControlRow(Static):
    """Segmented view switch left; fold / slice selectors and Apply right.

    Rendered as one row rather than nested containers so the right-hand group
    sits flush right at any width without a second layout pass.
    """

    selected: reactive[str] = reactive("")

    def __init__(self, views: tuple[str, ...], selected: str, fold: str,
                 slice_: str, width: int = 140, **kw):
        self.views = views
        self.fold, self.slice_, self._w = fold, slice_, width
        super().__init__(**kw)
        self.selected = selected

    def render(self) -> Text:
        left = Text()
        for opt in self.views:
            if opt == self.selected:
                left.append(f"  {opt}  ", Style(bgcolor=HERO, color=GROUND, bold=True))
            else:
                left.append(f"  {opt}  ", Style(color=TEXT_DIM))
            left.append("  ")

        right = Text()
        right.append(spaced("FOLD") + "  ", Style(color=LABEL))
        right.append(f" {self.fold} ▾ ", Style(color=TEXT, bgcolor=ROW_ACTIVE))
        right.append("   " + spaced("SLICE") + "  ", Style(color=LABEL))
        right.append(f" {self.slice_} ", Style(color=TEXT, bgcolor=ROW_ACTIVE))
        right.append("   Apply", Style(color=TEXT_DIM))

        t = Text()
        t.append_text(left)
        t.append(" " * max(1, self._w - left.cell_len - right.cell_len - 4))
        t.append_text(right)
        return t


class RightRail(Vertical):
    """SCOPE panel beside the chart: label, heading, range, key/value rows."""

    def __init__(self, heading: str, meta: str, rows: list[tuple[str, str]],
                 note: str = "", label: str = "SCOPE", span: int = 27, **kw):
        self._label, self._heading, self._meta = label, heading, meta
        self._rows, self._note, self._span = rows, note, span
        super().__init__(id="rightrail", **kw)

    def compose(self) -> ComposeResult:
        yield Static(Text(spaced(self._label), Style(color=LABEL)), classes="raillabel")
        yield Static(Text(self._heading, Style(color=TEXT, bold=True)), classes="railheading")
        yield Static(Text(self._meta, Style(color=TEXT_DIM)), classes="railmeta")
        for key, value in self._rows:
            # The rail is fixed width; a pair longer than it ran off the edge
            # and the value vanished, which reads as missing data, not a bug.
            value = value if len(value) <= self._span - 6 else value[: self._span - 7] + "…"
            line = Text()
            line.append(key, Style(color=TEXT_DIM))
            line.append(" " * max(1, self._span - len(key) - len(value)))
            line.append(value, Style(color=TEXT))
            yield Static(line, classes="railrow")
        if self._note:
            yield Static(Text(self._note, Style(color=CAPTION)), classes="railnote")


def _ramp(top: str, bottom: str, steps: int) -> list[str]:
    tr, tg, tb = (int(top[i:i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    return [
        f"#{int(tr + (br - tr) * i / max(steps - 1, 1)):02x}"
        f"{int(tg + (bg - tg) * i / max(steps - 1, 1)):02x}"
        f"{int(tb + (bb - tb) * i / max(steps - 1, 1)):02x}"
        for i in range(max(steps, 1))
    ]


class FoldTable(Vertical):
    """Hairline between rows, no zebra, no interior verticals.

    Column widths come from the LETTER-SPACED header, not the data: a spaced
    header is about twice its own length, so sizing to the values leaves the
    header overflowing its neighbour and nothing lines up.

    The trailing sparkline is scaled to the largest absolute out-of-sample
    result, so relative size reads without a second axis.
    """

    COLS = ("FOLD", "OOS FROM", "OR / STOP", "IS MAR", "OOS RET",
            "OOS MAR", "WFE", "TRADES")
    GAP = 3
    SPARK = 14

    def __init__(self, rows: list[dict], **kw):
        self._rows = rows
        self._widths = [len(spaced(c)) for c in self.COLS]
        self._scale = max((abs(r["oos_net"]) for r in rows), default=1.0) or 1.0
        super().__init__(**kw)

    def _cells(self, r: dict) -> list[Text]:
        # Sign is carried by the triangle, never by colour.
        ret = Text()
        ret.append("▲ " if r["oos_ret"] >= 0 else "▼ ", Style(color=TEXT))
        ret.append(f"{abs(r['oos_ret']):.1f}%", Style(color=TEXT))
        return [
            Text(f"{r['index']:02d}", Style(color=TEXT, bold=True)),
            Text(r["oos_from"], Style(color=TEXT_DIM)),
            Text(f"{r['or']} / {r['stop']}", Style(color=TEXT_DIM)),
            Text(f"{r['is_mar']:.2f}", Style(color=TEXT_DIM)),
            ret,
            Text(f"{r['oos_mar']:.2f}", Style(color=TEXT)),
            Text(f"{r['wfe']:.2f}", Style(color=TEXT_DIM)),
            Text(f"{r['oos_n']}", Style(color=TEXT_DIM)),
        ]

    def _spark(self, value: float) -> Text:
        n = max(1, int(abs(value) / self._scale * self.SPARK))
        ramp = _ramp(HERO, BAR_BASE, n)
        t = Text()
        for i in range(n):
            t.append("▬", Style(color=ramp[i]))
        return t

    def compose(self) -> ComposeResult:
        head = Text("  ")
        for name, w in zip(self.COLS, self._widths):
            head.append(spaced(name).rjust(w), Style(color=LABEL))
            head.append(" " * self.GAP)
        yield Static(head, classes="tablehead")

        for r in self._rows:
            line = Text("  ")
            for cell, w in zip(self._cells(r), self._widths):
                line.append(" " * max(0, w - cell.cell_len))
                line.append_text(cell)
                line.append(" " * self.GAP)
            line.append_text(self._spark(r["oos_net"]))
            yield Static(line, classes="tablerow")


class BottomBar(Static):
    SECTIONS = ("DATA", "STRATEGIES", "TEST", "RUN", "RESULTS")

    def __init__(self, active: str = "RESULTS", hint: str = "", width: int = 150, **kw):
        t = Text(" ")
        for s in self.SECTIONS:
            if s == active:
                t.append(spaced(s), Style(color=TEXT, bold=True, underline=True))
            else:
                t.append(spaced(s), Style(color=CAPTION))
            t.append("   ")
        t.append("│   ", Style(color=HAIRLINE))
        t.append(spaced("MORE"), Style(color=CAPTION))
        if hint:
            t.append(" " * max(1, width - t.cell_len - len(hint) - 4))
            t.append(hint, Style(color=CAPTION))
        super().__init__(t, id="bottombar", **kw)
