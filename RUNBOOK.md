# RUNBOOK

How to run it, how to add a strategy, how to read what it tells you.

## Starting it

Double-click **Backtest Lab** on your Desktop. A small window opens saying the
engine is running, and your browser opens the app. That is the whole startup
story.

Leave that window open while you use the app. Closing it stops the engine.

**Backtest Lab (terminal)** is the original text interface. It still works and
does the same things; it is there because it needs nothing but a console.

### On the web

The browser UI is four static files in `web/public`, and it is hosted wherever
you put them. See `web/DEPLOY.md` for the Vercel steps — drag the folder onto
vercel.com and you get a URL that works from any machine.

The hosted page is the interface only. The engine always runs on this PC,
because a walk-forward here reads 3.3 million bars across fourteen worker
processes and no serverless function can do that. The page finds the engine on
`127.0.0.1:8765` by itself; the indicator in the top right says `ENGINE LIVE`
when it has. Your bars are never uploaded anywhere.

If the shortcut is missing or you moved the folder, run `install.bat` again —
it is safe to re-run, it reuses the existing venv and only rebuilds what is
missing.

Nothing here needs a config file edited. If a feature can only be reached by
editing a file, that is a bug, not a workflow.

### If it will not start

Run `install.bat`. It ends with the self-test, which names the failure:

| Line | Meaning |
|---|---|
| `[ FAIL ] python version` | Install Python 3.13 from python.org and re-run |
| `[ FAIL ] numba kernel` | Wrong Python. numba has no 3.14 wheel; the venv must be 3.13 |
| `[ WARN ] data cache` | Normal on a fresh install — no data imported yet |
| `[ WARN ] java toolchain` | Java strategies unavailable; Python ones still work |

Warnings never block the app. Failures do.

You can run the self-test alone at any time:

```
.venv\Scripts\python.exe selftest.py
```

## Keys

**D S T R E M** are the sections, **H** is run history, **Q** quits, and they
work from anywhere. No screen rebinds them -- a screen that borrowed one would
be a screen you could not leave, and a test now fails if one tries.

Everything else is arrow keys and ENTER. The few extra keys are printed in the
bar at the bottom of whichever screen you are on, and **F5 re-reads the source**
on both Data (rescan the folder) and Strategies (re-validate a strategy).

Nothing is focused when a screen opens, so a letter is always a command. On the
Strategies screen, ENTER opens the path field and ESC leaves it again; dragging
a folder onto the window works whether or not that field is open.

**PageUp / PageDown / Home / End** scroll whichever panel is long, on every
screen. That is how a long run history or a long attribution table is read
without a mouse.

## Adding data

