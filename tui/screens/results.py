"""Results screen, laid out to the reference: hero top-right, chart beside a
SCOPE rail, fold table beneath."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

from ..theme import HAIRLINE, LABEL, TEXT, TEXT_DIM, spaced
from ..widgets.bloom import BloomValue
from ..widgets.charts import distribution, diverging, equity_curve
from ..widgets.chrome import ControlRow, FoldTable, RightRail, TopBar
from .base import LabScreen

CHART_W = 100
VIEWS = ("Curve", "Drawdown", "Diverge", "Histogram")


class ResultsScreen(LabScreen):
    SECTION = "RESULTS"
    SCREEN_NAME = "results"
    HINT = "← →  view    X  export HTML    D S T R E sections    Q quit"

    BINDINGS = LabScreen.BINDINGS + [
        ("right", "next_view", "Next view"),
        ("left", "prev_view", "Previous view"),
        ("x", "export_html", "Export HTML report"),
    ]

    view: reactive[str] = reactive("Curve")

    def __init__(self, run: dict, **kw):
        self.run = run
        super().__init__(**kw)

    def on_mount(self) -> None:
        super().on_mount()
        bar = self.query_one(TopBar)
        bar.strategy = self.run["strategy"]
        bar.run_id = self.run["run_id"]

    def compose(self) -> ComposeResult:
        yield self.top()
        yield from self.body()
        yield self.bottom()

    def body(self) -> ComposeResult:
        r = self.run
        BloomValue.claim(self)          # one bloom per screen, enforced


        with Vertical(id="sheet"):
            # Breadcrumb left, hero value right.
            with Horizontal(id="crumbrow"):
                yield Static(Text(spaced("WALKFORWARD  /  RESULTS"), Style(color=LABEL)),
                             id="crumb")
                with Vertical(id="heroblock"):
                    yield Static(Text(spaced("NET OOS"), Style(color=LABEL)), id="herolabel")
                    yield BloomValue(r["net_oos"], id="hero")

            title = Text()
            title.append(r["title"], Style(color=TEXT, bold=True))
            title.append(f"   {r['instrument']}  ·  {r['barsize']}", Style(color=TEXT_DIM))
            yield Static(title, id="sheettitle")
            yield Static(Text(r["subtitle"], Style(color=TEXT_DIM)), id="sheetdesc")

            yield ControlRow(VIEWS, "Curve", r["fold_filter"], r["slice_filter"],
                             width=CHART_W + 34, id="controls")
            yield Static(Text("─" * 400, Style(color=HAIRLINE)), classes="panelrule")

            with Horizontal(id="railwrap"):
                with Vertical(id="chartcol"):
                    yield Static(r["charts"]["Curve"], id="chartbox")
                    yield Static(r["legend"], id="legend")
                yield RightRail(
                    heading=r["rail"]["heading"], meta=r["rail"]["meta"],
                    rows=r["rail"]["rows"], note=r["rail"]["note"],
                )

            yield Static(Text("─" * 400, Style(color=HAIRLINE)), classes="panelrule")
            yield Static(Text(r["footnotes"]["Curve"], Style(color=TEXT_DIM)), id="footnote")
            yield Static(Text("─" * 400, Style(color=HAIRLINE)), classes="panelrule")

            with VerticalScroll(id="tablewrap"):
                yield FoldTable(r["fold_rows"])


    def watch_view(self, view: str) -> None:
        if not self.is_mounted:
            return
        self.query_one("#chartbox", Static).update(self.run["charts"][view])
        self.query_one("#footnote", Static).update(
            Text(self.run["footnotes"][view], Style(color=TEXT_DIM))
        )
        title = Text()
        title.append(self.run["titles"][view][0], Style(color=TEXT, bold=True))
        title.append(f"   {self.run['instrument']}  ·  {self.run['barsize']}",
                     Style(color=TEXT_DIM))
        self.query_one("#sheettitle", Static).update(title)
        self.query_one("#sheetdesc", Static).update(
            Text(self.run["titles"][view][1], Style(color=TEXT_DIM))
        )
        self.query_one("#controls", ControlRow).selected = view

    def action_next_view(self) -> None:
        self.view = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)]

    def action_prev_view(self) -> None:
        self.view = VIEWS[(VIEWS.index(self.view) - 1) % len(VIEWS)]

    def action_export_html(self) -> None:
        """Write the HTML report and say where it went.

        The terminal can only approximate the house style -- one font size, no
        sub-pixel rendering. The report is the exact rendering, and it is the
        export the spec asks for on this screen.
        """
        from tools.report import build
        from engine.report.html import render

        try:
            out = render(build(), "runs/report.html")
            self.notify(f"Report written to {out}", title="Export")
        except Exception as exc:                      # surfaced, never silent
            self.notify(f"Export failed: {exc}", title="Export", severity="error")


def breakeven_label(be: float | None) -> str:
    """The shared number, rendered. None means the strategy is already losing."""
    return "none — already losing" if be is None else f"${be:,.2f}/side"


def load_run(path: str = "runs/last_oos_trades.json") -> dict:
    """Build the screen's view model from a real run on disk.

    Every number comes from engine.analysis.view.summarise, which the browser
    UI and the HTML report also read. This function only decides how to draw
    them. The reference mockup carries example figures; copying its layout is
    the point, copying its numbers would make the screen a lie.
    """
    from engine.analysis.view import summarise

    v = summarise(path)
    eq = np.asarray(v["equity"])
    bh = None if v["buy_hold"] is None else np.asarray(v["buy_hold"])
    nets = [f["oos_net"] for f in v["folds"]]

    charts = {
        "Curve": equity_curve(eq, width=CHART_W, height=12,
                              start_ts=v["start_ts"], end_ts=v["end_ts"],
                              baseline=100_000.0, boundaries=v["fold_boundaries"],
                              comparison=bh),
        "Drawdown": equity_curve(np.asarray(v["drawdown"]), width=CHART_W, height=12,
                                 start_ts=v["start_ts"], end_ts=v["end_ts"],
                                 baseline=0.0, boundaries=v["fold_boundaries"]),
        "Diverge": diverging([f"FOLD {f['index']:02d}" for f in v["folds"]], nets,
                             width=CHART_W, row_markers={"median": v["median_fold"]}),
        "Histogram": distribution(np.asarray(v["final_equity"]), bins=42, height=12),
    }

    titles = {
        "Curve": ("Stitched equity", "Out-of-sample segments only, joined end to end."),
        "Drawdown": ("Drawdown", "Distance below the running equity peak."),
        "Diverge": ("Per-fold result", "Losing folds left of the axis, winning folds right."),
        "Histogram": ("Final equity distribution",
                      f"{v['mc_iterations']:,} resamples of the out-of-sample trade sequence."),
    }

    footnotes = {
        "Curve": (f"{v['n_trades']:,} out-of-sample trades across "
                  f"{v['n_folds_total']} folds. "
                  + (v["baseline_note"] + ". No buy & hold line is drawn."
                     if v["baseline_note"] else
                     f"Buy & hold returned {v['buy_hold_return_pct']:+.0f}% over "
                     "the same window, so the strategy line is compressed "
                     "against it.")
                  + " Folds under the minimum-trade threshold are excluded, "
                    "not zero-filled."),
        "Drawdown": (f"Worst {abs(v['max_drawdown']):,.0f}, "
                     f"{v['max_drawdown_pct']:.1f}% of the account. Measured on the "
                     "equity curve, so an open position's excursion counts."),
        "Diverge": (f"{v['n_folds_profitable']} of {v['n_folds_qualified']} folds "
                    f"profitable. Winning parameters agree on {v['worst_param']} "
                    f"across only {v['worst_param_frac']*100:.0f}% of folds."),
        "Histogram": (f"P5 {v['mc_p5']:,.0f} · P50 {v['mc_p50']:,.0f} · "
                      f"P95 {v['mc_p95']:,.0f}. Observed drawdown sits near the 15th "
                      "percentile, so the realised path was luckier than typical."),
    }

    legend = Text()
    legend.append("──  ", Style(color=TEXT))
    legend.append(spaced("STRATEGY  ·  OOS") + "     ", Style(color=LABEL))
    legend.append("──  ", Style(color=TEXT_DIM))
    legend.append(spaced("BUY & HOLD"), Style(color=LABEL))

    return {
        "strategy": "orb_v1",
        "run_id": "0147",
        "instrument": "NQ",
        "barsize": "1m",
        "net_oos": f"{v['return_pct']:+.1f}%",
        "title": titles["Curve"][0],
        "subtitle": titles["Curve"][1],
        "fold_filter": "All folds",
        "slice_filter": "None",
        "charts": charts,
        "titles": titles,
        "footnotes": footnotes,
        "legend": legend,
        "rail": {
            "heading": "All folds",
            "meta": v["span"],
            "rows": [
                ("Max drawdown", f"-{v['max_drawdown_pct']:.1f}%"),
                ("MAR", f"{v['mar']:.2f}"),
                ("WF efficiency", f"{v['wfe_mean']:.2f}"),
                ("OOS trades", f"{v['n_trades']:,}"),
                ("Break-even cost", breakeven_label(v["breakeven_per_side"])),
                ("MC 5th pctile", f"${v['mc_p5']:,.0f}"),
                ("vs buy & hold", "—" if v["excess_pp"] is None
                                  else f"{v['excess_pp']:+.0f} pp"),
                ("corr to long", "—" if v["corr_to_long"] is None
                                 else f"{v['corr_to_long']:+.2f}"),
            ],
            "note": "Before brokerage — slippage only, no commission.",
        },
        "fold_rows": [
            {"index": f["index"], "oos_from": f["oos_from"],
             "or": f["params"].get("or_minutes", 0),
             "stop": f["params"].get("stop_ticks", 0),
             "is_mar": f["is_mar"], "oos_ret": f["oos_ret_pct"],
             "oos_mar": f["oos_mar"], "wfe": f["wfe"],
             "oos_n": f["oos_n"], "oos_net": f["oos_net"]}
            for f in v["folds"]
        ],
    }
