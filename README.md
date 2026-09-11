# S³ LAB

A futures backtesting and robustness engine. Walk-forward in-sample /
out-of-sample testing, four Monte Carlo robustness tests, and a browser UI.

It runs on your own machine. There is no account, no upload, and no service in
the middle — your price data never leaves the computer it is on.

## Install

Windows, with [Python 3.13](https://www.python.org/downloads/) installed:

```
install.bat
```

That builds a virtual environment, installs the dependencies, compiles the
reference Java strategy if a JDK is present, runs a self-test, and puts two
shortcuts on your Desktop. It is safe to run again.

## Run

Double-click **Backtest Lab**. A small window opens saying the engine is
running and your browser opens the app. Leave that window open while you use
it; closing it stops the engine.

**Backtest Lab (terminal)** is the same application as a text interface, for
when a browser is inconvenient. Both drive the same engine and report the same
numbers — there is a test that asserts it.

## What ships with it

The **full NQ 1-minute ETH series** (3.17M bars, 2017-2026, 31 MB) is committed
under `data/cache/`, and one **completed walk-forward** under `runs/`. Clone,
install, and the Results screen draws a real result immediately — nothing to
download first, no account to create, no vendor to sign up with.

Both are real. The bars are a straight vendor export normalised to Parquet; the
run is what this engine actually produced from them.

> If you forked this from someone whose data vendor forbids redistribution,
> delete `data/cache/NQ_1m_eth.parquet` and import your own export instead.
> Nothing else depends on it — though with the bars gone the buy-and-hold
> baseline will correctly refuse to draw rather than compute itself over a
> window it no longer covers.

## Add your data

Put 1-minute CSV or Parquet exports into `data\raw\`, then open the **Data**
screen and import them. The importer sniffs the column layout, infers the
timezone from the CME session calendar rather than from the filename, and
reports gaps, duplicates, zero-volume bars and roll dates before you rely on
any of it.

Full ETH sessions, continuous contract, not back-adjusted. RTH-only data cannot
model an overnight gap, so a stop that gaps overnight would fill at a price
that never traded.

## Add a strategy

A Python module exposing two things:

```python
DESCRIBE = {"proto": 1, "name": "my_strategy", "version": "1.0", "params": [
    {"name": "stop_ticks", "type": "int", "default": 40,
     "min": 10, "max": 200, "step": 5},
]}

def generate_orders(bars, tick_size, **params) -> list[Order]:
    ...
```

`DESCRIBE` is what removes the config file: the app reads your parameter schema
and builds the optimisation grid from it. Drop the folder on the **Strategies**
screen and an eight-step intake resolves, builds, interrogates and registers it,
showing every step and never failing quietly.

Java works too, and anything else that can read stdin and write stdout — see
`PROTOCOL.md`. `strategies/reference/` has a worked example in both languages.

## What it refuses to do

These are the constraints the whole thing is built around, and they are worth
reading before you trust a number it prints.

- **It never fabricates a result.** If a run did not execute, it says so. No
  placeholder metric is ever presented as output and there is no silent
  fallback to synthetic data.
- **Look-ahead bias is tested for explicitly.** An order cannot fill on the bar
  the strategy was looking at when it decided. That rule is the whole defence
  and it has its own test file.
- **Intrabar ambiguity always resolves against the strategy.** If one bar could
  have hit both the stop and the target, the stop is taken.
- **Absent bars are never filled in.** A minute with no bar means nothing
  traded, so no fill was available there.
- **It models no commission.** Slippage — one tick per side, against you — is
  the only cost applied, so every P&L figure is before brokerage. In its place
  the Results rail reports the **break-even cost per side**: the rate at which
  the result reaches zero. Compare it against your own broker instead of
  trusting a number this app invented.
- **A buy-and-hold baseline is always drawn beside the result**, with the
  correlation between the two. A strategy 0.9 correlated to being long is not a
  strategy.
- **Slices under 30 trades are marked not significant** and excluded from any
  conclusion the app draws. If more than 60% of net P&L comes from one slice,
  the Results screen says so at the top, in plain language.

## The bundled strategy is not a trading idea

`strategies/reference/orb.py` is an opening-range breakout that exists to prove
the engine works, and the engine's verdict on it is that it does not. On the
data it was developed against it returned +8.2% while buy-and-hold returned
+447%, its winning parameters are a spike rather than a plateau, the entire
result comes from one half-hour bucket in one year, and it breaks even at $2.22
per side — below any real retail commission.

That is the machine doing its job. Do not read its numbers as a starting point.

## On the web

`web/public` is four static files and no build step. Host them anywhere —
`web/DEPLOY.md` has the Vercel steps — and the page will talk to the engine on
your own machine at `127.0.0.1:8765`. The engine cannot be hosted: one raw
export is 169 MB against a 4.5 MB serverless request limit, and a Monte Carlo
run is about six minutes across fourteen worker processes against a sixty
second ceiling.

## Checking it

```
.venv\Scripts\python.exe -m pytest tests\ -q
.venv\Scripts\python.exe tools\verify.py
```

`verify.py` checks the claims in this file against the repository rather than
asserting them: that the palette is closed, that no screen is a dead end, that
look-ahead is defended, that the Python and Java implementations still produce
identical trade logs, that no commission model has crept back in, and that the
browser and terminal report the same numbers.

## Reading further

| | |
|---|---|
| `SPEC.md` | what was decided and, more usefully, what was measured |
| `PROTOCOL.md` | the strategy wire format and the fill semantics |
| `RUNBOOK.md` | how to run it and how to read the reports |
| `web/DEPLOY.md` | hosting the UI |
