# S³ LAB — futures backtesting & robustness engine

Source of truth for this build. Kept current as decisions are made.

## Purpose

Run a strategy through walk-forward in-sample / out-of-sample testing, then put
surviving parameter sets through four Monte Carlo robustness tests. The engine
does not care what language a strategy is written in: Python and Java ship with
reference implementations, and anything that speaks the line protocol works.

Two constraints dominate every design decision:

1. **No config-file editing.** Every feature is reachable from the UI. If a
   feature can't be driven from the UI it isn't done. A config file may exist;
   the user should never need to open it.
2. **No commands to start it.** After install, a double-clicked desktop shortcut
   opens the app.

## Interfaces

Two front ends over one engine.

**Browser UI** (`web/public`) is the primary one. Four static files, no build
step, deployable to Vercel or any static host; `web/DEPLOY.md` has the steps.
It is what the design was always reaching for -- a real type scale, vector
charts, a pointer -- and the terminal was only ever an approximation of it.

**Terminal UI** (`tui/`) still ships and still works. It needs nothing but a
console, which is the reason to keep it.

**The engine is always local.** `web/server.py` serves the API on
`127.0.0.1:8765` and the browser page talks to it, hosted or not. This is not a
preference:

| | this project | Vercel serverless |
|---|---|---|
| one raw export | 169 MB | 4.5 MB request body |
| cached series | 61 MB memory-mapped Parquet | read-only disk, `/tmp` wiped per cold start |
| one walk-forward | 3.17M bars, ~25 s | 60 s ceiling |
| Monte Carlo, 10,000 iterations | ~6 min over 14 processes | no multiprocessing |

A hosted engine would have to run a toy on truncated data, which the
non-negotiables forbid. So the pages are hosted and the work is local.

**Neither front end computes anything.** Both read `engine.analysis.view`
`summarise()`, and a test asserts the browser API and the terminal screen report
the same MAR, return and trade count for the same run. Two surfaces that did
their own arithmetic would eventually disagree, and a backtester with two
answers for one run is worse than one with none.

## Verified environment (checked 2026-09-09, not assumed)

| | |
|---|---|
| CPU / RAM | Ryzen 7 7700X, 8C/16T, 31 GB, 138 GB free on C: |
| Python | project venv pinned to **3.13.7** (3.14 present but numba does not support it) |
| Java | Temurin JDK 26 — **no Maven, no Gradle** |
| C++ | not installed - and C++ is out of scope, see below |
| Windows Terminal | present — required for truecolor gradients and drag-to-paste |

Installed in `.venv`: numpy 2.5.3, pandas 3.0.5, pyarrow 25.0.1, numba 0.67.0,
textual 8.2.8, pytest 9.1.1.

Java uses plain `javac`/`jar`; Maven and Gradle are not required.

**C++ is out of scope.** Dropped by the user rather than left unfinished. The
protocol is language-agnostic and the intake pipeline already detects and runs
a compiled binary, so a C++ strategy would be written against the existing
contract without changing the engine -- there is simply no reference
implementation and no build wiring shipped for it.

## Verified data (audited 2026-09-09)

`data/raw/@NQ - 1 min - ETH.csv`, `data/raw/@ES - 1 min - ETH.csv`

| | NQ | ES |
|---|---|---|
| Bars | 3,165,675 | 3,307,327 |
| Span | 2017-04-17 → 2026-09-09 (9.40 yr) | same |
| Duplicate timestamps | 0 | 0 |
| OHLC violations / NaN / non-positive | 0 | 0 |
| Zero-volume bars | 85 (0.003%) | 0 |
| Minutes with no bar at all | ~19,400 | ~5,800 |

**Timezone: `America/Chicago`, bars stamped at their OPEN.** Established from the
CME session calendar, not from filenames or volume heuristics:

- daily halt: last bar 15:59 to first bar 17:00 (16:00–17:00 CT break)
- Christmas Eve and day-after-Thanksgiving: last bar 12:14 (12:15 CT early close)
- Sunday open 17:00 (474 Sundays); Friday close 15:59 (446 Fridays)

**A volume-peak heuristic is not sufficient to infer timezone.** The busiest
minute of the day is the cash *close*, not the open — ES averages 49,914 at
14:59 CT versus 12,674 at the 08:30 open. The detector fingerprints against the
CME holiday/halt calendar instead.

