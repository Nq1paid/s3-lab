"""Data screen -- scan the folder, confirm what was inferred, import.

Nothing is guessed silently. The scan shows the column mapping it sniffed and
the timezone it inferred, with the evidence, before anything is imported.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from ..theme import CAPTION, GROUND, HAIRLINE, HERO, LABEL, TEXT, TEXT_DIM, spaced
from .base import LabScreen, desc, eyebrow, rule, title


class DataScreen(LabScreen):
    SECTION = "DATA"
    SCREEN_NAME = "data"
    HINT = "↑↓ file    I  import    F5  rescan"

    BINDINGS = LabScreen.BINDINGS + [
        ("up", "move(-1)", "Previous"),
        ("down", "move(1)", "Next"),
        ("i", "import_file", "Import"),
        ("f5", "rescan", "Rescan"),
    ]

    row: reactive[int] = reactive(0)
    busy: reactive[bool] = reactive(False)

    def __init__(self, **kw):
        self.raw: list[Path] = []
        self.cached: list[dict] = []
        self._log: list[str] = []
        super().__init__(**kw)

    def body(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield eyebrow("DATA  /  SOURCES")
            yield title("Raw files and cached series")
            yield desc("Files are read where they sit. Nothing is copied, and a "
                       "confirmed column mapping is never asked for twice.")
            yield rule()
            with VerticalScroll():
                yield Static(id="rawlist")
                yield Static(id="cachedlist")
                yield Static(id="datalog")

    def on_mount(self) -> None:
        super().on_mount()
        # Deferred: children inside the VerticalScroll are not mounted yet.
        self.call_after_refresh(self.action_rescan)

    # ---- scanning

    def action_rescan(self) -> None:
        raw_dir = Path(self.state.data_dir)
        self.raw = sorted(p for p in raw_dir.glob("*")
                          if p.suffix.lower() in (".csv", ".txt", ".parquet"))
        self.cached = []
        for meta in sorted(Path(self.state.cache_dir).glob("*.meta.json")):
            try:
                self.cached.append(json.loads(meta.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        self.row = min(self.row, max(0, len(self.raw) - 1))
        self.render_all()

    def action_move(self, delta: int) -> None:
        if self.raw:
            self.row = (self.row + delta) % len(self.raw)

    def watch_row(self, _r: int) -> None:
        if self.is_mounted:
            self.render_all()

    def append_log(self, line: str) -> None:
        """Named append_log, not log: Textual's Widget already exposes `log` as
        its logger, and shadowing it makes the framework's own
        `self.log.debug(...)` raise the moment it focuses a widget."""
        self._log.append(line)
        if self.is_mounted:
            self.render_log()

    # ---- import

    def action_import_file(self) -> None:
        if self.busy or not self.raw:
            return
        path = self.raw[self.row]
        if any(c["source"] == str(path) for c in self.cached):
            self.notify(f"{path.name} is already imported — press R to rescan",
                        title="Data")
            return
        self.busy = True
        self._log = [f"importing {path.name}"]
        self.render_log()
        self.run_worker(lambda: self._import(path), thread=True, exclusive=True)

    def _import(self, path: Path) -> None:
        from engine.data.loader import ingest
        from engine.data.sniff import MappingStore

        try:
            store = MappingStore(Path(self.state.cache_dir) / "mappings.json")
            result = ingest(path, self.state.cache_dir, mapping_store=store)
        except Exception as exc:
            self.app.call_from_thread(self._imported, None, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._imported, result, "")

    def _imported(self, result, error: str) -> None:
        self.busy = False
        if error:
            # A refusal must say what it could not read, not fail quietly.
            self.append_log(f"FAILED: {error}")
            self.notify(error, title="Import failed", severity="error")
        else:
            for line in result.render().splitlines():
                self.append_log(line)
            self.notify(f"Imported {Path(result.source).name} as "
                        f"{Path(result.parquet).name}", title="Data")
        self.action_rescan()

    # ---- rendering

    def render_all(self) -> None:
        self.render_raw()
        self.render_cached()
        self.render_log()

    def render_raw(self) -> None:
        t = Text()
        t.append("  " + spaced("RAW  /  " + self.state.data_dir) + "\n\n", Style(color=LABEL))
        if not self.raw:
            t.append(f"  No files in {self.state.data_dir}. Save your CSV exports "
                     "there and press R.\n", Style(color=TEXT_DIM))
        for i, p in enumerate(self.raw):
            imported = any(c["source"] == str(p) for c in self.cached)
            live = i == self.row
            t.append("▎ " if live else "  ", Style(color=TEXT if live else GROUND))
            t.append(p.name.ljust(34), Style(color=TEXT if live else TEXT_DIM,
                                             bold=live))
            t.append(f"{p.stat().st_size / 1e6:>8,.1f} MB   ", Style(color=TEXT_DIM))
            t.append("imported" if imported else "not imported",
                     Style(color=TEXT_DIM if imported else CAPTION))
            t.append("\n")
        if self.raw and not self.busy:
            t.append("\n  ")
            t.append("  I  ", Style(bgcolor=HERO, color=GROUND, bold=True))
            t.append("  import the selected file", Style(color=TEXT_DIM))
        if self.busy:
            t.append("\n  importing — this takes about 15 seconds for a 3M-bar file",
                     Style(color=TEXT_DIM))
        t.append("\n")
        self.set_text("#rawlist", t)

    def render_cached(self) -> None:
        t = Text()
        t.append("\n  " + spaced("CACHED SERIES") + "\n\n", Style(color=LABEL))
        if not self.cached:
            t.append("  Nothing imported yet.\n", Style(color=TEXT_DIM))
        for c in self.cached:
            q = c.get("quality", {})
            t.append("  " + Path(c["parquet"]).stem.ljust(20), Style(color=TEXT))
            t.append(f"{q.get('n_bars', 0):>12,} bars   ", Style(color=TEXT_DIM))
            t.append(f"{q.get('years', 0):>5.2f} yr   ", Style(color=TEXT_DIM))
            t.append(f"{c.get('tz', '?'):<18}", Style(color=TEXT_DIM))
            t.append("confirmed by calendar" if c.get("tz_conclusive")
                     else "NOT CONCLUSIVE", Style(color=CAPTION))
            t.append("\n")
            warns = q.get("warnings", [])
            for w in warns[:2]:
                t.append(f"      ! {w}\n", Style(color=CAPTION))
        self.set_text("#cachedlist", t)

    def render_log(self) -> None:
        t = Text()
        if self._log:
            t.append("\n  " + spaced("LAST IMPORT") + "\n\n", Style(color=LABEL))
            for line in self._log[-24:]:
                t.append("  " + line + "\n", Style(color=TEXT_DIM))
        self.set_text("#datalog", t)
