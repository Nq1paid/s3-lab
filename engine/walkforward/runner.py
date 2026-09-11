"""Walk-forward execution: optimise in-sample, test out-of-sample, stitch.

The stitched OOS curve is the honest result. In-sample metrics are reported
only so walk-forward efficiency can be computed and so a strategy that only
works in-sample is visible as such.
"""

from __future__ import annotations

import importlib
import itertools
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np

from ..core.kernel import run_kernel
from ..core.metrics import Metrics, compute
from ..core.simulator import ExecConfig
from ..core.types import RunResult
from ..data.canonical import Instrument
from .splitter import Fold, WalkForwardConfig, make_folds, slice_bars

OBJECTIVES = ("mar", "sharpe", "profit_factor", "expectancy_x_trades")


def objective_value(m: Metrics, name: str) -> float:
    """Score an in-sample result. NaN is treated as unusable, never as zero."""
    if name == "mar":
        v = m.mar
    elif name == "sharpe":
        v = m.sharpe
    elif name == "profit_factor":
        v = m.profit_factor
    elif name == "expectancy_x_trades":
        v = m.expectancy * m.n_trades if m.n_trades else float("nan")
    else:
        raise ValueError(f"unknown objective {name!r}; expected one of {OBJECTIVES}")
    return float("-inf") if v is None or math.isnan(v) else float(v)


def full_range(spec: dict) -> list:
    """Every value a declared parameter can take, at its declared step."""
    if spec["type"] in ("int", "float") and {"min", "max", "step"} <= spec.keys():
        lo, hi, step = spec["min"], spec["max"], spec["step"]
        n = int(round((hi - lo) / step)) + 1
        return [int(lo + i * step) if spec["type"] == "int" else float(lo + i * step)
                for i in range(n)]
    if spec["type"] == "choice":
        return list(spec["options"])
    return [spec["default"]]


def build_grid(param_specs: list[dict], overrides: dict | None = None,
               sweep_all: bool = False) -> list[dict]:
    """Cartesian grid from a strategy's --describe schema.

    `overrides` maps a parameter name to the values to sweep. Anything NOT
    overridden is held at its default.

    That default matters. Expanding every declared parameter to its full range
    turns "sweep two parameters" into a combinatorial explosion without saying
    so: on the reference strategy, asking for 2 x 2 x 1 silently became 9,240
    combinations, because buffer_ticks, no_new_after and qty contribute 21, 11
    and 10 values of their own. The run does not fail, it just takes hours, and
    the UI's own estimate is wrong by three orders of magnitude.

    `sweep_all=True` restores the exhaustive behaviour for anyone who wants it.
    """
    overrides = overrides or {}
    axes: list[list] = []
    names: list[str] = []
    for spec in param_specs:
        name = spec["name"]
        names.append(name)
        if name in overrides:
            axes.append(list(overrides[name]))
        elif sweep_all:
            axes.append(full_range(spec))
        else:
            axes.append([spec["default"]])
    return [dict(zip(names, combo)) for combo in itertools.product(*axes)]


@dataclass
class FoldResult:
    index: int
    params: dict
    is_metrics: Metrics
    oos_metrics: Metrics
    oos_trades: list = field(default_factory=list)
    oos_equity: list = field(default_factory=list)
    oos_bar_ts: list = field(default_factory=list)
    disqualified: str = ""

    @property
    def wfe(self) -> float:
        """Walk-forward efficiency: OOS performance over IS performance."""
        a, b = self.oos_metrics.mar, self.is_metrics.mar
        if b is None or math.isnan(b) or b == 0 or math.isnan(a):
            return float("nan")
        return a / b


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    stitched: RunResult
    metrics: Metrics
    config: WalkForwardConfig
    objective: str
    grid_size: int
    elapsed_s: float
    warnings: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> list[FoldResult]:
        return [f for f in self.folds if not f.disqualified]

    def parameter_stability(self) -> dict[str, float]:
        """Fraction of qualified folds agreeing on the modal value, per param.

        Reported plainly because unstable winning parameters ARE the finding --
        a strategy whose best settings jump every fold has not been validated,
        it has been curve-fitted repeatedly.
        """
        good = self.qualified
        if not good:
            return {}
        out = {}
        for name in good[0].params:
            vals = [f.params[name] for f in good]
            out[name] = max(vals.count(v) for v in set(vals)) / len(vals)
        return out


# --- worker state -----------------------------------------------------------
_W: dict = {}


def _init_worker(parquet: str, flatten_minute: int | None, strategy_path: str,
                 instrument: Instrument, exec_cfg: ExecConfig) -> None:
    from ..data.annotate import annotate
    from ..data.loader import load

    df = load(parquet)
    bars = {k: df[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "volume")}
    module_name, func_name = strategy_path.rsplit(":", 1)
    _W["bars"] = annotate(bars, flatten_minute=flatten_minute)
    _W["gen"] = getattr(importlib.import_module(module_name), func_name)
    _W["inst"] = instrument
    _W["cfg"] = exec_cfg


def _run_slice(bars: dict, params: dict) -> RunResult:
    orders = _W["gen"](bars, _W["inst"].tick_size, **params)
    return run_kernel(bars, orders, _W["inst"], _W["cfg"])