Save CSV or Parquet files into `data\raw\`. Nothing is uploaded or copied
anywhere; the loader reads them off disk.

The importer works out the rest and shows you what it inferred before anything
runs:

- **Column mapping** is sniffed, and confirmed once per vendor format. A second
  export in the same shape is recognised without asking again.
- **Timezone is detected per file**, from the CME session calendar — the daily
  16:00–17:00 CT halt, the 12:15 CT holiday-eve close, the Sunday 17:00 reopen.
  Never from the filename. Two exports from the same vendor on this machine are
  in *different* timezones, which is exactly why.
- **Quality** is reported: gaps, duplicates, zero-volume bars, session
  boundaries, and whether the series is continuous or a single contract.

Bars that do not exist are never forward-filled. A minute with no bar means
nothing traded, so no fill was available there.

### What to export

1-minute bars, **full ETH session** (not RTH), continuous contract, volume
included, no back-adjustment. RTH-only data cannot model an overnight gap, so a
stop that gaps overnight will fill at a price that never traded.

## Adding a strategy

### Python

A module exposing `generate_orders(bars, tick_size, **params) -> list[Order]`
and a `DESCRIBE` dict. See `strategies/reference/orb.py`.

`DESCRIBE` is what makes the no-config rule work — the app reads the parameter
schema from it and builds the optimisation grid itself:

```python
DESCRIBE = {"proto": 1, "name": "orb_breakout", "version": "1.0", "params": [
    {"name": "stop_ticks", "type": "int", "default": 40,
     "min": 10, "max": 200, "step": 5},
]}
```

`step` matters beyond the grid: parameter perturbation jitters by the declared
step, so a parameter that moves in fives is tested at ±5, not ±1.

Orders carry the **absolute index of the bar the strategy was looking at**. The
engine will not fill one before `bar + 1`, and rejects an order tagged outside
the batch it was just shown. That rule is the whole of the look-ahead defence.

### Java

A `.jar` that supports `--describe` and speaks the line protocol in
`PROTOCOL.md`. Built with `javac`/`jar` directly — Maven and Gradle are not
required and are not installed here. `install.bat` builds it when a JDK is
present; to rebuild by hand:

```
strategies/reference/java/build.bat
```

The reference implementation is `strategies/reference/java/OrbStrategy.java`,
with a ~160-line `Json.java` beside it. That is the entire dependency footprint
of a Java strategy: a protocol that needs a build system to join would not be
one any language can implement.

The conformance test asserts the Java jar, the Python CLI and the
in-process Python strategy produce an identical trade log on real NQ data. If
you change one, that test tells you.

### Any other language

The protocol has no Python or Java in it. Anything that can read stdin, write
stdout and support `--describe` can be a strategy -- see `PROTOCOL.md`. Drop the
built binary on the Strategies screen and the intake pipeline treats it like
any other.

C++ is not shipped: no reference implementation and no build wiring. The
adapter path is generic, so adding one later is writing the strategy, not
changing the engine.

## Reading the reports

Everything below is computed on **out-of-sample trades only**.

### Stitched equity

Every fold's out-of-sample period, chained end to end. This is the honest
result. In-sample numbers appear only so walk-forward efficiency can be
computed, and so a strategy that works only in-sample is visible as such.

The dim second line is **buy & hold** over the same window with the same costs.
If your strategy sits below it, it lost to doing nothing, and the rail reports
both the gap and the correlation. A strategy highly correlated to being long is
not a strategy.

### Walk-forward efficiency

Out-of-sample MAR divided by in-sample MAR, per fold. Around 1.0 means the
strategy held up. Wildly varying values across folds are not skill, they are
noise — and that variation *is* the finding.

### Parameter stability

The share of folds agreeing on each winning parameter. Low agreement means the
strategy was curve-fitted once per fold rather than validated once.

### Monte Carlo

| Method | Question it answers |
|---|---|
| Trade bootstrap (resample) | What might this edge have produced on a different draw? |
| Trade bootstrap (shuffle) | What would the *same* trades in a different order have done? |
| Parameter perturbation | Is the winner on a plateau, or on a spike? |
| Noise injection | Does it survive a tick of price noise and a bar of timing slip? |
| Block bootstrap | Does it survive a reshuffled history? |

Read shuffle's **drawdown and streak** bands, never its final equity — the
trade set is preserved, so final equity is identical in every path by
construction.

**Parameter perturbation is the one that matters most.** A SPIKE verdict means
being one grid step wrong about a parameter erases the result, and you cannot
pick the winning parameters in advance.

### Attribution

Where the result actually comes from. A slice under 30 trades is marked not
significant and excluded from any conclusion the app draws.

If one slice holds more than 60% of net P&L, that is said at the top in plain
language. **That is concentration, not an edge.** If a single half-hour bucket
or a single year carries the whole book, the result depends on that one
condition repeating.

## Costs

**Every P&L figure in this app is before brokerage.** There is no commission or
fee model — it was removed on purpose, so nothing here quietly charges you a
rate someone guessed at.

Slippage is the one cost applied: 1 tick per side by default, always against
you, and it lives in the fill price. Intrabar ambiguity is likewise always
resolved against the strategy — see `PROTOCOL.md`.

To judge a result against your own broker, read **break-even cost** on the
Results rail. That is the per-side rate at which the strategy's net reaches
zero, across two sides per round trip. If your broker charges more than that
number, the strategy loses money at your desk — no arithmetic needed. A
strategy that is already losing has no break-even cost, and the rail says so
rather than printing a negative number.

## Run history

Every completed run is saved automatically. Press **H** for the browser.

Each row stores the configuration **and the costs it was computed with**, so a
past result never silently re-reads whatever the settings say today.

- **SPACE** marks a run; mark two and the comparison appears beneath.
- **X** exports the selected run trade log to `runs/trades_<id>.csv`.

The comparison lists configuration differences rather than leaving them to be
noticed. Two runs with different costs or windows are not directly comparable,
and when they are, it says so.

## Verifying the install

```
.venv/Scripts/python.exe tools/verify.py
```

Checks the palette is closed, no screen is a dead end, look-ahead is defended,
the three implementations still agree, the data is cached, the costs are
honestly labelled, and the shortcut exists. It exits non-zero if any of that
stops being true.

## Exporting

Press **E** on the Results screen to write `runs\report.html`. It is
self-contained — no external stylesheet, font or script — so it opens anywhere
or mails cleanly.

The terminal has one font size and no sub-pixel rendering, so the report is the
exact rendering of the house style and the TUI is the approximation.

## Where things live

| Path | |
|---|---|
| `data\raw\` | your CSV/Parquet exports, read in place |
| `data\cache\` | normalised Parquet plus the per-file metadata sidecar |
| `runs\` | run artifacts and exported reports |
| `SPEC.md` | the design, the verified environment, the data audit |
| `PROTOCOL.md` | the strategy wire format and fill semantics |
| `selftest.py` | run it any time; it names what is broken |
