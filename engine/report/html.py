"""Standalone HTML report -- the exact rendering of the house style.

The TUI remains the application: every feature is driven from it and it starts
from a desktop shortcut. This is the export the spec asks for on the Results
screen, and it is where the style can be rendered exactly rather than
approximated -- a terminal has one font size, so the title, the hero value and
the serif mark can only be suggested there. Here they are real.

Self-contained: no external stylesheets, fonts or scripts, so the file can be
opened anywhere or mailed to someone.
"""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np

from .svgchart import bar_chart, diverging_rows, line_chart

# The closed palette, identical to tui/theme.py.
GROUND = "#000000"
PANEL = "#0A0A0B"
ROW_ACTIVE = "#101012"
HAIRLINE = "#1C1C1F"
HAIRLINE_LIT = "#2E2E33"
HERO = "#FFFFFF"
TEXT = "#F2F2F3"
TEXT_DIM = "#8E8E93"
LABEL = "#5A5A5F"
CAPTION = "#3A3A3E"

CSS = f"""
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: {GROUND}; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto,
               "Helvetica Neue", Arial, sans-serif;
  color: {TEXT};
  -webkit-font-smoothing: antialiased;
  font-size: 14px;
}}
.mono {{
  font-family: "Berkeley Mono", "SF Mono", "JetBrains Mono", "Cascadia Mono",
               ui-monospace, Menlo, Consolas, monospace;
}}
/* Micro-labels: uppercase, letter-spaced, dim. The most recognisable
   feature of the style, so it is one class rather than ad-hoc spacing. */
.label {{
  font-family: "Berkeley Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace;
  text-transform: uppercase;
  letter-spacing: 0.22em;
  font-size: 10px;
  color: {LABEL};
}}
.sm {{ font-size: 11px; }}
.dim {{ color: {TEXT_DIM}; }}
.caption {{ color: {CAPTION}; font-size: 12px; }}

/* ---- top bar ---- */
.topbar {{
  display: flex; align-items: center; gap: 34px;
  padding: 14px 26px; border-bottom: 1px solid {HAIRLINE};
}}
.mark {{ display: flex; align-items: flex-start; gap: 6px; }}
.mark .glyph {{
  font-family: "Times New Roman", Georgia, "Iowan Old Style", serif;
  font-size: 34px; line-height: 0.9; color: {TEXT};
}}
.mark .sup {{
  font-family: "Times New Roman", Georgia, serif;
  font-size: 17px; vertical-align: super; color: {TEXT};
}}
.mark .meta {{ display: flex; flex-direction: column; gap: 2px; padding-top: 3px; }}
.mark .meta span {{ font-size: 9px; color: {CAPTION}; letter-spacing: 0.12em; }}
.tabs {{ display: flex; gap: 22px; }}
.tab {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; color: {CAPTION}; padding-bottom: 3px;
  letter-spacing: 0.05em;
}}
.tab.on {{ color: {TEXT}; border-bottom: 1px solid {TEXT}; }}
.topright {{ margin-left: auto; display: flex; align-items: center; gap: 18px; }}
.kv {{ display: flex; align-items: baseline; gap: 8px; }}
.kv .v {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; color: {TEXT};
}}
.dot {{ color: {TEXT_DIM}; font-size: 9px; }}

/* ---- sheet ---- */
.sheet {{ margin: 22px; border: 1px solid {HAIRLINE}; }}
.sheethead {{ padding: 26px 34px 0; }}
.crumbrow {{ display: flex; align-items: flex-start; }}
.hero {{ margin-left: auto; text-align: right; }}
.hero .value {{
  font-size: 26px; font-weight: 600; color: {HERO}; letter-spacing: -0.01em;
  margin-top: 4px;
  /* One bloomed element per page, and the halo stays tight -- a wide wash
     reads as a CRT, which is the look the style exists to avoid. */
  text-shadow: 0 0 10px rgba(255,255,255,0.30), 0 0 26px rgba(255,255,255,0.10);
}}
h1 {{
  font-size: 34px; font-weight: 600; letter-spacing: -0.02em;
  margin: 14px 0 0; color: {TEXT};
}}
h1 .sub {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; font-weight: 400; color: {TEXT_DIM};
  letter-spacing: 0.06em; margin-left: 12px;
}}
.desc {{ color: {TEXT_DIM}; margin: 10px 0 0; font-size: 14px; }}

/* ---- control row ---- */
.controls {{ display: flex; align-items: center; margin: 22px 0 0; padding-bottom: 22px; }}
.seg {{ display: flex; gap: 4px; }}
.seg button {{
  font: inherit; font-size: 14px; border: 0; cursor: default;
  background: transparent; color: {TEXT_DIM}; padding: 9px 20px;
}}
.seg button.on {{ background: {HERO}; color: {GROUND}; font-weight: 600; }}
.ctrlright {{ margin-left: auto; display: flex; align-items: center; gap: 12px; }}
.pill {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; color: {TEXT}; border: 1px solid {HAIRLINE};
  padding: 7px 12px; background: {GROUND};
}}
.apply {{ font-size: 13px; color: {TEXT}; padding: 7px 4px; }}

/* ---- body ---- */
.body {{ display: flex; border-top: 1px solid {HAIRLINE}; }}
.chartwrap {{ flex: 1; padding: 26px 34px 18px; min-width: 0; }}
.chartmeta {{ display: flex; justify-content: space-between; }}
.legend {{ display: flex; gap: 26px; margin-top: 18px; align-items: center; }}
.legend i {{ display: inline-block; width: 22px; height: 1px; margin-right: 9px;
             vertical-align: middle; }}
.rail {{ width: 300px; border-left: 1px solid {HAIRLINE}; padding: 26px 28px; }}
.rail h2 {{ font-size: 26px; font-weight: 600; margin: 8px 0 4px; color: {TEXT}; }}
.railrow {{
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 11px 0; border-bottom: 1px solid {HAIRLINE};
}}
.railrow .k {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; color: {TEXT_DIM};
}}
.railrow .v {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px; color: {TEXT};
}}
.railnote {{ margin-top: 20px; color: {TEXT_DIM}; font-size: 13px; line-height: 1.5; }}

.foot {{ padding: 18px 34px; border-top: 1px solid {HAIRLINE};
         color: {TEXT_DIM}; font-size: 13px; }}

/* ---- table ---- */
table {{ width: 100%; border-collapse: collapse; }}
thead th {{
  text-align: right; padding: 16px 14px; border-bottom: 1px solid {HAIRLINE};
  font-weight: 400;
}}
thead th:first-child, tbody td:first-child {{ text-align: left; padding-left: 34px; }}
thead th:last-child, tbody td:last-child {{ padding-right: 34px; }}
tbody td {{
  font-family: "Berkeley Mono", ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px; text-align: right; padding: 13px 14px;
  border-bottom: 1px solid {HAIRLINE}; color: {TEXT_DIM};
}}
tbody tr:hover td {{ background: {ROW_ACTIVE}; }}
td.fold {{ color: {TEXT}; font-weight: 600; }}
td.strong {{ color: {TEXT}; }}
.spark {{ height: 7px; background: linear-gradient(90deg, {HERO}, #141416); }}
"""


