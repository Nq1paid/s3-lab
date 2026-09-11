"""Subprocess adapter: the protocol contract, and how it fails."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.adapters.line_protocol import (  # noqa: E402
    StrategyError, SubprocessStrategy, describe,
)
from engine.core.simulator import ExecConfig, Simulator  # noqa: E402
from engine.data.canonical import Instrument  # noqa: E402

NQ = Instrument("NQ", "test", 0.25, 5.0)
PY_EXE = sys.executable
ORB_CLI = [PY_EXE, str(ROOT / "strategies" / "reference" / "orb_cli.py")]


def stub(body: str) -> list[str]:
    """Write a throwaway strategy that misbehaves in a specific way."""
    import tempfile
    f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    f.write(textwrap.dedent(body))
    f.close()
    return [PY_EXE, f.name]


def bars(n=60):
    return {
        "ts": np.arange(1_600_000_000, 1_600_000_000 + n * 60, 60, dtype="int64"),
        "open": np.full(n, 15000.0), "high": np.full(n, 15010.0),
        "low": np.full(n, 14990.0), "close": np.full(n, 15000.0),
        "volume": np.ones(n, dtype="int64"),
    }


# ---- discovery

def test_describe_returns_the_parameter_schema():
    d = describe(ORB_CLI)
    assert d.proto == 1 and d.name == "orb_breakout"
    assert d.defaults["stop_ticks"] == 40
    assert any(p["name"] == "or_minutes" and p["step"] == 5 for p in d.params)


def test_describe_rejects_an_unknown_protocol_version():
    cmd = stub('''
        import json, sys
        print(json.dumps({"proto": 99, "name": "x", "version": "1"}))
    ''')
    with pytest.raises(StrategyError, match="unsupported protocol"):
        describe(cmd)


def test_describe_reports_non_json_output_with_the_command():
    cmd = stub('import sys; print("not json at all")')
    with pytest.raises(StrategyError, match="did not print one JSON object") as e:
        describe(cmd)
    assert "command :" in str(e.value)


# ---- failure modes, all loud

def test_a_crashing_strategy_fails_with_its_stderr():
    cmd = stub('''
        import sys
        print("boom on purpose", file=sys.stderr)
        raise SystemExit(3)
    ''')
    with pytest.raises(StrategyError) as e:
        with SubprocessStrategy(cmd, {}, NQ):
            pass
    assert "boom on purpose" in str(e.value)


def test_a_hanging_strategy_times_out_rather_than_blocking_forever():
    cmd = stub('''
        import time
        time.sleep(60)
    ''')
    with pytest.raises(StrategyError, match="did not reply within"):
        with SubprocessStrategy(cmd, {}, NQ, timeout=1.0):
            pass


def test_malformed_json_on_stdout_is_reported_not_parsed():
    cmd = stub('''
        import sys
        sys.stdin.readline()
        print("{ this is not json")
        sys.stdout.flush()
        sys.stdin.readline()
    ''')
    with pytest.raises(StrategyError, match="malformed JSON"):
        with SubprocessStrategy(cmd, {}, NQ, timeout=5.0):
            pass


def test_a_missing_executable_fails_immediately():
    with pytest.raises(StrategyError, match="could not start"):
        with SubprocessStrategy(["definitely-not-a-real-binary-xyz"], {}, NQ):
            pass


def test_a_strategy_that_never_says_ready_is_rejected():
    cmd = stub('''
        import json, sys
        sys.stdin.readline()
        print(json.dumps({"t": "orders", "orders": []}))
        sys.stdout.flush()
        sys.stdin.readline()
    ''')
    with pytest.raises(StrategyError, match="expected 'ready'"):
        with SubprocessStrategy(cmd, {}, NQ, timeout=5.0):
            pass


# ---- the run loop

def test_noop_is_a_valid_reply_and_produces_no_trades():
    cmd = stub('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if m["t"] == "init":
                print(json.dumps({"t": "ready"}))
            elif m["t"] == "bars":
                print(json.dumps({"t": "noop"}))
            elif m["t"] == "end":
                print(json.dumps({"t": "bye"})); break
            else:
                continue
            sys.stdout.flush()
    ''')
    with SubprocessStrategy(cmd, {}, NQ, timeout=10.0) as s:
        res = Simulator(NQ, ExecConfig(batch_size=20)).run(bars(), s)
    assert res.n_trades == 0


def test_a_malformed_order_is_rejected_without_losing_the_batch():
    cmd = stub('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if m["t"] == "init":
                print(json.dumps({"t": "ready"}))
            elif m["t"] == "bars":
                start = m["start"]
                print(json.dumps({"t": "orders", "orders": [
                    {"action": "sideways", "qty": 1, "bar": start},
                    {"action": "buy", "type": "market", "qty": 1, "bar": start},
                ]}))
            elif m["t"] == "end":
                print(json.dumps({"t": "bye"})); break
            else:
                continue
            sys.stdout.flush()
    ''')
    with SubprocessStrategy(cmd, {}, NQ, timeout=10.0) as s:
        res = Simulator(NQ, ExecConfig(batch_size=60)).run(bars(), s)
        assert s.rejected and "sideways" in s.rejected[0]
    assert res.n_trades >= 1, "the valid order in the same batch must still fill"


def test_the_strategy_is_never_sent_a_future_bar():
    cmd = stub('''
        import json, sys
        seen = []
        for line in sys.stdin:
            m = json.loads(line)
            if m["t"] == "init":
                print(json.dumps({"t": "ready"}))
            elif m["t"] == "bars":
                seen.append((m["start"], len(m["bars"])))
                print(json.dumps({"t": "noop"}))
            elif m["t"] == "end":
                print(json.dumps({"t": "bye", "seen": seen})); break
            else:
                continue
            sys.stdout.flush()
    ''')
    with SubprocessStrategy(cmd, {}, NQ, timeout=10.0) as s:
        Simulator(NQ, ExecConfig(batch_size=25)).run(bars(60), s)
    # 60 bars in batches of 25 -> starts 0, 25, 50 and no batch runs past the end.
