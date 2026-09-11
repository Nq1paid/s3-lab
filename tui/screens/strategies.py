"""Strategies screen -- a drop zone, then the registered list.

Dragging a folder onto a terminal pastes its quoted path, and the screen
handles that paste itself -- nothing has to be focused first. The moment a path
arrives the intake pipeline runs end to end with no further prompting. ENTER
opens a field for a typed path, so the flow still works where drag does not.

Nothing is focused by default, and that is deliberate. A focused Input
correctly swallows every letter, which turned D S T R E M into typed text and
made this the one screen you could not leave by pressing a section key.

The eight steps fill in as a live checklist. On failure the strategy stays in
the list in a FAILED state with the step, the command, the exit code and the
stderr tail attached -- a silently rejected folder is unacceptable.
"""

from __future__ import annotations

from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from ..theme import CAPTION, GROUND, HAIRLINE, HERO, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, rule, title

DROP_W = 74


class StrategiesScreen(LabScreen):
    SECTION = "STRATEGIES"
    SCREEN_NAME = "strategies"
    HINT = ("drag a folder here    ENTER  type a path    ESC  leave the field"
            "    F5  re-validate")

    # Not 'r': D S T R E M H Q belong to navigation on every screen, and a
    # screen that borrows one is a screen you cannot leave. F5 is "re-read the
    # source" here and on Data, so it means one thing in the whole app.
    BINDINGS = LabScreen.BINDINGS + [
        ("f5", "revalidate", "Re-validate"),
        ("enter", "type_path", "Type a path"),
        ("escape", "leave_field", "Leave the drop field"),
    ]

    def __init__(self, **kw):
        self._steps: list = []
        self._busy = False
        self._last = None
        super().__init__(**kw)

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("STRATEGIES  /  REGISTER")
            yield title("Drop a strategy")
            yield desc("The folder stays where it is. Nothing is copied — the app "
                       "stores the path.")
            yield Static(self._dropzone(), id="dropzone")
            yield Input(placeholder="", id="droppath")
            yield rule()
            with VerticalScroll():
                yield Static(id="checklist")
                yield Static(id="registered")

    def on_mount(self) -> None:
        super().on_mount()
        self.call_after_refresh(self.render_all)

    def on_paste(self, event) -> None:
        """A dragged folder arrives as a paste, focus or no focus.

        Textual routes a paste to the focused widget and it bubbles to the
        screen, so handling it here covers both cases: drag with nothing
        focused, and drag while the typed-path field is open. Either way the
        path goes straight into intake and the field is not a prerequisite.
        """
        raw = (event.text or "").strip()
        if not raw or self._busy:
            return
        event.stop()
        found = self.query("#droppath")
        if found:
            found.first(Input).value = ""
        self.start_intake(raw)

    def action_type_path(self) -> None:
        """Focus the field, for a path that is typed rather than dragged."""
        found = self.query("#droppath")
        if found:
            found.first(Input).focus()

    def action_leave_field(self) -> None:
        """Blur the field so single-key navigation works again."""
        found = self.query("#droppath")
        if found:
            found.first(Input).blur()
        self.set_focus(None)

    def _dropzone(self) -> Text:
        t = Text()
        t.append("  " + "╌" * DROP_W + "\n", Style(color=HAIRLINE))
        label = spaced("DROP STRATEGY FOLDER HERE")
        t.append("  " + label.center(DROP_W) + "\n", Style(color=LABEL))
        t.append("  " + "or press ENTER to type a path".center(DROP_W) + "\n",
                 Style(color=CAPTION))
        t.append("  " + "╌" * DROP_W, Style(color=HAIRLINE))
        return t

    # ---- intake

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw or self._busy:
            return
        event.input.value = ""
        self.start_intake(raw)

    def start_intake(self, raw: str) -> None:
        self._busy = True
        self._steps = []
        self.render_checklist(header=raw)
        self.run_worker(lambda: self._intake(raw), thread=True, exclusive=True)

    def _intake(self, raw: str) -> None:
        from engine.adapters.intake import Registry, intake

        def on_step(step):
            self.app.call_from_thread(self._step, step)

        try:
            reg = intake(raw, Registry(), on_step=on_step)
        except Exception as exc:                 # never swallow, never guess
            self.app.call_from_thread(self._done, None, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._done, reg, "")

    def _step(self, step) -> None:
        self._steps.append(step)
        self.render_checklist()

    def _done(self, reg, error: str) -> None:
        self._busy = False
        self._last = reg
        if error:
            self.notify(error, title="Intake failed", severity="error")
        elif reg.state == "ok":
            self.notify(f"{reg.name} v{reg.version} registered ({reg.language})",
                        title="Strategies")
        else:
            self.notify(f"Failed at: {reg.failed_step}", title="Strategies",
                        severity="error")
        self.render_checklist()
        self.render_registered()

    def action_revalidate(self) -> None:
        from engine.adapters.intake import Registry

        reg = Registry()
        if not reg.items:
            self.notify("Nothing registered yet", title="Strategies")
            return
        stale = [r for r in reg.items.values() if r.stale]
        target = (stale or list(reg.items.values()))[0]
        self.start_intake(target.source)

    # ---- rendering

    def render_checklist(self, header: str = "") -> None:
        from engine.adapters.intake import STEPS

        t = Text()
        if not self._steps and not header:
            self.set_text("#checklist", t)
            return

        t.append("\n  " + spaced("INTAKE") + "\n\n", Style(color=LABEL))
        done = {s.step: s for s in self._steps}
        for step in STEPS:
            result = done.get(step.value)
            if result is None:
                mark, style = "  ", Style(color=CAPTION)
            elif result.ok:
                mark, style = "OK", Style(color=TEXT)
            else:
                mark, style = "!!", Style(color=TEXT, bold=True)
            t.append(f"  [{mark}] ", style)
            t.append(step.value.ljust(30), style)
            t.append((result.detail if result else "")[:60] + "\n",
                     Style(color=TEXT_DIM))
            if result is not None and not result.ok:
                # Everything needed to see why, without leaving the screen.
                if result.command:
                    t.append(f"        command : {result.command}\n", Style(color=CAPTION))
                if result.returncode is not None:
                    t.append(f"        exit    : {result.returncode}\n", Style(color=CAPTION))
                for line in result.stderr[-8:]:
                    t.append(f"        {line}\n", Style(color=CAPTION))
        self.set_text("#checklist", t)

    def render_all(self) -> None:
        self.render_checklist()
        self.render_registered()

    def render_registered(self) -> None:
        from engine.adapters.intake import Registry

        reg = Registry()
        t = Text()
        t.append("\n  " + spaced("REGISTERED") + "\n\n", Style(color=LABEL))
        if not reg.items:
            t.append("  Nothing registered yet. Drop a folder above.\n",
                     Style(color=TEXT_DIM))
        for r in reg.items.values():
            ok = r.state == "ok"
            t.append("  " + (r.name or "?").ljust(20),
                     Style(color=TEXT if ok else TEXT_DIM, bold=ok))
            t.append(f"v{r.version:<6}", Style(color=TEXT_DIM))
            t.append(f"{r.language:<8}", Style(color=TEXT_DIM))
            if not ok:
                t.append(f"FAILED at {r.failed_step}", Style(color=TEXT))
            elif r.stale:
                t.append("stale — press R to rebuild", Style(color=TEXT))
            else:
                t.append(f"{len(r.params)} params", Style(color=TEXT_DIM))
            t.append("\n")
            t.append(f"      {r.source}\n", Style(color=CAPTION))
        self.set_text("#registered", t)