def _esc(s) -> str:
    return html.escape(str(s))


def _label(text: str) -> str:
    return f'<span class="label">{_esc(text)}</span>'


def _robustness_section(methods: list) -> str:
    """The four Monte Carlo verdicts, with the one that matters called out."""
    if not methods:
        return ""
    rows = []
    for m in methods:
        rows.append(
            '<tr>'
            f'<td class="strong" style="text-align:left">{_esc(m["name"])}</td>'
            f'<td>{_esc(m["iterations"])}</td>'
            f'<td>{_esc(m["headline"])}</td>'
            f'<td class="strong" style="text-align:left">{_esc(m["verdict"])}</td>'
            '</tr>'
        )
    notes = "".join(f'<p class="railnote">{_esc(n)}</p>'
                    for m in methods for n in m.get("notes", []))
    return (
        '<div class="sheethead" style="padding-top:26px">'
        + _label("robustness  /  monte carlo")
        + '<h1 style="font-size:26px">Robustness</h1>'
        + '<p class="desc">Every method reports bands, never a single number.</p>'
        + '</div>'
        '<table><thead><tr>'
        + "".join(f'<th style="text-align:left">{_label(c)}</th>'
                  for c in ("method", "iterations", "result", "verdict"))
        + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        + ('<div class="foot">' + notes + '</div>' if notes else "")
    )


def _attribution_section(dims: list) -> str:
    """Where the result lives, and the concentration warnings, verbatim."""
    if not dims:
        return ""
    blocks = []
    for d in dims:
        rows = "".join(
            '<tr>'
            f'<td class="{"strong" if s["significant"] else ""}" '
            f'style="text-align:left">{_esc(s["key"])}</td>'
            f'<td>{s["n_trades"]:,}</td>'
            f'<td>{s["net_pnl"]:,.0f}</td>'
            f'<td>{s["expectancy"]:,.1f}</td>'
            f'<td>{s["win_rate"]:.1f}%</td>'
            f'<td>{s["share"]:.1%}</td>'
            f'<td>{"" if s["significant"] else "not significant"}</td>'
            '</tr>'
            for s in d["slices"]
        )
        warn = (f'<p class="railnote"><strong>{_esc(d["concentration"])}</strong></p>'
                if d.get("concentration") else "")
        blocks.append(
            '<div class="sheethead" style="padding-top:22px">'
            + _label("attribution  /  " + d["dimension"].replace("_", " "))
            + f'<h1 style="font-size:22px">{_esc(d["title"])}</h1>'
            + '</div>'
            '<table><thead><tr>'
            + "".join(f'<th style="text-align:left">{_label(c)}</th>'
                      for c in ("slice", "n", "net p&l", "expectancy",
                                "win", "share", ""))
            + '</tr></thead><tbody>' + rows + '</tbody></table>'
            + ('<div class="foot">' + warn + '</div>' if warn else "")
        )
    return "".join(blocks)


