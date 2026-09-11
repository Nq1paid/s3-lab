"""Subprocess strategy adapter -- newline-delimited JSON over stdin/stdout.

Any language that can read stdin and write stdout can be a strategy. See
`PROTOCOL.md`; this is the engine half of that contract.

Failure is loud by construction. A strategy that crashes, hangs past the
timeout, or writes malformed JSON fails the run and reports the command, the
exit code and the stderr tail. It never silently produces zero trades, because
"no trades" and "the strategy died" look identical in a result and only one of
them is a finding.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.types import Order, OrderType, Side

PROTO = 1
STDERR_TAIL = 40


class StrategyError(RuntimeError):
    """A strategy failed. Carries everything needed to see why."""

    def __init__(self, message: str, command: list[str], returncode: int | None,
                 stderr: list[str]):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n    ".join(stderr[-STDERR_TAIL:]) or "(no stderr output)"
        super().__init__(
            f"{message}\n"
            f"  command : {' '.join(command)}\n"
            f"  exit    : {returncode if returncode is not None else 'still running'}\n"
            f"  stderr  :\n    {tail}"
        )


@dataclass
class Describe:
    """What a strategy says about itself. This is what removes config files."""

    proto: int
    name: str
    version: str
    params: list[dict] = field(default_factory=list)

    @property
    def defaults(self) -> dict:
        return {p["name"]: p["default"] for p in self.params if "default" in p}


def _pump_stderr(stream, sink: list[str]) -> None:
    for raw in iter(stream.readline, ""):
        sink.append(raw.rstrip("\n"))
    stream.close()


def _pump_stdout(stream, sink: "queue.Queue[str | None]") -> None:
    for raw in iter(stream.readline, ""):
        sink.put(raw)
    sink.put(None)          # EOF sentinel
    stream.close()


def describe(command: list[str], timeout: float = 30.0) -> Describe:
    """Run `--describe` and parse the parameter schema."""
    cmd = list(command) + ["--describe"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise StrategyError(f"--describe timed out after {timeout}s", cmd, None,
                            (exc.stderr or "").splitlines()) from None
    except OSError as exc:
        # Not executable, wrong architecture, missing interpreter. This is a
        # normal way for a dropped folder to be wrong and must read as a
        # strategy failure, not an internal error.
        raise StrategyError(f"could not execute: {exc}", cmd, None, []) from None
    if out.returncode != 0:
        raise StrategyError("--describe exited non-zero", cmd, out.returncode,
                            out.stderr.splitlines())
    try:
        payload = json.loads(out.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise StrategyError(
            f"--describe did not print one JSON object; got {out.stdout[:200]!r}",
            cmd, out.returncode, out.stderr.splitlines()) from None

    proto = payload.get("proto")
    if proto != PROTO:
        raise StrategyError(
            f"unsupported protocol {proto!r}; this engine speaks proto {PROTO}",
            cmd, out.returncode, out.stderr.splitlines())
    return Describe(proto=proto, name=payload.get("name", "unnamed"),
                    version=str(payload.get("version", "0")),
                    params=payload.get("params", []))


class SubprocessStrategy:
    """Drives a child process as a strategy. Usable anywhere a Strategy is."""

    def __init__(self, command: list[str], params: dict, instrument,
                 session: dict | None = None, timeout: float = 60.0,
                 cwd: str | Path | None = None):
        self.command = list(command)
        self.params = dict(params)
        self.instrument = instrument
        self.session = session or {"tz": "America/Chicago", "rth": ["08:30", "15:00"]}
        self.timeout = timeout
        self.cwd = str(cwd) if cwd else None
        self.proc: subprocess.Popen | None = None
        self.stderr: list[str] = []
        self._out: queue.Queue[str | None] = queue.Queue()
        self.rejected: list[str] = []

    # ---- lifecycle

    def __enter__(self) -> "SubprocessStrategy":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, cwd=self.cwd,
                encoding="utf-8",
            )
        except OSError as exc:
            raise StrategyError(f"could not start strategy: {exc}",
                                self.command, None, []) from None

        threading.Thread(target=_pump_stderr, args=(self.proc.stderr, self.stderr),
                         daemon=True).start()
        threading.Thread(target=_pump_stdout, args=(self.proc.stdout, self._out),
                         daemon=True).start()

        self._send({
            "t": "init", "proto": PROTO, "params": self.params,
            "instrument": {
                "symbol": self.instrument.symbol,
                "tick_size": self.instrument.tick_size,
                "tick_value": self.instrument.tick_value,
            },
            "session": self.session,
        })
        reply = self._recv()
        if reply.get("t") != "ready":
            self._fail(f"expected 'ready' after init, got {reply.get('t')!r}")

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                self._send({"t": "end"})
                try:
                    self._recv()            # "bye", best effort
                except StrategyError:
                    pass
        finally:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None

    # ---- wire

    def _fail(self, message: str):
        rc = self.proc.poll() if self.proc else None
        raise StrategyError(message, self.command, rc, self.stderr)

    def _send(self, obj: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            self._fail("strategy is not running")
        try:
            self.proc.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._fail("strategy closed its stdin (it probably crashed)")

    def _recv(self) -> dict:
        try:
            line = self._out.get(timeout=self.timeout)
        except queue.Empty:
            self._fail(f"strategy did not reply within {self.timeout}s")
        if line is None:
            self._fail("strategy closed its stdout without replying")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            self._fail(f"strategy wrote malformed JSON on stdout: {line[:200]!r}")

    # ---- Strategy interface

    def on_batch(self, batch, position: int, equity: float) -> list[Order]:
        rows = [
            [int(batch.ts[i]), float(batch.open[i]), float(batch.high[i]),
             float(batch.low[i]), float(batch.close[i]), int(batch.volume[i])]
            for i in range(len(batch))
        ]
        self._send({"t": "bars", "bars": rows, "start": int(batch.start),
                    "pos": int(position), "equity": float(equity)})
        reply = self._recv()
        kind = reply.get("t")
        if kind == "noop":
            return []
        if kind != "orders":
            self._fail(f"expected 'orders' or 'noop', got {kind!r}")

        out: list[Order] = []
        for raw in reply.get("orders", []):
            try:
                out.append(self._order(raw))
            except (KeyError, ValueError) as exc:
                # A malformed order is a strategy bug and must be visible, but
                # it should not destroy an otherwise valid batch silently.
                self.rejected.append(f"{exc}: {raw}")
        return out

    def on_fills(self, fills: list) -> None:
        self._send({"t": "fills", "fills": [
            {"ts": f.ts, "bar": f.bar, "side": f.side.value, "qty": f.qty,
             "price": f.price, "reason": f.reason} for f in fills
        ]})

    @staticmethod
    def _order(raw: dict) -> Order:
        action = str(raw["action"]).lower()
        if action not in ("buy", "sell"):
            raise ValueError(f"unsupported action {action!r}")
        return Order(
            side=Side(action),
            type=OrderType(str(raw.get("type", "market")).lower()),
            qty=int(raw.get("qty", 1)),
            bar=int(raw["bar"]),
            price=raw.get("price"),
            stop_loss=raw.get("stop_loss"),
            take_profit=raw.get("take_profit"),
            tag=str(raw.get("tag", "")),
        )
