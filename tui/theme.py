"""House style: modern, monochrome, high-contrast instrument panel.

Rule zero: terminals have one font. Hierarchy comes from four devices only --
CASE, boldness, brightness, and empty space. Nothing simulates size except the
mark.

Every colour used anywhere in this application is defined here. `PALETTE` is
the closed set, and `tests/test_palette.py` greps the source tree to prove no
literal outside it ever appears. Warm hues are a bug, not a preference.
"""

from __future__ import annotations

from textual.theme import Theme

# ---------------------------------------------------------------- palette
GROUND = "#000000"        # every background on screen except the bloom
PANEL = "#0A0A0B"         # panel fill
ROW_ACTIVE = "#101012"    # active table row
HAIRLINE = "#1C1C1F"      # hairlines, borders, dividers
HAIRLINE_LIT = "#2E2E33"  # focused border, chart marker rules
HERO = "#FFFFFF"          # the single bloomed hero value, selected-control bg
TEXT = "#F2F2F3"          # primary values, titles, chart lines
TEXT_DIM = "#8E8E93"      # secondary text, descriptions
LABEL = "#5A5A5F"         # uppercase micro-labels, chart outline halo
CAPTION = "#3A3A3E"       # captions, footnotes, inactive nav

# Bloom falloff only. Never used as a general background.
BLOOM_FAR = "#0B0B0C"     # row above / below the hero value
BLOOM_NEAR = "#0D0D0E"    # two cells either side, on the hero's own row
BLOOM_CORE = "#121215"    # behind the hero value's own characters

#: Base of the vertical bar gradient. Mandated by the chart rules; it is the
#: one value not in the section's colour list, and it exists so a bar fades to
#: near-black without ever touching pure ground.
BAR_BASE = "#141416"

PALETTE = frozenset({
    GROUND, PANEL, ROW_ACTIVE, HAIRLINE, HAIRLINE_LIT, HERO, TEXT, TEXT_DIM,
    LABEL, CAPTION, BLOOM_FAR, BLOOM_NEAR, BLOOM_CORE, BAR_BASE,
})

#: Every slot Textual exposes is pinned to a grey. Inheriting a stock theme and
#: tweaking it leaves amber in the warning/error slots, which is where the old
#: build's warm cast came from.
S3_THEME = Theme(
    name="s3",
    dark=True,
    background=GROUND,
    surface=PANEL,
    panel=ROW_ACTIVE,
    primary=TEXT,
    secondary=TEXT_DIM,
    accent=TEXT,
    foreground=TEXT,
    success=TEXT,
    warning=TEXT,
    error=TEXT,
    boost=ROW_ACTIVE,
    variables={
        "block-cursor-foreground": GROUND,
        "block-cursor-background": HERO,
        "block-cursor-text-style": "none",
        "block-hover-background": PANEL,
        "border": HAIRLINE,
        "border-blurred": HAIRLINE,
        "surface-active": ROW_ACTIVE,
        "scrollbar": HAIRLINE,
        "scrollbar-hover": HAIRLINE_LIT,
        "scrollbar-active": HAIRLINE_LIT,
        "scrollbar-background": GROUND,
        "scrollbar-background-hover": GROUND,
        "scrollbar-background-active": GROUND,
        "footer-key-foreground": TEXT,
        "footer-description-foreground": LABEL,
        "footer-background": GROUND,
        "input-selection-background": HERO,
        "input-cursor-background": HERO,
        "input-cursor-foreground": GROUND,
        "link-color": TEXT,
        "link-color-hover": HERO,
        "link-background": GROUND,
        "link-background-hover": GROUND,
        "text-primary": TEXT,
        "text-secondary": TEXT_DIM,
        "text-accent": TEXT,
        "text-warning": TEXT,
        "text-error": TEXT,
        "text-success": TEXT,
        "text-muted": LABEL,
        "text-disabled": CAPTION,
    },
)


