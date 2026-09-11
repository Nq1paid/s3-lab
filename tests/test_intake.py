"""Strategy intake: the eight-step pipeline, and how it refuses."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.adapters.intake import (  # noqa: E402
    Language, Registry, STEPS, detect_language, find_entry, intake,
)

ORB_CLI = ROOT / "strategies" / "reference" / "orb_cli.py"
ORB_JAR = ROOT / "strategies" / "reference" / "java" / "orb.jar"


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path / "registry.json")


def drop(tmp_path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ---- language detection

def test_detects_python_from_a_bare_file():
    lang, marker = detect_language(ORB_CLI)
    assert lang is Language.PYTHON and marker.endswith(".py")


@pytest.mark.skipif(not ORB_JAR.exists(), reason="orb.jar not built")
def test_detects_java_from_a_bare_jar():
    assert detect_language(ORB_JAR)[0] is Language.JAVA


def test_detects_python_from_a_project_marker(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    lang, marker = detect_language(tmp_path)
    assert lang is Language.PYTHON and marker == "pyproject.toml"


def test_detects_native_from_cmake(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    assert detect_language(tmp_path)[0] is Language.NATIVE


def test_unknown_language_is_reported_not_guessed(tmp_path):
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    lang, marker = detect_language(tmp_path)
    assert lang is Language.UNKNOWN and "no language marker" in marker


# ---- entry point

def test_entry_point_prefers_convention(tmp_path):
    for name in ("helper.py", "strategy.py", "other.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    entry, _ = find_entry(tmp_path, Language.PYTHON)
    assert entry.name == "strategy.py"


def test_ambiguous_entry_point_is_not_guessed(tmp_path):
    for name in ("alpha.py", "beta.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    entry, candidates = find_entry(tmp_path, Language.PYTHON)
    assert entry is None
    assert {c.name for c in candidates} == {"alpha.py", "beta.py"}


# ---- the pipeline

def test_a_real_python_strategy_registers(registry):
    reg = intake(str(ORB_CLI), registry)
    assert reg.state == "ok", reg.failed_step
    assert reg.name == "orb_breakout"
    assert reg.language == "python"
    assert any(p["name"] == "stop_ticks" for p in reg.params)
    assert len(reg.steps) == len(STEPS)
    assert all(s["ok"] for s in reg.steps)


def test_a_real_java_jar_registers(registry):
    if not ORB_JAR.exists():
        pytest.skip("orb.jar not built")
    reg = intake(str(ORB_JAR), registry)
    assert reg.state == "ok", reg.failed_step
    assert reg.language == "java" and reg.name == "orb_breakout"


def test_nothing_is_copied(registry, tmp_path):
    reg = intake(str(ORB_CLI), registry)
    assert reg.source == str(ORB_CLI), "the strategy must stay where it lives"
    assert not list(tmp_path.glob("*.py")), "intake copied files it should not have"


def test_a_dropped_path_may_be_quoted(registry):
    """Dragging onto a terminal pastes the path already quoted."""
    reg = intake(f'"{ORB_CLI}"', registry)
    assert reg.state == "ok"


# ---- failure stays visible

def test_a_missing_path_fails_loudly_and_stays_registered(registry):
    reg = intake(r"C:\definitely\not\here\strategy.py", registry)
    assert reg.state == "failed"
    assert reg.failed_step == "resolve the path"
    assert "does not exist" in reg.steps[-1]["detail"]
    assert reg.source in registry.items, "a failed drop must stay visible"


def test_a_strategy_that_cannot_describe_fails_with_its_stderr(registry, tmp_path):
    bad = drop(tmp_path, "strategy.py", '''
        import sys
        print("no schema here", file=sys.stderr)
        raise SystemExit(4)
    ''')
    reg = intake(str(bad), registry)
    assert reg.state == "failed"
    assert reg.failed_step == "run --describe"
    step = reg.steps[-1]
    assert step["returncode"] == 4
    assert any("no schema here" in line for line in step["stderr"])
    assert step["command"], "the exact command must be recorded"


def test_an_ambiguous_folder_fails_rather_than_picking_one(registry, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    for name in ("alpha.py", "beta.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    reg = intake(str(tmp_path), registry)
    assert reg.state == "failed" and reg.failed_step == "find the entry point"
    assert "alpha.py" in reg.steps[-1]["detail"]


def test_a_failing_build_reports_the_command_and_output(registry, tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n\texit 7\n", encoding="utf-8")
    (tmp_path / "main.exe").write_text("", encoding="utf-8")
    reg = intake(str(tmp_path), registry)
    # Without make or cmake installed this cannot build; either way it must not
    # claim success, and it must say which step stopped it.
    assert reg.state == "failed"
    assert reg.failed_step in ("build it", "run --describe")


# ---- staleness

def test_a_changed_source_is_marked_stale(registry, tmp_path):
    src = drop(tmp_path, "strategy.py", f'''
        import json, sys
        if "--describe" in sys.argv:
            print(json.dumps({{"proto": 1, "name": "s", "version": "1", "params": []}}))
    ''')
    reg = intake(str(src), registry)
    assert reg.state == "ok" and not reg.stale
    import os, time
    time.sleep(0.01)
    os.utime(src, (time.time() + 10, time.time() + 10))
    assert reg.stale, "editing the source must mark the strategy stale"


def test_the_registry_round_trips(registry, tmp_path):
    intake(str(ORB_CLI), registry)
    reloaded = Registry(registry.path)
    assert str(ORB_CLI) in reloaded.items
    assert reloaded.items[str(ORB_CLI)].name == "orb_breakout"
    assert json.loads(registry.path.read_text(encoding="utf-8"))
