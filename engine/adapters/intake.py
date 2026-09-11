"""Strategy intake -- drag a folder in, nothing else.

Eight steps, run end to end with no further prompting. On any failure the
strategy stays registered in a FAILED state carrying the step that failed, the
exact command, the exit code and the stderr tail. A silently rejected folder is
unacceptable: the user dropped something and is owed an answer either way.

Nothing is ever copied. The strategy stays where the user keeps it and the
registry stores the path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from .line_protocol import StrategyError, describe

REGISTRY = Path("strategies/registry.json")


class Step(str, Enum):
    RESOLVE = "resolve the path"
    DETECT = "detect the language"
    ENTRY = "find the entry point"
    BUILD = "build it"
    DESCRIBE = "run --describe"
    CONFORM = "run the conformance harness"
    REGISTER = "register it"
    WATCH = "watch the folder"


STEPS = tuple(Step)


class Language(str, Enum):
    """What the engine needs to know to build and launch a strategy.

    NATIVE means "a compiled binary we just execute" -- it is deliberately not
    named after a language, because the launch command is the same whatever
    produced the executable. C++ is out of scope for this build; a native
    strategy still registers and runs, it just has no reference implementation
    shipped with it.
    """

    PYTHON = "python"
    JAVA = "java"
    NATIVE = "native"
    UNKNOWN = "unknown"


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""
    command: str = ""
    returncode: int | None = None
    stderr: list[str] = field(default_factory=list)


@dataclass
class Registration:
    name: str
    version: str
    language: str
    source: str
    entry: str
    command: list[str]
    params: list[dict]
    build_command: str = ""
    state: str = "ok"                 # ok | failed
    failed_step: str = ""
    steps: list[dict] = field(default_factory=list)
    source_mtime: float = 0.0
    registered_at: float = 0.0

    @property
    def stale(self) -> bool:
        """True when the source changed after registration."""
        try:
            return newest_mtime(Path(self.source)) > self.source_mtime + 1e-6
        except OSError:
            return False


def newest_mtime(path: Path) -> float:
    """Most recent mtime in a file or tree, for staleness detection."""
    if path.is_file():
        return path.stat().st_mtime
    latest = 0.0
    for p in path.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts:
            latest = max(latest, p.stat().st_mtime)
    return latest


def detect_language(path: Path) -> tuple[Language, str]:
    """Language plus the marker that decided it, so the UI can show its reasoning."""
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".py":
            return Language.PYTHON, path.name
        if suffix == ".jar":
            return Language.JAVA, path.name
        if suffix == ".exe":
            return Language.NATIVE, path.name
        return Language.UNKNOWN, f"unrecognised file type {suffix!r}"

    markers = (
        (Language.PYTHON, ("pyproject.toml", "requirements.txt", "setup.py")),
        (Language.JAVA, ("pom.xml", "build.gradle", "build.gradle.kts")),
        (Language.NATIVE, ("CMakeLists.txt", "Makefile", "makefile")),
    )
    for lang, names in markers:
        for name in names:
            if (path / name).exists():
                return lang, name
    for lang, pattern in ((Language.JAVA, "*.jar"), (Language.PYTHON, "*.py"),
                          (Language.NATIVE, "*.cpp")):
        hits = sorted(path.glob(pattern))
        if hits:
            return lang, hits[0].name
    return Language.UNKNOWN, "no language marker found"


def find_entry(path: Path, lang: Language) -> tuple[Path | None, list[Path]]:
    """Entry point by convention. Returns (entry, candidates).

    Two or more candidates is never resolved by guessing -- the caller asks
    once, with the list. Picking silently is how the wrong file gets backtested
    for a week.
    """
    if path.is_file():
        return path, [path]

    if lang is Language.PYTHON:
        preferred = ("main.py", "strategy.py", "run.py")
        for name in preferred:
            if (path / name).exists():
                return path / name, [path / name]
        cands = [p for p in sorted(path.glob("*.py")) if not p.name.startswith("_")]
        return (cands[0] if len(cands) == 1 else None), cands

    if lang is Language.JAVA:
        cands = sorted(path.rglob("*.jar"))
        return (cands[0] if len(cands) == 1 else None), cands

    if lang is Language.NATIVE:
        cands = sorted(path.rglob("*.exe"))
        return (cands[0] if len(cands) == 1 else None), cands

    return None, []


def build_command_for(path: Path, lang: Language) -> list[str] | None:
    if lang is Language.JAVA:
        if (path / "pom.xml").exists() and shutil.which("mvn"):
            return ["mvn", "-q", "package"]
        if (path / "build.gradle").exists() and shutil.which("gradle"):
            return ["gradle", "build"]
        if (path / "build.bat").exists():
            return ["cmd", "/c", "build.bat"]
    if lang is Language.NATIVE:
        if (path / "CMakeLists.txt").exists() and shutil.which("cmake"):
            return ["cmake", "--build", "."]
        if (path / "Makefile").exists() and shutil.which("make"):
            return ["make"]
    return None


def run_command_for(entry: Path, lang: Language) -> list[str]:
    import sys

    if lang is Language.PYTHON:
        return [sys.executable, str(entry)]
    if lang is Language.JAVA:
        return ["java", "-jar", str(entry)]
    return [str(entry)]


class Registry:
    """Registered strategies. Paths only -- nothing is copied."""

    def __init__(self, path: Path = REGISTRY):
        self.path = Path(path)
        self.items: dict[str, Registration] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in raw.items():
                    self.items[key] = Registration(**value)
            except (json.JSONDecodeError, OSError, TypeError):
                self.items = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({k: asdict(v) for k, v in self.items.items()}, indent=2),
            encoding="utf-8")

    def add(self, reg: Registration) -> None:
        self.items[reg.source] = reg
        self.save()


def intake(dropped: str, registry: Registry | None = None,
           conformance=None, on_step=None) -> Registration:
    """Run the eight-step pipeline. Always returns a Registration.

    A failure produces a Registration in the `failed` state rather than an
    exception, because a rejected folder must stay visible with the reason
    attached.
    """
    registry = registry or Registry()
    steps: list[StepResult] = []

    def record(step: Step, ok: bool, detail: str = "", **kw) -> StepResult:
        r = StepResult(step=step.value, ok=ok, detail=detail, **kw)
        steps.append(r)
        if on_step:
            on_step(r)
        return r

    def fail(step: Step, reg_path: str, detail: str, **kw) -> Registration:
        record(step, False, detail, **kw)
        reg = Registration(
            name=Path(reg_path).stem or "unknown", version="0",
            language=Language.UNKNOWN.value, source=reg_path, entry="",
            command=[], params=[], state="failed", failed_step=step.value,
            steps=[asdict(s) for s in steps], registered_at=time.time(),
        )
        registry.add(reg)
        return reg

    # 1 -- resolve
    raw = dropped.strip().strip('"').strip("'")
    path = Path(raw).expanduser()
    if not path.exists():
        return fail(Step.RESOLVE, raw, f"path does not exist: {path}")
    try:
        next(path.iterdir()) if path.is_dir() else path.open("rb").close()
    except (OSError, StopIteration) as exc:
        if isinstance(exc, OSError):
            return fail(Step.RESOLVE, str(path), f"not readable: {exc}")
    record(Step.RESOLVE, True, str(path))

    # 2 -- language
    lang, marker = detect_language(path)
    if lang is Language.UNKNOWN:
        return fail(Step.DETECT, str(path), marker)
    record(Step.DETECT, True, f"{lang.value}  ({marker})")

    # 3 -- entry point
    entry, candidates = find_entry(path, lang)
    if entry is None:
        listing = ", ".join(c.name for c in candidates) or "none found"
        return fail(Step.ENTRY, str(path),
                    f"{len(candidates)} candidates, cannot choose: {listing}")
    record(Step.ENTRY, True, entry.name)

    # 4 -- build
    # A dropped FILE is already built. Building its parent directory would run
    # whatever build script happens to sit next to it, which is not what the
    # user dropped and not theirs to trigger.
    build = build_command_for(path, lang) if path.is_dir() else None
    if build is None:
        record(Step.BUILD, True,
               "single file, nothing to build" if path.is_file() else "nothing to build")
        build_str = ""
    else:
        cwd = path if path.is_dir() else path.parent
        try:
            out = subprocess.run(build, cwd=str(cwd), capture_output=True,
                                 text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return fail(Step.BUILD, str(path), str(exc), command=" ".join(build))
        if out.returncode != 0:
            return fail(Step.BUILD, str(path), "build failed",
                        command=" ".join(build), returncode=out.returncode,
                        stderr=(out.stderr or out.stdout).splitlines())
        build_str = " ".join(build)
        record(Step.BUILD, True, build_str)
        entry, candidates = find_entry(path, lang)
        if entry is None:
            return fail(Step.ENTRY, str(path), "build produced no runnable entry point")

    # 5 -- describe
    command = run_command_for(entry, lang)
    try:
        described = describe(command)
    except StrategyError as exc:
        return fail(Step.DESCRIBE, str(path), str(exc).splitlines()[0],
                    command=" ".join(command), returncode=exc.returncode,
                    stderr=exc.stderr)
    record(Step.DESCRIBE, True,
           f"{described.name} v{described.version}, {len(described.params)} params")

    # 6 -- conformance
    if conformance is None:
        record(Step.CONFORM, True, "skipped (no reference slice configured)")
    else:
        try:
            detail = conformance(command, described)
        except Exception as exc:
            return fail(Step.CONFORM, str(path), f"{type(exc).__name__}: {exc}",
                        command=" ".join(command))
        record(Step.CONFORM, True, detail)

    # 7 -- register
    reg = Registration(
        name=described.name, version=described.version, language=lang.value,
        source=str(path), entry=str(entry), command=command,
        params=described.params, build_command=build_str, state="ok",
        steps=[asdict(s) for s in steps], source_mtime=newest_mtime(path),
        registered_at=time.time(),
    )
    record(Step.REGISTER, True, f"{described.name} -> {path}")
    reg.steps = [asdict(s) for s in steps]
    registry.add(reg)

    # 8 -- watch
    record(Step.WATCH, True, "source mtime recorded; stale rebuilds are offered")
    reg.steps = [asdict(s) for s in steps]
    registry.save()
    return reg