def spaced(text: str) -> str:
    """Letter-space a micro-label: 'MAX DD' -> 'M A X   D D'.

    A literal space between every character, two between words. This is the
    most recognisable feature of the style, so it lives in one helper --
    hand-typed spacing drifts, and drift is what makes a house style look
    accidental.
    """
    return "   ".join(" ".join(word.upper()) for word in text.split())


def signed(value: float, decimals: int = 0) -> str:
    """Sign carried by a triangle. Never by colour."""
    mark = "▲" if value > 0 else ("▼" if value < 0 else " ")
    return f"{mark} {abs(value):,.{decimals}f}"


#: No-data cells show this plus a `not run` caption -- never a zero, which is
#: indistinguishable from a real result of zero.
NO_DATA = "—"

CSS = f"""
Screen {{
    background: {GROUND};
    color: {TEXT};
}}

/* ---- chrome -------------------------------------------------------- */
#topbar {{
    height: 5;
    background: {GROUND};
    border-bottom: solid {HAIRLINE};
    padding: 0 2;
}}
#bottombar {{
    dock: bottom;
    height: 2;
    background: {GROUND};
    border-top: solid {HAIRLINE};
    padding: 0 2;
}}

/* ---- stat strip ---------------------------------------------------- */
#statstrip {{
    height: 6;
    background: {GROUND};
    border-bottom: solid {HAIRLINE};
}}
.statcell {{
    width: 1fr;
    height: 100%;
    padding: 0 2;
    border-left: solid {HAIRLINE};
}}
.statcell.first {{ border-left: none; }}

/* ---- the sheet ------------------------------------------------------ */
/* One bordered sheet inset from the screen edge, as in the reference. */
#sheet {{
    height: 1fr;
    margin: 1 2;
    border: solid {HAIRLINE};
    padding: 1 3;
}}
#crumbrow   {{ height: 3; }}
#crumb      {{ width: 1fr; height: 1; color: {LABEL}; }}
#heroblock  {{ width: 18; height: 4; }}
#herolabel  {{ height: 1; color: {LABEL}; text-align: right; }}
#sheettitle {{ height: 1; }}
#sheetdesc  {{ height: 1; color: {TEXT_DIM}; margin-bottom: 1; }}
#controls   {{ height: 1; margin-bottom: 1; }}
.panelrule  {{ height: 1; color: {HAIRLINE}; }}
#footnote   {{ height: 1; color: {TEXT_DIM}; }}
#legend     {{ height: 1; margin-top: 1; }}
#tablewrap  {{ height: 1fr; }}
.tablehead  {{ height: 1; color: {LABEL}; }}
.tablerow   {{ height: 1; }}

/* ---- right rail ----------------------------------------------------- */
#railwrap {{ height: auto; }}
#chartcol {{ width: 1fr; height: auto; }}
#rightrail {{
    width: 32;
    height: auto;
    border-left: solid {HAIRLINE};
    padding: 0 2;
}}
.raillabel   {{ height: 1; color: {LABEL}; }}
.railheading {{ height: 1; color: {TEXT}; text-style: bold; }}
.railmeta    {{ height: 1; color: {TEXT_DIM}; margin-bottom: 1; }}
.railrow     {{ height: 2; border-bottom: solid {HAIRLINE}; }}
.railnote    {{ height: auto; color: {CAPTION}; margin-top: 1; }}

/* ---- tables --------------------------------------------------------- */
DataTable {{
    background: {GROUND};
    color: {TEXT};
}}
DataTable > .datatable--header {{
    background: {GROUND};
    color: {LABEL};
    text-style: none;
}}
DataTable > .datatable--cursor {{
    background: {ROW_ACTIVE};
    color: {TEXT};
    text-style: none;
}}
DataTable > .datatable--hover {{ background: {PANEL}; }}
DataTable > .datatable--odd-row,
DataTable > .datatable--even-row {{ background: {GROUND}; }}

/* ---- fold table ------------------------------------------------------ */
.tablehead {{ height: 1; color: {LABEL}; }}
.tablerow  {{ height: 1; }}
.tablerow.live {{ background: {ROW_ACTIVE}; }}

Tabs, Tab {{ background: {GROUND}; }}
"""
