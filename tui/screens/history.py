"""Run history browser, and a side-by-side comparison of two runs.

Comparison lists the configuration differences rather than leaving them to be
noticed. Two results computed with different costs or different windows are not
directly comparable, and saying so is the point of the feature.
"""

from __future__ import annotations

from datetime import datetime

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from ..theme import GROUND, HERO, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, rule, title


class HistoryScreen(LabScreen):
    SECTION = "MORE"
    SCREEN_NAME = "history"
    HINT = "up/down run    SPACE mark for compare    X export trades    Q quit"

    BINDINGS = LabScreen.BINDINGS + [
        ("up", "move(-1)", "Previous"),
        ("down", "move(1)", "Next"),
        ("space", "mark", "Mark for compare"),
        ("x", "export_trades", "Export trades"),
    ]

    row: reactive[int] = reactive(0)

    def __init__(self, **kw):
        self.records: list = []
        self.marked: list = []
        super().__init__(**kw)

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("MORE  /  RUN HISTORY")
            yield title("Past runs")
            yield desc("Each run stores the configuration and the costs it was "
                       "computed with. Mark two to compare.")
            yield rule()
            with VerticalScroll():
                yield Static(id="runlist")
                yield Static(id="comparison")

    def on_mount(self) -> None:
        super().on_mount()
        self.call_after_refresh(self.reload)

    def reload(self) -> None:
        from engine.store import runs as store

        self.records = store.recent(limit=50)
        self.row = min(self.row, max(0, len(self.records) - 1))
        self.render_all()

    def action_move(self, delta: int) -> None:
        if self.records:
            self.row = (self.row + delta) % len(self.records)

    def watch_row(self, _r: int) -> None:
        if self.is_mounted:
            self.render_all()

    def action_mark(self) -> None:
        if not self.records:
            return
        run_id = self.records[self.row].run_id
        if run_id in self.marked:
            self.marked.remove(run_id)
        else:
            self.marked.append(run_id)
            self.marked = self.marked[-2:]      # compare is always a pair
        self.render_all()

    def action_export_trades(self) -> None:
        """CSV of the selected run's trade log, on demand."""
        if not self.records:
            return
        from pathlib import Path

        from engine.store import runs as store

        rec = self.records[self.row]
        try:
            df = store.load_trades(rec)
            out = Path("runs") / ("trades_" + rec.short_id + ".csv")
            df.to_csv(out, index=False)
            self.notify(str(len(df)) + " trades written to " + str(out),
                        title="Export")
        except Exception as exc:
            self.notify(str(exc), title="Export failed", severity="error")

    # ---- rendering

    def render_all(self) -> None:
        self.render_list()
        self.render_comparison()

    def render_list(self) -> None:
        t = Text()
        head = ("   " + "RUN".ljust(10) + "WHEN".ljust(18) + "STRATEGY".ljust(16)
                + "FOLDS".rjust(6) + "TRADES".rjust(8) + "NET".rjust(12)
                + "MAR".rjust(8))
        t.append("  " + spaced("RUNS") + "\n\n", Style(color=LABEL))
        t.append("  " + head + "\n", Style(color=LABEL))
        if not self.records:
            t.append("\n  No runs yet. Press T to configure one and ENTER to start.\n",
                     Style(color=TEXT_DIM))
        for i, r in enumerate(self.records):
            live = i == self.row
            mark = "*" if r.run_id in self.marked else " "
            when = datetime.fromtimestamp(r.created_at).strftime("%Y-%m-%d %H:%M")
            mar = "-" if r.mar is None else format(r.mar, ".2f")
            t.append("  " + mark + "  ", Style(color=TEXT))
            t.append(r.short_id.ljust(10),
                     Style(color=TEXT if live else TEXT_DIM, bold=live))
            t.append(when.ljust(18), Style(color=TEXT_DIM))
            t.append(r.strategy[:15].ljust(16), Style(color=TEXT_DIM))
            t.append(str(r.n_folds).rjust(6), Style(color=TEXT_DIM))
            t.append(format(r.n_trades, ",").rjust(8), Style(color=TEXT_DIM))
            t.append(format(r.net_pnl, ",.0f").rjust(12), Style(color=TEXT))
            t.append(mar.rjust(8), Style(color=TEXT_DIM))
            t.append("\n")
        if self.records:
            t.append("\n  ")
            t.append(" SPACE ", Style(bgcolor=HERO, color=GROUND, bold=True))
            t.append("  mark for compare (" + str(len(self.marked)) + "/2 marked)",
                     Style(color=TEXT_DIM))
        self.set_text("#runlist", t)

    def render_comparison(self) -> None:
        from engine.store import runs as store

        t = Text()
        if len(self.marked) == 2:
            a = store.get(self.marked[0])
            b = store.get(self.marked[1])
            if a and b:
                t.append("\n  " + spaced("COMPARE") + "\n\n", Style(color=LABEL))
                t.append(store.compare(a, b).render() + "\n", Style(color=TEXT_DIM))
        self.set_text("#comparison", t)
