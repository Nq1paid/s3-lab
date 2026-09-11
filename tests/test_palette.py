"""The palette is a closed set, and this proves it.

Style rules that live only in a document drift. These greps are the enforcement:
every hex literal in the TUI must be on the permitted list, no warm hue may
appear anywhere, and the Digits widget must not exist in the codebase.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tui.theme import PALETTE  # noqa: E402

TUI = ROOT / "tui"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")

#: Colour words that must never appear as a Rich/Textual style value.
WARM = ("amber", "gold", "tan", "orange", "yellow", "brown", "red", "coral",
        "salmon", "peru", "sienna", "khaki", "wheat", "bisque")


def tui_sources() -> list[Path]:
    return [p for p in TUI.rglob("*.py") if "__pycache__" not in p.parts]


def test_every_hex_literal_is_on_the_permitted_list():
    offenders: list[str] = []
    for path in tui_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in HEX.findall(line):
                if match.upper() not in {c.upper() for c in PALETTE}:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}  {match}")
    assert not offenders, "hex literals outside the palette:\n  " + "\n  ".join(offenders)


def test_no_warm_colour_names_anywhere():
    offenders: list[str] = []
    for path in tui_sources():
        text = path.read_text(encoding="utf-8").lower()
        for word in WARM:
            # Only flag it where it could be a style value, not in prose.
            for m in re.finditer(rf'["\']{word}["\']', text):
                offenders.append(f"{path.relative_to(ROOT)}  {m.group(0)}")
    assert not offenders, "warm colour names used as style values:\n  " + "\n  ".join(offenders)


def test_digits_widget_is_not_used_anywhere():
    """LCD numerals are banned outright."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in tui_sources()
        if re.search(r"\bDigits\b", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Digits widget found in: {offenders}"


def test_no_rounded_borders():
    offenders: list[str] = []
    for path in tui_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"border[^:\n]*:\s*round", line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, f"rounded borders found: {offenders}"


def test_palette_has_no_warm_hue():
    """Red channel must not dominate: every entry is a true grey or near-grey."""
    for colour in PALETTE:
        r, g, bl = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, bl) - min(r, g, bl) <= 6, f"{colour} is not neutral"


def test_only_one_bloom_per_screen_is_allowed():
    from tui.widgets.bloom import BloomValue, TooManyBlooms

    class FakeScreen:
        pass

    screen = FakeScreen()
    BloomValue.claim(screen)
    with pytest.raises(TooManyBlooms):
        BloomValue.claim(screen)


# --- the HTML report shares the palette; it is not exempt --------------------

REPORT = ROOT / "engine" / "report"
#: rgba() is permitted for the hero bloom only: Textual has no text-shadow so
#: the TUI fakes it with cell backgrounds, but a browser has the real thing and
#: a shadow needs alpha. It is still pure white, so no hue is introduced.
ALPHA_WHITE = re.compile(r"rgba\(255,\s*255,\s*255,\s*[0-9.]+\)")


def report_sources() -> list[Path]:
    return [p for p in REPORT.rglob("*.py") if "__pycache__" not in p.parts]


def test_report_hex_literals_are_on_the_permitted_list():
    offenders: list[str] = []
    allowed = {c.upper() for c in PALETTE}
    for path in report_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in HEX.findall(line):
                if match.upper() not in allowed:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}  {match}")
    assert not offenders, "hex literals outside the palette:\n  " + "\n  ".join(offenders)


def test_report_uses_no_warm_colour_names():
    for path in report_sources():
        text = path.read_text(encoding="utf-8").lower()
        for word in WARM:
            assert f'"{word}"' not in text and f"'{word}'" not in text


def test_report_alpha_is_white_only():
    """Any rgba() in the report must be pure white -- bloom, not a tint."""
    for path in report_sources():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"rgba\([^)]*\)", text):
            assert ALPHA_WHITE.fullmatch(m.group(0)), f"non-white alpha: {m.group(0)}"


def test_report_renders_and_is_self_contained():
    """No external stylesheet, font or script: the file must open anywhere."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from tools.report import build
    from engine.report.html import render

    out = render(build(), ROOT / "runs" / "_test_report.html")
    doc = out.read_text(encoding="utf-8")
    out.unlink()
    for banned in ("http://", "https://", "<script", "@import"):
        assert banned not in doc, f"report is not self-contained: {banned}"
    assert "Stitched equity" in doc and "buy &amp; hold" in doc
