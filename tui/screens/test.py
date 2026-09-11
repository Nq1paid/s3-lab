"""Test screen -- everything needed to define a run, none of it in a file.

Arrow keys move and change values; Enter starts. Nothing here requires knowing
a command, and every value is written back to the config as it changes.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..theme import CAPTION, HERO, GROUND, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, field_row, rule, title

OBJECTIVES = ("mar", "sharpe", "profit_factor", "expectancy_x_trades")
MODES = ("rolling", "anchored")
GRID_PRESETS = {
    "or_minutes": ([15, 30], [15, 30, 45, 60], [5, 10, 15, 20, 30, 45, 60, 90]),
    "stop_ticks": ([40, 60], [20, 40, 60, 80], [20, 30, 40, 50, 60, 80, 100, 120]),
    "target_r": ([2.0], [1.0, 2.0, 3.0], [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]),
}


class TestScreen(LabScreen):
    SECTION = "TEST"
    SCREEN_NAME = "test"
    HINT = "↑↓ field    ←→ change    ENTER run    D S T R E sections    Q quit"

    BINDINGS = LabScreen.BINDINGS + [
        ("up", "move(-1)", "Previous field"),
        ("down", "move(1)", "Next field"),
        ("left", "adjust(-1)", "Decrease"),
        ("right", "adjust(1)", "Increase"),
        ("enter", "start", "Start run"),
    ]

    row: reactive[int] = reactive(0)

    #: (key, label, hint)
    ROWS = (
        ("instrument", "INSTRUMENT", "series available in the cache"),
        ("mode", "WALK FORWARD", "rolling drops old data, anchored keeps it"),
        ("is_sessions", "IN SAMPLE", "trading sessions per fold"),
        ("oos_sessions", "OUT OF SAMPLE", "sessions tested per fold"),
        ("step_sessions", "STEP", "sessions between folds"),
        ("min_trades", "MIN TRADES", "below this a fold is disqualified"),
        ("objective", "OBJECTIVE", "what the in-sample search maximises"),
        ("or_minutes", "GRID / OR MINUTES", ""),
        ("stop_ticks", "GRID / STOP TICKS", ""),
        ("target_r", "GRID / TARGET R", ""),
    )

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("TEST  /  WALKFORWARD")
            yield title("Define the run")
            yield desc("Parameter ranges come from the strategy's own schema. "
                       "Nothing here lives in a config file.")
            yield rule()
            yield Static(id="fields")
            yield rule()
            yield Static(id="summary")

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_rows()

    # ---- values

    def value_of(self, key: str) -> str:
        t = self.state.test
        if key in GRID_PRESETS:
            vals = t.grid.get(key, [])
            return ", ".join(str(v) for v in vals) if vals else "—"
        if key == "instrument":
            return f"{t.instrument}  {t.bar_minutes}m  {t.session}"
        return str(getattr(t, key))

    def action_move(self, delta: int) -> None:
        self.row = (self.row + delta) % len(self.ROWS)

    def action_adjust(self, delta: int) -> None:
        t = self.state.test
        key = self.ROWS[self.row][0]

        if key == "instrument":
            series = self.state.available_series()
            if series:
                names = [s.split("_")[0] for s in series]
                i = (names.index(t.instrument) + delta) % len(names) if t.instrument in names else 0
                t.instrument = names[i]
                parts = series[i].split("_")
                t.bar_minutes = int(parts[1].rstrip("m"))
                t.session = parts[2].upper()
        elif key == "mode":
            t.mode = MODES[(MODES.index(t.mode) + delta) % len(MODES)]
        elif key == "objective":
            t.objective = OBJECTIVES[(OBJECTIVES.index(t.objective) + delta) % len(OBJECTIVES)]
        elif key in GRID_PRESETS:
            presets = GRID_PRESETS[key]
            current = t.grid.get(key, [])
            idx = next((i for i, p in enumerate(presets) if p == current), 0)
            t.grid[key] = list(presets[(idx + delta) % len(presets)])
        else:
            step = {"is_sessions": 25, "oos_sessions": 25, "step_sessions": 25,
                    "min_trades": 5}[key]
            setattr(t, key, max(step, getattr(t, key) + delta * step))

        self.state.save()
        self.refresh_rows()

    def watch_row(self, _row: int) -> None:
        if self.is_mounted:
            self.refresh_rows()

    def render_all(self) -> None:
        self.refresh_rows()

    def refresh_rows(self) -> None:
        body = Text()
        for i, (key, label, hint) in enumerate(self.ROWS):
            body.append_text(field_row(label, self.value_of(key), hint,
                                       selected=(i == self.row)))
            body.append("\n")
        self.set_text("#fields", body)

        t = self.state.test
        combos = t.grid_size
        est = self._estimate(combos)
        s = Text()
        s.append("  " + spaced("GRID") + "   ", Style(color=LABEL))
        s.append(f"{combos:,} combinations", Style(color=TEXT))
        s.append("      " + spaced("ESTIMATE") + "   ", Style(color=LABEL))
        s.append(est, Style(color=TEXT))
        s.append("\n\n  ")
        s.append("  ENTER  ", Style(bgcolor=HERO, color=GROUND, bold=True))
        s.append("  start the run", Style(color=TEXT_DIM))
        s.append("\n\n  ")
        s.append("Slippage is the only cost applied. No commission or fees — "
                 "every P&L figure is before brokerage.", Style(color=CAPTION))
        self.set_text("#summary", s)

    def _estimate(self, combos: int) -> str:
        """Show the cost before committing, so a sweep is never a surprise."""
        from pathlib import Path

        parquet = Path(self.state.parquet())
        if not parquet.exists():
            return "— no cached series for this selection"
        t = self.state.test
        # ~0.5s per million bars per combo on the JIT'd path, measured.
        approx_folds = 15
        bars_per_fold = t.is_sessions * 1365
        total = approx_folds * combos * bars_per_fold
        seconds = total / 6_600_000 / 12          # 12 effective workers
        if seconds < 90:
            return f"~{seconds:.0f}s across {approx_folds} folds"
        return f"~{seconds / 60:.1f} min across {approx_folds} folds"

    def action_start(self) -> None:
        from pathlib import Path

        if not Path(self.state.parquet()).exists():
            self.notify(f"No cached series at {self.state.parquet()} — import data first",
                        title="Cannot start", severity="error")
            return
        self.app.show("run", start=True)
