"""The bloom -- the single glowing element on a screen.

Textual has no text-shadow, so the halo is built from cell backgrounds:

      ░░░░░░░░░░░░       #0B0B0C   row above, value width + 2 either side
      ▒▒ 8.21% ▒▒        #0D0D0E   two cells either side, on the value's row
                         #121215   behind the value's own characters
      ░░░░░░░░░░░░       #0B0B0C   row below

The result is a TIGHT halo roughly one character wide. A wide soft wash reads
as a CRT, which is the exact look this style exists to avoid, so the falloff
stops at one row and two cells and never bleeds further.

This is one widget so the effect can never be applied inconsistently or to two
elements at once. `BloomValue.claim()` enforces the one-per-screen rule at
construction time rather than leaving it to review.
"""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from ..theme import BLOOM_CORE, BLOOM_FAR, BLOOM_NEAR, GROUND, HERO

PAD = 2  # cells of near-falloff either side


class TooManyBlooms(RuntimeError):
    """Raised when a second bloom is claimed on one screen."""


class BloomValue(Widget):
    """A hero value with a tight white halo. One per screen."""

    DEFAULT_CSS = "BloomValue { height: 3; width: 1fr; }"

    def __init__(self, value: str, *, offset_x: int = 0, **kw):
        self.value = value
        self.offset_x = offset_x
        super().__init__(**kw)

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        text = self.value
        x0 = self.offset_x
        span = len(text)

        far = Style(bgcolor=BLOOM_FAR, color=HERO)
        near = Style(bgcolor=BLOOM_NEAR, color=HERO)
        core = Style(bgcolor=BLOOM_CORE, color=HERO, bold=True)
        base = Style(bgcolor=GROUND, color=HERO)

        if y == 1:
            segs = [
                Segment(" " * x0, base),
                Segment(" " * PAD, near),
                Segment(text, core),
                Segment(" " * PAD, near),
                Segment(" " * max(0, width - x0 - span - 2 * PAD), base),
            ]
        elif y in (0, 2):
            # The falloff row spans the value plus PAD on each side, and stops.
            segs = [
                Segment(" " * x0, base),
                Segment(" " * (span + 2 * PAD), far),
                Segment(" " * max(0, width - x0 - span - 2 * PAD), base),
            ]
        else:
            segs = [Segment(" " * width, base)]
        return Strip(segs, width)

    def get_content_height(self, *_args) -> int:
        return 3

    # ---- one-per-screen enforcement

    @staticmethod
    def claim(screen) -> None:
        """Call once per screen before composing a BloomValue."""
        if getattr(screen, "_bloom_claimed", False):
            raise TooManyBlooms(
                f"{type(screen).__name__} already has a bloomed element; "
                "only one element per screen may glow"
            )
        screen._bloom_claimed = True