**Timezone is per file, never global.** The 5-minute `@NQ` export on this machine
is in `America/New_York` while these 1-minute files are `America/Chicago` — same
vendor, same machine. Every import infers its own timezone and shows the
evidence for confirmation.

**Continuous contracts are unadjusted.** Price ranges (NQ 5,375 to 30,975,
ES 2,174 to 7,838) are true historical levels; a back-adjusted series would have
drifted off them across 37 quarterly rolls. Roll-window overnight gaps have
median +0.589%, consistent with raw splicing at cost-of-carry.

Consequence: ~4 artificial gaps per year at rolls. Harmless for strategies flat
by the close; fake PnL for anything held overnight. The engine detects roll
dates, records them in run metadata, defaults swing mode to force-flat across a
roll boundary, and tags roll-adjacent trades in the attribution slicer.

**Absent bars are never forward-filled.** A minute with no bar means nothing
traded, so no fill was available there. Synthesising those bars would invent
fills that never existed. Tested explicitly.

## Architecture

**Core engine — Python 3.13.** Owns the event loop, order simulation, fills,
costs, position and equity accounting, walk-forward slicing, Monte Carlo and
reporting. Strategies emit orders and nothing else.

**Strategy adapter.** Subprocess line protocol — see `PROTOCOL.md`. Plus an
in-process fast path for Python strategies, proven equivalent by test.

**Data layer.** Scan a folder, sniff schemas, confirm the column mapping once and
persist it. Normalise to canonical Parquet, memory-map for reads. Detect and
flag gaps, duplicates, zero-volume bars, session boundaries, roll dates, and
whether a series is continuous or raw.

**Storage.** Run history in SQLite; normalised bars and run artifacts as Parquet
under `data/cache/` and `runs/`.

## Walk-forward

Rolling **and** anchored/expanding, selectable in the UI. Configurable IS length,
OOS length, step, and a minimum-trades-per-fold threshold that disqualifies a
fold rather than reporting noise.

Default objective **return over max drawdown (MAR-style)**; Sharpe, profit
factor and expectancy × trade count also selectable.

Per fold: chosen params, IS metrics, OOS metrics, walk-forward efficiency
(OOS ÷ IS). The **stitched OOS equity curve is the honest result** and the UI
leads with it. Parameter stability across folds is reported in plain language —
if winning params jump between folds, the app says so.

**Buy-and-hold baseline always shown** — same period, instrument and slippage
applied to a single entry and exit, same contract count. Drawn as a dim
second line, with excess return and the correlation between strategy returns and
the underlying's. A strategy 0.9 correlated to being long is not a strategy.

## Attribution

OOS trades only, sliced by: time of day (30-min buckets plus overnight), day of
week, year, month, volatility regime (5 quantiles of ATR at entry), direction
(long/short never netted), session (RTH/ETH), trade duration, fold.

Per slice: trade count, net PnL, expectancy, win rate, MAR, share of net PnL.

Slices under 30 trades are marked not significant and excluded from any
conclusion the app draws. **If more than 60% of net PnL comes from one slice,
the Results screen says so at the top in plain language.**

## Monte Carlo

Percentile bands, never a single number.

| Method | Runs on | Default iterations | Cost |
|---|---|---|---|
| 1. Trade-sequence bootstrap | existing trade list | 10,000 | cheap |
| 2. Parameter perturbation | re-run, ±k steps | 10,000 | moderate |
| 3. Randomised entries / noise injection | full re-simulation | 10,000 | ~6 min / 14 workers |
| 4. Block bootstrap of price series | full re-simulation | 10,000 | ~6 min / 14 workers |

**Measured end to end, all four methods can run at 10,000 iterations.** The
earlier estimate of 88 core-hours came from assuming a pure-Python core and was
wrong by roughly two orders of magnitude.

A full re-simulation of the reference strategy over all 3,165,675 NQ bars costs
0.51s -- 0.11s order generation plus 0.40s fill simulation. Session annotation
(0.58s) depends only on timestamps, so a price-resampling method reuses it
rather than recomputing it. That puts 10,000 block-bootstrap iterations at ~85
core-minutes, or roughly 6 minutes across 14 workers.

