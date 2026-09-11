"""Reference ORB as a protocol strategy -- stdin/stdout, no engine imports.

Deliberately standalone. It imports nothing from the engine, so it exercises
the same contract any other-language strategy would, and cannot accidentally share
state or helpers with the host. If this file needed engine code to agree with
the in-process version, the protocol would not be a real boundary.

stdout is protocol only. Anything diagnostic goes to stderr, which the engine
captures into the run log.

Run `python orb_cli.py --describe` to print the parameter schema.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PROTO = 1

DESCRIBE = {
    "proto": PROTO,
    "name": "orb_breakout",
    "version": "1.0",
    "params": [
        {"name": "or_minutes", "type": "int", "default": 30, "min": 5, "max": 120, "step": 5},
        {"name": "buffer_ticks", "type": "int", "default": 2, "min": 0, "max": 20, "step": 1},
        {"name": "stop_ticks", "type": "int", "default": 40, "min": 10, "max": 200, "step": 5},
        {"name": "target_r", "type": "float", "default": 2.0, "min": 0.5, "max": 6.0, "step": 0.5},
        {"name": "no_new_after", "type": "int", "default": 840, "min": 600, "max": 900, "step": 30},
        {"name": "qty", "type": "int", "default": 1, "min": 1, "max": 10, "step": 1},
    ],
}
DEFAULTS = {p["name"]: p["default"] for p in DESCRIBE["params"]}

RTH_OPEN_MIN = 8 * 60 + 30          # 08:30 exchange time
SESSION_SHIFT_HOURS = 7             # 17:00 CT belongs to the next trade date


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class ORB:
    """One entry per session, at the first bar that breaks the opening range."""

    def __init__(self, params: dict, tick_size: float, tz: str):
        p = {**DEFAULTS, **params}
        self.or_end = RTH_OPEN_MIN + int(p["or_minutes"])
        self.buf = int(p["buffer_ticks"]) * tick_size
        self.stop_dist = int(p["stop_ticks"]) * tick_size
        self.target_dist = self.stop_dist * float(p["target_r"])
        self.no_new_after = int(p["no_new_after"])
        self.qty = int(p["qty"])
        self.zone = ZoneInfo(tz)

        self.sess = None
        self.or_hi = None
        self.or_lo = None
        self.done = False

    def _keys(self, ts: int) -> tuple[int, object]:
        dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(self.zone)
        mod = dt.hour * 60 + dt.minute
        sess = (dt + timedelta(hours=SESSION_SHIFT_HOURS)).date()
        return mod, sess

    def on_bar(self, index: int, ts: int, o: float, h: float, l: float,
               c: float) -> dict | None:
        mod, sess = self._keys(ts)

        if sess != self.sess:
            self.sess, self.or_hi, self.or_lo, self.done = sess, None, None, False

        if RTH_OPEN_MIN <= mod < self.or_end:
            self.or_hi = h if self.or_hi is None else max(self.or_hi, h)
            self.or_lo = l if self.or_lo is None else min(self.or_lo, l)
            return None

        if self.done or self.or_hi is None:
            return None
        if not (self.or_end <= mod < self.no_new_after):
            return None

        long_break = h > self.or_hi + self.buf
        short_break = l < self.or_lo - self.buf

        # A bar breaking BOTH sides gives no ordering -- its high and low say
        # nothing about which came first -- so the session is skipped rather
        # than resolved by a coin flip. Marking it done matches the vectorised
        # version, which takes only the first breaking bar of a session.
        if long_break and short_break:
            self.done = True
            return None
        if not (long_break or short_break):
            return None

        self.done = True
        if long_break:
            return {"action": "buy", "type": "market", "qty": self.qty, "bar": index,
                    "stop_loss": c - self.stop_dist, "take_profit": c + self.target_dist,
                    "tag": "orb_long"}
        return {"action": "sell", "type": "market", "qty": self.qty, "bar": index,
                "stop_loss": c + self.stop_dist, "take_profit": c - self.target_dist,
                "tag": "orb_short"}


def main() -> int:
    if "--describe" in sys.argv[1:]:
        out(DESCRIBE)
        return 0

    strat: ORB | None = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("t")

        if kind == "init":
            if msg.get("proto") != PROTO:
                log(f"unsupported proto {msg.get('proto')!r}")
                return 2
            inst = msg.get("instrument", {})
            strat = ORB(msg.get("params", {}),
                        float(inst.get("tick_size", 0.25)),
                        msg.get("session", {}).get("tz", "America/Chicago"))
            log(f"init {inst.get('symbol')} tick={inst.get('tick_size')} "
                f"params={msg.get('params', {})}")
            out({"t": "ready"})

        elif kind == "bars":
            if strat is None:
                log("bars before init")
                return 2
            start = int(msg.get("start", 0))
            orders = []
            for offset, row in enumerate(msg["bars"]):
                ts, o, h, l, c, _v = row
                order = strat.on_bar(start + offset, int(ts), o, h, l, c)
                if order is not None:
                    orders.append(order)
            out({"t": "orders", "orders": orders} if orders else {"t": "noop"})

        elif kind == "fills":
            # This strategy brackets its entries, so the engine manages exits
            # and there is nothing to reconcile. Acknowledged, not ignored.
            pass

        elif kind == "end":
            out({"t": "bye"})
            return 0

        else:
            log(f"unknown message {kind!r}")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