def render(run: dict, out_path: str | Path) -> Path:
    """Write the report. `run` is the same view model the TUI screen uses."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chart_svg, peak = line_chart(
        run["chart_series"], width=1040, height=380,
        baseline=run.get("chart_baseline"), boundaries=run.get("boundaries"),
        log=run.get("chart_log", False),
    )

    tabs = "".join(
        f'<span class="tab{" on" if t == run["instrument"] else ""}">{_esc(t)}</span>'
        for t in ("NQ", "MNQ", "ES", "MES")
    )
    seg = "".join(
        f'<button class="{"on" if v == run["view"] else ""}">{_esc(v)}</button>'
        for v in run["views"]
    )
    rail_rows = "".join(
        f'<div class="railrow"><span class="k">{_esc(k)}</span>'
        f'<span class="v">{_esc(v)}</span></div>'
        for k, v in run["rail"]["rows"]
    )
    head_cells = "".join(f'<th>{_label(c)}</th>' for c in run["table"]["columns"])
    robustness = _robustness_section(run.get("robustness", []))
    attribution = _attribution_section(run.get("attribution", []))

    body_rows = []
    for r in run["table"]["rows"]:
        arrow = "▲" if r["oos_ret"] >= 0 else "▼"
        body_rows.append(
            f'<tr>'
            f'<td class="fold">{r["index"]:02d}</td>'
            f'<td>{_esc(r["oos_from"])}</td>'
            f'<td>{r["or"]} / {r["stop"]}</td>'
            f'<td>{r["is_mar"]:.2f}</td>'
            f'<td class="strong">{arrow} {abs(r["oos_ret"]):.1f}%</td>'
            f'<td class="strong">{r["oos_mar"]:.2f}</td>'
            f'<td>{r["wfe"]:.2f}</td>'
            f'<td>{r["oos_n"]}</td>'
            f'<td style="width:120px"><div class="spark" '
            f'style="width:{r["spark"] * 100:.0f}%"></div></td>'
            f'</tr>'
        )

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_esc(run["title"])} — s³ LAB</title>
<style>{CSS}</style></head>
<body>
<div class="topbar">
  <div class="mark">
    <span class="glyph">s<span class="sup">3</span></span>
    <span class="meta"><span>v0.1</span><span>LAB</span></span>
  </div>
  <div class="tabs">{tabs}</div>
  <div class="topright">
    <div class="kv">{_label("strategy")}<span class="v">{_esc(run["strategy"])}</span></div>
    <div class="kv">{_label("run /")}<span class="v">{_esc(run["run_id"])}</span></div>
    <div class="kv"><span class="dot">●</span>{_label(run["state"])}</div>
    <span class="v mono dim">{_esc(run["clock"])}</span>
  </div>
</div>

<div class="sheet">
  <div class="sheethead">
    <div class="crumbrow">
      <div>{_label("walkforward  /  results")}</div>
      <div class="hero">{_label("net oos")}
        <div class="value">{_esc(run["net_oos"])}</div>
      </div>
    </div>
    <h1>{_esc(run["title"])}<span class="sub">{_esc(run["instrument"])} &nbsp;·&nbsp;
      {_esc(run["barsize"])}</span></h1>
    <p class="desc">{_esc(run["subtitle"])}</p>
    <div class="controls">
      <div class="seg">{seg}</div>
      <div class="ctrlright">
        {_label("fold")}<span class="pill">{_esc(run["fold_filter"])} ▾</span>
        {_label("slice")}<span class="pill">{_esc(run["slice_filter"])}</span>
        <span class="apply">Apply</span>
      </div>
    </div>
  </div>

  <div class="body">
    <div class="chartwrap">
      <div class="chartmeta"><span class="mono sm" style="color:{LABEL}">
        {peak:,.0f}</span></div>
      <svg viewBox="0 0 1040 380" width="100%" height="380"
           preserveAspectRatio="none">{chart_svg}</svg>
      <div class="chartmeta" style="margin-top:10px">
        <span class="mono sm" style="color:{LABEL}">{_esc(run["x_start"])}</span>
        <span class="mono sm" style="color:{LABEL}">{_esc(run["x_end"])}</span>
      </div>
      <div class="legend">
        <span class="label"><i style="background:{TEXT}"></i>strategy · oos</span>
        <span class="label"><i style="background:{TEXT_DIM}"></i>buy &amp; hold</span>
      </div>
    </div>
    <div class="rail">
      {_label("scope")}
      <h2>{_esc(run["rail"]["heading"])}</h2>
      <div class="mono sm dim">{_esc(run["rail"]["meta"])}</div>
      <div style="margin-top:16px">{rail_rows}</div>
      <p class="railnote">{_esc(run["rail"]["note"])}</p>
    </div>
  </div>

  <div class="foot">{_esc(run["footnote"])}</div>

  <table>
    <thead><tr>{head_cells}<th></th></tr></thead>
    <tbody>{"".join(body_rows)}</tbody>
  </table>

  {robustness}
  {attribution}
</div>
</body></html>"""

    out_path.write_text(doc, encoding="utf-8")
    return out_path


__all__ = ["render", "bar_chart", "diverging_rows", "np"]
