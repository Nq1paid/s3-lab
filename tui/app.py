"""S3 LAB -- the terminal application."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.app import App

from tui.state import AppState
from tui.theme import CSS, S3_THEME


class S3Lab(App):
    CSS = CSS
    TITLE = "S3 LAB"
    BINDINGS = [("q", "quit", "Quit"), ("m", "settings", "Settings")]

    def __init__(self, **kw):
        self.state = AppState.load()
        self.run_id = ""
        self.run_state = "IDLE"
        self.last_result = None
        super().__init__(**kw)

    def on_mount(self) -> None:
        self.register_theme(S3_THEME)
        self.theme = "s3"
        self.show("test")

    def show(self, name: str, **kw) -> None:
        """Swap to a section. Screens are rebuilt so they always reflect state."""
        from tui.screens.data import DataScreen
        from tui.screens.history import HistoryScreen
        from tui.screens.results import ResultsScreen, load_run
        from tui.screens.run import RunScreen
        from tui.screens.settings import SettingsScreen
        from tui.screens.strategies import StrategiesScreen
        from tui.screens.test import TestScreen

        if name == "results":
            try:
                screen = ResultsScreen(load_run())
            except Exception as exc:
                self.notify(f"No results yet: {exc}", title="Results",
                            severity="warning")
                return
        elif name == "run":
            screen = RunScreen(**kw)
        elif name == "test":
            screen = TestScreen()
        elif name == "data":
            screen = DataScreen()
        elif name == "strategies":
            screen = StrategiesScreen()
        elif name == "settings":
            screen = SettingsScreen()
        elif name == "history":
            screen = HistoryScreen()
        else:
            self.notify(f"Unknown section {name!r}", title="Navigation",
                        severity="warning")
            return

        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(screen)


    def action_settings(self) -> None:
        self.show("settings")


def main() -> None:
    S3Lab().run()


if __name__ == "__main__":
    main()
