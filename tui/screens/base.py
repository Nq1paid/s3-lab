"""Shared screen chrome and navigation.

Every screen carries the same top bar and bottom bar, and every section is
reachable by a single keypress from anywhere. Nothing in this application
requires memorising a command: the bottom bar shows the sections, the keys are
their initials, and arrow keys plus Enter work on every control.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from ..theme import CAPTION, HAIRLINE, LABEL, TEXT, TEXT_DIM, spaced
from ..widgets.chrome import BottomBar, TopBar

#: section -> (key, screen name). The bottom bar and the bindings are generated
#: from this one table so they cannot drift apart.
SECTIONS = (
    ("DATA", "d", "data"),
    ("STRATEGIES", "s", "strategies"),
    ("TEST", "t", "test"),
    ("RUN", "r", "run"),
    ("RESULTS", "e", "results"),
    ("MORE", "m", "settings"),
)


class LabScreen(Screen):
    """Base for every screen: chrome, navigation, and access to app state."""

    SECTION = "RESULTS"
    HINT = ""

    #: The name this screen is opened under by App.show. SECTION is what the
    #: bottom bar highlights and two screens can share one -- History and
    #: Settings are both MORE -- so it is not an identity and must not be used
    #: as one.
    SCREEN_NAME = ""

    #: Nothing is focused when a screen opens. Textual's default is to focus
    #: the first focusable widget, and a focused Input correctly swallows
    #: every letter -- which turns D S T R E M into typed text and makes that
    #: screen a dead end. Focus here is always something the user asked for.
    #:
    #: This is "" and not None on purpose: None means "inherit the app's
    #: setting", which is the "*" that causes the problem.
    AUTO_FOCUS = ""

    BINDINGS = [
        ("d", "go('data')", "Data"),
        ("s", "go('strategies')", "Strategies"),
        ("t", "go('test')", "Test"),
        ("r", "go('run')", "Run"),
        ("e", "go('results')", "Results"),
        ("m", "go('settings')", "More"),
        ("h", "go('history')", "History"),
        ("q", "quit", "Quit"),
        ("pageup", "page(-1)", "Scroll up"),
        ("pagedown", "page(1)", "Scroll down"),
        ("home", "to_end(-1)", "Top"),
        ("end", "to_end(1)", "Bottom"),
    ]

    def _scroller(self):
        """The screen's scrolling region, if it has one."""
        found = self.query(VerticalScroll)
        return found.first(VerticalScroll) if found else None

    def action_page(self, direction: int) -> None:
        """Page through long content without focusing anything.

        Nothing is focused on arrival (see AUTO_FOCUS), which is what keeps
        letters working as commands -- but it also means the scroll region
        never receives arrow keys. Without this, a run history longer than the
        window would have content you could only reach with a mouse.
        """
        box = self._scroller()
        if box is None:
            return
        if direction > 0:
            box.scroll_page_down()
        else:
            box.scroll_page_up()

    def action_to_end(self, direction: int) -> None:
        box = self._scroller()
        if box is None:
            return
        if direction > 0:
            box.scroll_end()
        else:
            box.scroll_home()

    @property
    def state(self):
        return self.app.state

    def set_text(self, selector: str, content) -> bool:
        """Update a child widget, or do nothing if it is not mounted yet.

        Children inside a VerticalScroll are not present when the Screen's
        on_mount fires, and a screen that RAISES during mount stops responding
        to navigation entirely -- every later keypress then looks broken. A
        missing widget is a timing fact, not an error, so it is tolerated and
        the caller re-renders once the screen is shown.
        """
        found = self.query(selector)
        if not found:
            return False
        found.first(Static).update(content)
        return True

    def on_show(self) -> None:
        """Re-render once the screen is actually visible and fully mounted."""
        refresh = getattr(self, "render_all", None)
        if refresh is not None:
            refresh()

    def action_go(self, name: str) -> None:
        if self.SCREEN_NAME == name:
            return          # already here; rebuilding would lose screen state
        self.app.show(name)

    # ---- chrome helpers used by every screen

    def compose(self) -> ComposeResult:
        """Every screen is chrome, body, chrome.

        Defined here rather than in each screen: a screen that forgets it
        composes nothing at all, renders an empty frame, and gives no error --
        which is exactly what happened to Data, Strategies and Settings.
        """
        yield self.top()
        yield from self.body()
        yield self.bottom()

    def body(self) -> ComposeResult:
        return iter(())

    def top(self) -> TopBar:
        bar = TopBar(id="topbar")
        return bar

    def bottom(self) -> BottomBar:
        return BottomBar(self.SECTION, hint=self.HINT or self.default_hint())

    def default_hint(self) -> str:
        return "D S T R E M  sections    H  history    Q  quit"

    def on_mount(self) -> None:
        try:
            bar = self.query_one(TopBar)
        except Exception:
            return
        bar.strategy = self.state.test.strategy_name
        bar.active_instrument = self.state.test.instrument
        bar.run_id = getattr(self.app, "run_id", "") or "—"
        bar.state = getattr(self.app, "run_state", "IDLE")


def field_row(label: str, value: str, hint: str = "", width: int = 26,
              selected: bool = False) -> Text:
    """One settings row: spaced micro-label, value, dim hint."""
    t = Text()
    t.append("▎ " if selected else "  ", Style(color=TEXT if selected else HAIRLINE))
    t.append(spaced(label).ljust(width), Style(color=LABEL))
    t.append(value.ljust(22), Style(color=TEXT, bold=selected))
    if hint:
        t.append(hint, Style(color=CAPTION))
    return t


def rule(width: int = 400) -> Static:
    return Static(Text("─" * width, Style(color=HAIRLINE)), classes="panelrule")


def eyebrow(text: str) -> Static:
    return Static(Text(spaced(text), Style(color=LABEL)), classes="breadcrumb")


def title(text: str) -> Static:
    return Static(Text(text, Style(color=TEXT, bold=True)), classes="paneltitle")


def desc(text: str) -> Static:
    return Static(Text(text, Style(color=TEXT_DIM)), classes="paneldesc")