The defaults above are therefore raised to 10,000 for all four methods. The cost
estimate is still shown before a run and the run is still cancellable, because a
slower user strategy can move this by an order of magnitude and the engine must
not assume the reference strategy's speed is representative.

Method 1 outputs final equity, max drawdown, longest losing streak and risk of
ruin against a configurable account size. Method 2 outputs a stability surface
and a plateau-vs-spike verdict — the main defence against curve-fitting, and
weighted accordingly in the UI. Method 3 reports degradation as a percentage of
baseline. Method 4 resamples contiguous blocks (default ~1 session).

## Execution realism

Per-instrument specs for ES/MES/NQ/MNQ, editable in the UI. Micros are backtested
on ES/NQ price series with micro multipliers — same underlying, no separate data.

**No commission or fee model.** Dropped by the user rather than left as an
unconfirmed placeholder, and the engine carries no trace of one: no field on
`Instrument`, no argument to the kernel, no column in the trade log. Every P&L
figure is therefore **before brokerage**, and the UI says so on the Test screen,
the Settings screen and the Results rail rather than leaving it to be assumed.

The compensating number is the **break-even cost per side**, shown on the
Results rail and computed from net P&L over round trips. It is the rate at which
the result reaches zero, so a reader applies their own broker's number by
comparison instead of trusting one the app invented. The reference ORB strategy
breaks even at $2.22 per side, which is below any real retail rate -- the number
does its job.

Slippage 1 tick per side, against the strategy. Account size $100,000. Fill
semantics per `PROTOCOL.md`. Session awareness (RTH/ETH), optional
flatten-at-session-end, no-new-trades window before the close, roll handling
recorded in run metadata.

## Performance

The engine hot loop is a numba kernel over numpy arrays with a pure-Python
reference beside it, and tests assert the two emit identical trade logs across
30 randomised seeds.

Measured on real NQ bars:

| path | throughput |
|---|---|
| pure-Python reference | 520,376 bars/s |
| numba kernel (fills + accounting) | 8,125,424 bars/s |
| in-process strategy, end to end | ~6,600,000 bars/s |
| subprocess line protocol, batch 500 | ~257,000 bars/s |

### The line protocol can carry a full sweep, if bars are batched

This was flagged as the main technical risk, so it was measured before the UI
was built on top of it. Batch size against throughput, 50k real bars:

| batch | bars/s | vs in-process |
|---|---|---|
| 1 | 15,190 | 373x slower |
| 5 | 53,269 | 106x |
| 25 | 140,542 | 40x |
| 100 | 214,625 | 26x |
| 500 | 256,935 | 22x |
| 2,000 | 264,115 | 21x |

Per-bar round-trips are as bad as feared: at 15k bars/s a single pass over
3.17M bars takes 211 seconds, and a 2,400-run sweep would take 140 hours.

Batching fixes it, and the gain is almost entirely captured by 500. Beyond that
the curve is flat, because the remaining cost is JSON encoding and the
strategy's own per-bar work rather than IPC. At batch 500 a full pass costs
12.3s and a 12-fold by 200-combo sweep is roughly 1.8 core-hours, about 8
minutes across 14 workers.

So the default of 500 is correct, raising it buys nothing, and lowering it
below ~100 is what actually hurts.

## Non-negotiables

- **Never fabricate results.** If a run didn't execute, say so. No placeholder
  metrics presented as output, no silent fallback to synthetic data.
- **Look-ahead bias is the failure mode that makes everything worthless.** Tested
  explicitly and separately.
- Tests for all money code: fills, costs, PnL accounting, walk-forward slicing.
- Verify on real data before calling a phase done, and paste real output.

## Build order

**Phase 1** — data loader plus canonical schema, engine core, Python adapter and
fast path, walk-forward (rolling plus anchored), trade-sequence bootstrap,
minimal TUI, performance benchmark on real NQ data.

**Phase 2** — parameter perturbation, noise injection, block bootstrap,
attribution slicer, results browser, run history, comparison, HTML export.

**Phase 3** — Java adapter, cross-language conformance test (in-process
Python, Python over the pipe, Java), `install.bat`, desktop shortcut,
self-test. C++ dropped from scope.

Commit at the end of each phase with a working tree.
