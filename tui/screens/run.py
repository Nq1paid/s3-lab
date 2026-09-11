"""Run screen -- live progress, a trade tape, and a cancel that works.

The walk-forward runs on a worker thread so the UI stays responsive. Cancelling
stops consuming folds and tears down the worker pool, and the result says how
many folds actually completed rather than presenting a partial curve as whole.
"""

from __future__ import annotations

import time

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static
from textual.worker import get_current_worker

from ..theme import GROUND, HAIRLINE, HERO, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, rule, title

BAR_W = 60


class RunScreen(LabScreen):
    SECTION = "RUN"
    SCREEN_NAME = "run"
    HINT = "C  cancel    E  results    D S T R E sections    Q quit"

    BINDINGS = LabScreen.BINDINGS + [("c", "cancel", "Cancel run")]

    done: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    status: reactive[str] = reactive("idle")

    def __init__(self, start: bool = False, **kw):
        self._start = start
        self._cancelled = False
        self._t0 = 0.0
        self._log: list[str] = []
        super().__init__(**kw)

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("RUN  /  WALKFORWARD")
            yield title("Running")
            yield desc("Folds are optimised in parallel. Cancel stops after the "
                       "folds already finished.")
            yield rule()
            yield Static(id="progress")
            yield Static(id="tape")

    def on_mount(self) -> None:
        super().on_mount()
        self.render_progress()
        if self._start:
            self.start()

    # ---- the run

    def start(self) -> None:
        self.status = "running"
        self.app.run_state = "RUNNING"
        self._t0 = time.perf_counter()
        self._cancelled = False
        self.log_line("loading cached bars")
        self.run_walkforward()

    def action_cancel(self) -> None:
        if self.status == "running":
            self._cancelled = True
            self.status = "cancelling"
            self.log_line("cancel requested — finishing the fold in flight")
            self.render_progress()

    def log_line(self, text: str) -> None:
        self._log.append(text)
        if self.is_mounted:
            self.render_tape()

    def _progress(self, done: int, total: int) -> None:
        self.app.call_from_thread(self._set_progress, done, total)

    def _set_progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total
        self.render_progress()

    def _finish(self, result, error: str = "") -> None:
        elapsed = time.perf_counter() - self._t0
        self.app.run_state = "FAILED" if error else "DONE"
        self.status = "failed" if error else ("cancelled" if self._cancelled else "done")
        if error:
            self.log_line(f"FAILED: {error}")
        else:
            self.app.last_result = result
            try:
                from engine.store import runs as store

                rec = store.save(result, self.state)
                self.app.run_id = rec.short_id
                self.log_line("saved to history as " + rec.short_id)
            except Exception as exc:
                # A history failure must not destroy the result in memory.
                self.log_line("! could not save to history: " + str(exc))
            m = result.metrics
            self.log_line(f"{len(result.folds)} folds, {m.n_trades:,} OOS trades "
                          f"in {elapsed:.1f}s")
            for w in result.warnings:
                self.log_line(f"! {w}")
            self.log_line("press E for results")
        self.render_progress()

    def run_walkforward(self) -> None:
        self.run_worker(self._work, thread=True, exclusive=True)

    def _work(self) -> None:
        from engine.core.simulator import ExecConfig
        from engine.data.annotate import RTH_CLOSE_MIN
        from engine.walkforward.runner import build_grid, run_walkforward
        from engine.walkforward.splitter import WalkForwardConfig
        from strategies.reference import orb

        worker = get_current_worker()
        t = self.state.test
        try:
            grid = build_grid(orb.DESCRIBE["params"], overrides=t.grid)
            wf = WalkForwardConfig(mode=t.mode, is_sessions=t.is_sessions,
                                   oos_sessions=t.oos_sessions,
                                   step_sessions=t.step_sessions,
                                   min_trades_per_fold=t.min_trades)
            result = run_walkforward(
                self.state.parquet(), t.strategy, self.state.instrument(), grid,
                wf_cfg=wf,
                exec_cfg=ExecConfig(slippage_ticks=self.state.slippage_ticks,
                                    starting_equity=self.state.starting_equity),
                objective=t.objective, flatten_minute=RTH_CLOSE_MIN,
                progress=self._progress,
                cancel=lambda: self._cancelled or worker.is_cancelled,
            )
        except Exception as exc:                    # surfaced, never swallowed
            self.app.call_from_thread(self._finish, None, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._finish, result)

    # ---- rendering

    def render_progress(self) -> None:
        if not self.is_mounted:
            return
        t = Text()
        frac = (self.done / self.total) if self.total else 0.0
        filled = int(frac * BAR_W)
        t.append("  ")
        t.append("█" * filled, Style(color=TEXT))
        t.append("░" * (BAR_W - filled), Style(color=HAIRLINE))
        t.append(f"  {frac:5.0%}\n\n", Style(color=TEXT))

        elapsed = time.perf_counter() - self._t0 if self._t0 else 0.0
        eta = (elapsed / frac - elapsed) if frac > 0.02 else 0.0
        for label, value in (
            ("STATUS", self.status),
            ("FOLD", f"{self.done} / {self.total}" if self.total else "—"),
            ("ELAPSED", f"{elapsed:5.1f}s" if self._t0 else "—"),
            ("ETA", f"{eta:5.1f}s" if eta else "—"),
        ):
            t.append("  " + spaced(label).ljust(14), Style(color=LABEL))
            t.append(str(value) + "\n", Style(color=TEXT))

        if self.status == "done":
            t.append("\n  ")
            t.append("  E  ", Style(bgcolor=HERO, color=GROUND, bold=True))
            t.append("  open results", Style(color=TEXT_DIM))
        self.query_one("#progress", Static).update(t)

    def render_tape(self) -> None:
        t = Text()
        for line in self._log[-12:]:
            t.append("  " + line + "\n", Style(color=TEXT_DIM))
        self.query_one("#tape", Static).update(t)
