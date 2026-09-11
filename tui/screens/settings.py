"""Settings -- slippage, account size, contract specs, defaults.

There is no commission field, by the user's decision: this engine does not
model per-contract costs at all. Slippage is the only cost it applies, and the
panel below the fields says so plainly rather than leaving a reader to assume
a backtest carries costs it never charged.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..theme import CAPTION, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, field_row, rule, title


class SettingsScreen(LabScreen):
    SECTION = "MORE"
    SCREEN_NAME = "settings"
    HINT = "up/down field    left/right change"

    BINDINGS = LabScreen.BINDINGS + [
        ("up", "move(-1)", "Previous"),
        ("down", "move(1)", "Next"),
        ("left", "adjust(-1)", "Decrease"),
        ("right", "adjust(1)", "Increase"),
    ]

    row: reactive[int] = reactive(0)

    ROWS = (
        ("slippage", "SLIPPAGE", "ticks per side, always against you"),
        ("equity", "ACCOUNT SIZE", "used for MAR, drawdown % and risk of ruin"),
        ("pnl_colour", "P&L COLOUR", "classic green/red instead of monochrome"),
    )

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("MORE  /  SETTINGS")
            yield title("Execution")
            yield desc("Applied to every run from here on. Existing results keep "
                       "the settings they were computed with.")
            yield rule()
            yield Static(id="fields")
            yield rule()
            yield Static(id="costnote")

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_rows()

    def value_of(self, key: str) -> str:
        s = self.state
        if key == "slippage":
            return f"{s.slippage_ticks:g} ticks"
        if key == "equity":
            return f"{s.starting_equity:,.0f}"
        return "on" if s.classic_pnl_colours else "off"

    def action_move(self, delta: int) -> None:
        self.row = (self.row + delta) % len(self.ROWS)

    def action_adjust(self, delta: int) -> None:
        s = self.state
        key = self.ROWS[self.row][0]
        if key == "slippage":
            s.slippage_ticks = max(0.0, round(s.slippage_ticks + delta * 0.5, 1))
        elif key == "equity":
            s.starting_equity = max(1000.0, s.starting_equity + delta * 5000.0)
        else:
            s.classic_pnl_colours = not s.classic_pnl_colours
        s.save()
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

        s = self.state
        t = Text()
        t.append("  " + spaced("COSTS APPLIED") + "   ", Style(color=LABEL))
        t.append(f"{s.slippage_ticks:g} tick per side, against you\n\n",
                 Style(color=TEXT, bold=True))
        t.append("  No commission, no exchange fees. This engine does not model "
                 "per-contract\n  costs at all, so every P&L figure is before "
                 "brokerage.\n\n", Style(color=TEXT_DIM))
        t.append("  A round turn at a typical retail rate is a few dollars per "
                 "contract. Subtract\n  it yourself: the Results screen reports "
                 "the round-trip count next to the net,\n  and the break-even "
                 "cost per side that would wipe the result out.",
                 Style(color=CAPTION))
        self.set_text("#costnote", t)
