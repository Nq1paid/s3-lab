"""The web UI obeys the same house rules as the terminal one.

There are two front ends now, and the palette was only ever enforced on one of
them. A closed colour set that is checked in one place and copied by hand into
another is not closed -- it is a convention, and conventions drift. So the same
greps run over web/public.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tui.theme import PALETTE

WEB = Path(__file__).resolve().parents[1] / "web" / "public"
CSS = WEB / "app.css"
JS = WEB / "app.js"
HTML = WEB / "index.html"

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def files():
    return [p for p in (CSS, JS, HTML) if p.exists()]


def test_the_web_ui_exists():
    assert CSS.exists() and JS.exists() and HTML.exists(), "web UI is missing"


def test_no_colour_outside_the_closed_palette():
    allowed = {c.upper() for c in PALETTE}
    found = {}
    for p in files():
        text = p.read_text(encoding="utf-8")
        # The favicon is a data: URI of an SVG and carries its own two colours;
        # they are the palette's ground and hero, written in the short form the
        # URI needs.
        text = re.sub(r"<link rel=\"icon\".*?>", "", text, flags=re.S)
        for hit in HEX.findall(text):
            if hit.upper() not in allowed:
                found.setdefault(p.name, set()).add(hit)
    assert not found, "off-palette colours: " + str({k: sorted(v) for k, v in found.items()})


def test_every_palette_colour_is_declared_once_as_a_variable():
    """The CSS custom properties are the single definition, as theme.py is."""
    css = CSS.read_text(encoding="utf-8")
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    declared = {h.upper() for h in HEX.findall(root)}
    assert declared == {c.upper() for c in PALETTE}, (
        "the CSS :root block and tui.theme.PALETTE disagree; "
        "missing=" + str({c.upper() for c in PALETTE} - declared)
        + " extra=" + str(declared - {c.upper() for c in PALETTE}))


def test_no_rounded_corners():
    """A house rule that survives the move off the terminal."""
    css = CSS.read_text(encoding="utf-8")
    radii = re.findall(r"border-radius\s*:\s*([^;]+);", css)
    bad = [r for r in radii if "0" not in r]
    assert not bad, "rounded corners: " + str(bad)


#: Every CSS property that can put a colour on screen.
COLOUR_PROPS = ("color", "background", "background-color", "fill", "stroke",
                "border-color", "border-top-color", "border-bottom-color",
                "border-left-color", "border-right-color", "outline-color")

#: Colour keywords that carry meaning we have banned, plus the named hues a
#: stray paste would most likely introduce. `transparent`, `inherit`, `none`
#: and `currentColor` are not colours in this sense and are allowed.
BANNED_KEYWORDS = ("green", "red", "crimson", "lime", "orange", "amber",
                   "gold", "yellow", "blue", "teal", "purple", "pink", "brown")


def test_no_colour_is_used_to_signal_profit_or_loss():
    """Green and red are the whole reason the palette is closed.

    Checked against declaration *values*, not the text of the file: the word
    "red" appears in "rendered" and in the comment explaining that a losing
    fold is never red, and a test that fails on prose is a test people delete.
    """
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    offenders = []
    for prop, value in re.findall(r"([a-z-]+)\s*:\s*([^;{}]+)[;}]", css):
        if prop not in COLOUR_PROPS:
            continue
        low = value.lower()
        if any(re.search(r"\b" + k + r"\b", low) for k in BANNED_KEYWORDS):
            offenders.append(prop + ": " + value.strip())
    assert not offenders, "colour with meaning in the web UI: " + str(offenders)


def test_every_colour_value_is_a_palette_variable_or_a_palette_hex():
    """No literal colour sneaks in beside the variables."""
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    root_end = css.index("}", css.index(":root"))
    body = css[root_end:]                     # skip the :root definitions
    allowed = {c.upper() for c in PALETTE}
    offenders = []
    for prop, value in re.findall(r"([a-z-]+)\s*:\s*([^;{}]+)[;}]", body):
        if prop not in COLOUR_PROPS:
            continue
        for hit in HEX.findall(value):
            if hit.upper() not in allowed:
                offenders.append(prop + ": " + hit)
    assert not offenders, "off-palette literal: " + str(offenders)


@pytest.mark.parametrize("needle", [
    "before brokerage",          # the cost disclosure must survive a redesign
    "127.0.0.1",                 # the engine address the page defaults to
])
def test_key_statements_are_present(needle):
    blob = "\n".join(p.read_text(encoding="utf-8").lower() for p in files())
    assert needle.lower() in blob


def test_the_ui_never_ships_a_hardcoded_result():
    """The mockup's example figures must not survive into the real screen.

    Copying the reference layout is the point. Copying its numbers would make
    the page a lie, and these are the exact figures from the mockup image.
    """
    blob = "\n".join(p.read_text(encoding="utf-8") for p in files())
    for figure in ("+27.4%", "146,892", "110,381", "-12.9%", "2.12", "0.71"):
        assert figure not in blob, "mockup figure hardcoded in the UI: " + figure
