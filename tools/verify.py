"""Final verification. Every claim checked against the repo, not asserted."""
import subprocess, sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK, FAIL, WARN = "  OK  ", " FAIL ", " WARN "
results = []

def check(name, fn, required=True):
    try:
        detail = fn() or ""
        results.append((OK, name, str(detail)))
    except Exception as e:
        results.append((FAIL if required else WARN, name, f"{type(e).__name__}: {e}"))

def _palette():
    from tui.theme import PALETTE
    hexes = set()
    for p in list((ROOT/"tui").rglob("*.py")) + list((ROOT/"engine"/"report").rglob("*.py")):
        if "__pycache__" in p.parts: continue
        hexes |= set(m.upper() for m in re.findall(r"#[0-9a-fA-F]{6}\b", p.read_text(encoding="utf-8")))
    bad = hexes - {c.upper() for c in PALETTE}
    if bad: raise AssertionError(f"off-palette: {sorted(bad)}")
    return f"{len(hexes)} literals, all on the list"

def _no_digits():
    hits = [str(p.relative_to(ROOT)) for p in (ROOT/"tui").rglob("*.py")
            if "__pycache__" not in p.parts and re.search(r"\bDigits\b", p.read_text(encoding="utf-8"))]
    if hits: raise AssertionError(f"Digits used in {hits}")
    return "Digits appears nowhere"

def _screens():
    from tui.screens.base import SECTIONS
    import tui.screens.data, tui.screens.strategies, tui.screens.test
    import tui.screens.run, tui.screens.results, tui.screens.settings, tui.screens.history
    return f"{len(SECTIONS)} sections, 7 screens import"

def _navigation():
    """The headline promise: every section reachable from every other, by one key."""
    out = subprocess.run([str(ROOT/".venv/Scripts/python.exe"), "-m", "pytest",
                          "tests/test_navigation.py", "-q"], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0: raise AssertionError(out.stdout[-300:])
    return "every section reachable from every other, one keypress"

def _conformance():
    out = subprocess.run([str(ROOT/".venv/Scripts/python.exe"), "-m", "pytest",
                          "tests/test_conformance.py", "-q"], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0: raise AssertionError(out.stdout[-300:])
    return "in-process py, py over pipe, java -- identical trade logs"

def _lookahead():
    out = subprocess.run([str(ROOT/".venv/Scripts/python.exe"), "-m", "pytest",
                          "tests/test_engine.py", "-q", "-k", "look or bar or batch"],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    if out.returncode != 0: raise AssertionError(out.stdout[-300:])
    return "order never fills on the bar it was decided"

def _data():
    from engine.data.loader import load
    ps = sorted((ROOT/"data"/"cache").glob("*.parquet"))
    if not ps: raise AssertionError("no cached series")
    n = sum(len(load(str(p), columns=("ts",))) for p in ps)
    return f"{len(ps)} series, {n:,} bars"

def _costs():
    """No commission model anywhere, checked structurally rather than by grep.

    A text search would trip over the comments that explain the absence. These
    are the actual attachment points a cost model would have to come back
    through.
    """
    import inspect
    from engine.core.kernel import simulate
    from engine.core.types import Trade
    from engine.data.canonical import DEFAULT_INSTRUMENTS
    from engine.store.runs import RunRecord
    from tui.state import AppState

    gone = {
        "Instrument": (next(iter(DEFAULT_INSTRUMENTS.values())),
                       ("commission_per_side", "fees_per_side", "cost_per_side",
                        "costs_confirmed")),
        "AppState": (AppState(), ("costs",)),
    }
    for owner, (obj, names) in gone.items():
        live = [n for n in names if hasattr(obj, n)]
        if live: raise AssertionError(owner + " still carries " + ", ".join(live))

    for owner, names in (("Trade", ("costs", "gross_pnl")),
                         ("RunRecord", ("commission", "fees", "costs_confirmed"))):
        cls = Trade if owner == "Trade" else RunRecord
        live = [n for n in names if n in cls.__dataclass_fields__]
        if live: raise AssertionError(owner + " still has field " + ", ".join(live))

    py = getattr(simulate, "py_func", simulate)
    if "cost" in inspect.signature(py).parameters:
        raise AssertionError("the kernel still takes a cost argument")
    return "slippage only, no commission modelled anywhere"

def _web():
    """The browser UI exists, is deployable, and agrees with the terminal."""
    out = subprocess.run([str(ROOT/".venv/Scripts/python.exe"), "-m", "pytest",
                          "tests/test_web_server.py", "tests/test_web_palette.py", "-q"],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    if out.returncode != 0: raise AssertionError(out.stdout[-300:])
    pub = ROOT / "web" / "public"
    missing = [f for f in ("index.html", "app.js", "app.css", "vercel.json")
               if not (pub / f).exists()]
    if missing: raise AssertionError(f"web/public missing {missing}")
    kb = sum(f.stat().st_size for f in pub.iterdir() if f.is_file()) // 1024
    return f"web/public deployable, {kb} kB, same numbers as the terminal"

def _history():
    from engine.store import runs as store
    recs = store.recent(db=ROOT/"runs"/"history.sqlite")
    return f"{len(recs)} runs recorded"

def _docs():
    missing = [f for f in ("SPEC.md","PROTOCOL.md","RUNBOOK.md") if not (ROOT/f).exists()]
    if missing: raise AssertionError(f"missing {missing}")
    return "SPEC, PROTOCOL, RUNBOOK present"

def _shortcut():
    import os
    p = Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "Backtest Lab.lnk"
    alt = Path(os.path.expanduser("~")) / "Desktop" / "Backtest Lab.lnk"
    if not p.exists() and not alt.exists(): raise AssertionError("no Desktop shortcut")
    return "Backtest Lab on the Desktop"


print()
print("  S3 LAB  final verification")
print("  " + "-"*66)
check("closed palette", _palette)
check("no LCD numerals", _no_digits)
check("all screens", _screens)
check("nothing is a dead end", _navigation)
check("cross-language conformance", _conformance)
check("look-ahead defence", _lookahead)
check("real data cached", _data)
check("no invented costs", _costs)
check("browser UI", _web)
check("run history", _history)
check("documentation", _docs)
check("desktop shortcut", _shortcut)
for s,n,d in results:
    print(f"  [{s}] {n:<28} {d}")
print("  " + "-"*66)
f = [r for r in results if r[0]==FAIL]; w = [r for r in results if r[0]==WARN]
print(f"  {len(results)-len(f)-len(w)} passed, {len(w)} warnings, {len(f)} failed")
print()
sys.exit(1 if f else 0)