def _process_fold(args) -> FoldResult:
    fold, grid, objective, min_trades = args
    bars = _W["bars"]
    is_bars = slice_bars(bars, fold.is_start, fold.is_end)
    oos_bars = slice_bars(bars, fold.oos_start, fold.oos_end)

    best_params, best_score, best_is = None, float("-inf"), None
    for params in grid:
        res = _run_slice(is_bars, params)
        m = compute(res)
        if m.n_trades < min_trades:
            continue                      # too few trades to be a signal
        score = objective_value(m, objective)
        if score > best_score:
            best_params, best_score, best_is = params, score, m

    if best_params is None:
        empty = compute(RunResult(starting_equity=_W["cfg"].starting_equity))
        return FoldResult(fold.index, {}, empty, empty,
                          disqualified=f"no parameter set reached {min_trades} in-sample trades")

    oos = _run_slice(oos_bars, best_params)
    oos_m = compute(oos)
    dq = "" if oos_m.n_trades >= min_trades else (
        f"only {oos_m.n_trades} OOS trades, below the {min_trades} threshold"
    )
    return FoldResult(
        index=fold.index, params=best_params, is_metrics=best_is, oos_metrics=oos_m,
        oos_trades=oos.trades, oos_equity=oos.equity, oos_bar_ts=oos.bar_ts,
        disqualified=dq,
    )


def run_walkforward(
    parquet: str,
    strategy_path: str,
    instrument: Instrument,
    grid: list[dict],
    wf_cfg: WalkForwardConfig | None = None,
    exec_cfg: ExecConfig | None = None,
    objective: str = "mar",
    flatten_minute: int | None = None,
    n_workers: int | None = None,
    progress=None,
    cancel=None,
) -> WalkForwardResult:
    import multiprocessing as mp

    wf_cfg = wf_cfg or WalkForwardConfig()
    exec_cfg = exec_cfg or ExecConfig()
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {objective!r}; expected one of {OBJECTIVES}")

    from ..data.annotate import annotate
    from ..data.loader import load

    df = load(parquet, columns=("ts",))
    ts = df["ts"].to_numpy()
    sess = annotate({"ts": ts}, flatten_minute=None)["sess"]
    folds = make_folds(sess, wf_cfg)
    if not folds:
        raise ValueError(
            f"data spans {len(np.unique(sess))} sessions, too few for "
            f"{wf_cfg.is_sessions} IS + {wf_cfg.oos_sessions} OOS"
        )

    warnings: list[str] = []
    if wf_cfg.step_sessions < wf_cfg.oos_sessions:
        warnings.append(
            "step is smaller than the OOS window, so OOS periods overlap between "
            "folds; the stitched curve counts those bars more than once"
        )

    n_workers = n_workers or min(len(folds), max(1, (os.cpu_count() or 2) - 2))
    tasks = [(f, grid, objective, wf_cfg.min_trades_per_fold) for f in folds]

    t0 = time.perf_counter()
    init_args = (parquet, flatten_minute, strategy_path, instrument, exec_cfg)
    if n_workers == 1:
        _init_worker(*init_args)
        results = []
        for i, t in enumerate(tasks):
            if cancel is not None and cancel():
                break
            results.append(_process_fold(t))
            if progress:
                progress(i + 1, len(tasks))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(n_workers, initializer=_init_worker, initargs=init_args) as pool:
            results = []
            for i, r in enumerate(pool.imap(_process_fold, tasks)):
                results.append(r)
                if progress:
                    progress(i + 1, len(tasks))
                if cancel is not None and cancel():
                    # Stop consuming; the pool is torn down by the context
                    # manager, so workers do not outlive the cancellation.
                    pool.terminate()
                    break
    elapsed = time.perf_counter() - t0

    results.sort(key=lambda r: r.index)
    if cancel is not None and cancel() and len(results) < len(folds):
        warnings.append(
            f"cancelled after {len(results)} of {len(folds)} folds; the stitched "
            "curve covers only the folds that completed"
        )
    stitched = _stitch(results, exec_cfg.starting_equity)
    return WalkForwardResult(
        folds=results, stitched=stitched, metrics=compute(stitched),
        config=wf_cfg, objective=objective, grid_size=len(grid),
        elapsed_s=round(elapsed, 2), warnings=warnings,
    )


def _stitch(results: list[FoldResult], starting_equity: float) -> RunResult:
    """Chain qualified folds' OOS equity into one continuous curve.

    Disqualified folds are skipped entirely rather than zero-filled: a fold that
    produced too few trades has no result, and carrying a flat line through it
    would understate drawdown and flatter the curve.
    """
    out = RunResult(starting_equity=starting_equity)
    running = starting_equity
    for f in results:
        if f.disqualified or not f.oos_equity:
            continue
        offset = running - starting_equity
        out.equity.extend(e + offset for e in f.oos_equity)
        out.bar_ts.extend(f.oos_bar_ts)
        for t in f.oos_trades:
            # Stamp the fold on the trade so attribution can slice by it later
            # without re-deriving fold membership from timestamps.
            t.fold = f.index
        out.trades.extend(f.oos_trades)
        running = out.equity[-1] if out.equity else running
    if not out.equity:
        out.equity = [starting_equity]
        out.bar_ts = [0]
    return out
