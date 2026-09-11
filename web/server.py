"""The engine, over HTTP, on your own machine.

The browser UI is a static site and can be hosted anywhere, including Vercel.
It cannot host the engine: a single raw export here is 169 MB against a 4.5 MB
serverless request limit, the cached series is 61 MB of memory-mapped Parquet,
and a Monte Carlo pass is about six minutes spread over fourteen worker
processes against a sixty second ceiling. So the pages are served from the
internet and the work happens here, on the machine that has the data and the
cores.

This is a stdlib server on purpose. It adds no dependency to install.bat, and
the whole point of a local companion is that it starts without ceremony.

Nothing here computes anything itself -- every endpoint calls the same engine
the terminal app calls, so the two cannot drift apart.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = 8765
PUBLIC = Path(__file__).resolve().parent / "public"


# --------------------------------------------------------------------- jobs

class Job:
    """One long-running piece of work, polled by the browser.

    A backtest outlives any sane HTTP request, so the browser starts a job and
    then asks how it is going. Failure is a state of the job with the error
    text attached -- never a silent stall, and never a partial result dressed
    up as a whole one.
    """

    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.state = "running"          # running | done | failed | cancelled
        self.done = 0
        self.total = 0
        self.log: list[str] = []
        self.error = ""
        self.result = None
        self.started = time.time()
        self.cancelled = False

    def line(self, text: str) -> None:
        self.log.append(text)

    def progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "state": self.state,
            "done": self.done, "total": self.total, "log": self.log[-200:],
            "error": self.error, "elapsed": round(time.time() - self.started, 1),
        }


JOBS: dict[str, Job] = {}
CURRENT: dict[str, str] = {}            # kind -> job id, so the UI can reattach


def start_job(kind: str, work) -> Job:
    job = Job(kind)
    JOBS[job.id] = job
    CURRENT[kind] = job.id

    def runner():
        try:
            job.result = work(job)
            job.state = "cancelled" if job.cancelled else "done"
        except Exception as exc:
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.line("FAILED: " + job.error)
            # The error is already on the job, which is what the browser reads.
            # The traceback is for whoever is looking at the engine window, so
            # it is printed only when there is one -- under pytest it is noise.
            if sys.stderr.isatty():
                traceback.print_exc()

    threading.Thread(target=runner, daemon=True).start()
    return job


# ------------------------------------------------------------------ engine

def app_state():
    from tui.state import AppState
    return AppState.load(ROOT / "config.json")


def data_listing() -> dict:
    state = app_state()
    raw_dir = ROOT / state.data_dir
    cache_dir = ROOT / state.cache_dir
    raw = []
    if raw_dir.exists():
        for p in sorted(raw_dir.glob("*")):
            if p.suffix.lower() in (".csv", ".parquet", ".txt"):
                raw.append({"name": p.name, "path": str(p),
                            "bytes": p.stat().st_size})
    cached = []
    for meta in sorted(cache_dir.glob("*.meta.json")) if cache_dir.exists() else []:
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue              # a damaged sidecar hides one series, not all
        q = m.get("quality", {})
        cached.append({
            "name": meta.name.replace(".meta.json", ""),
            "symbol": m.get("symbol"),
            "timezone": m.get("tz"),
            "tz_conclusive": m.get("tz_conclusive"),
            "bars": q.get("n_bars"),
            "first": (q.get("first") or "")[:10],
            "last": (q.get("last") or "")[:10],
            "bar_seconds": q.get("bar_seconds"),
            "duplicates": q.get("duplicate_timestamps"),
            "ohlc_violations": q.get("ohlc_violations"),
            "zero_volume": q.get("zero_volume_bars"),
            "source": m.get("source"),
            "parquet": m.get("parquet"),
        })

    # The sidecar records the source path as it was given at import time, which
    # is relative to the project root. Comparing raw absolute paths against it
    # marked every already-imported file as new.
    imported = set()
    for c in cached:
        src = c.get("source")
        if src:
            imported.add(Path(src).name)
    for r in raw:
        r["imported"] = Path(r["path"]).name in imported
    return {"raw": raw, "cached": cached,
            "data_dir": str(raw_dir), "cache_dir": str(cache_dir)}


def strategy_listing() -> dict:
    from dataclasses import asdict

    from engine.adapters.intake import Registry

    reg = Registry(ROOT / "strategies" / "registry.json")
    return {"items": [asdict(v) for v in reg.items.values()]}


def history_listing(limit: int = 50) -> dict:
    from dataclasses import asdict

    from engine.store import runs as store

    recs = store.recent(limit=limit, db=ROOT / "runs" / "history.sqlite")
    out = []
    for r in recs:
        d = asdict(r)
        d["short_id"] = r.short_id
        d["folds"] = None               # the blob is not needed for a list row
        d.pop("folds_json", None)
        d.pop("grid_json", None)
        d.pop("warnings_json", None)
        out.append(d)
    return {"items": out}


def results_payload() -> dict:
    from engine.analysis.view import summarise

    path = ROOT / "runs" / "last_oos_trades.json"
    if not path.exists():
        raise FileNotFoundError(
            "no completed run on disk yet -- start one from the Test screen")
    return summarise(str(path))


def import_work(job: Job, path: str):
    from engine.data.loader import ingest
    from engine.data.sniff import MappingStore

    state = app_state()
    job.line("reading " + Path(path).name)
    store = MappingStore(ROOT / state.cache_dir / "mappings.json")
    result = ingest(Path(path), str(ROOT / state.cache_dir), mapping_store=store)
    for ln in result.render().splitlines():
        job.line(ln)
    return {"parquet": str(result.parquet)}


def intake_work(job: Job, path: str):
    from dataclasses import asdict

    from engine.adapters.intake import Registry, intake

    reg = Registry(ROOT / "strategies" / "registry.json")
    steps: list[dict] = []

    def on_step(step):
        steps.append({"step": step.step, "ok": step.ok, "detail": step.detail,
                      "command": step.command, "returncode": step.returncode,
                      "stderr": step.stderr})
        job.line(("[OK] " if step.ok else "[!!] ") + step.step + "  " + step.detail)
        job.progress(len(steps), 8)

    reg_result = intake(path, reg, on_step=on_step)
    return {"registration": asdict(reg_result), "steps": steps}


def walkforward_work(job: Job):
    from engine.core.simulator import ExecConfig
    from engine.data.annotate import RTH_CLOSE_MIN
    from engine.walkforward.runner import build_grid, run_walkforward
    from engine.walkforward.splitter import WalkForwardConfig
    from strategies.reference import orb

    state = app_state()
    t = state.test
    parquet = str(ROOT / state.parquet())
    if not Path(parquet).exists():
        raise FileNotFoundError(
            "no cached series for " + t.instrument + " " + str(t.bar_minutes)
            + "m " + t.session + " -- import it on the Data screen first")

    grid = build_grid(orb.DESCRIBE["params"], overrides=t.grid)
    job.line(f"{len(grid):,} parameter combinations")
    wf = WalkForwardConfig(mode=t.mode, is_sessions=t.is_sessions,
                           oos_sessions=t.oos_sessions,
                           step_sessions=t.step_sessions,
                           min_trades_per_fold=t.min_trades)
    result = run_walkforward(
        parquet, t.strategy, state.instrument(), grid, wf_cfg=wf,
        exec_cfg=ExecConfig(slippage_ticks=state.slippage_ticks,
                            starting_equity=state.starting_equity),
        objective=t.objective, flatten_minute=RTH_CLOSE_MIN,
        progress=job.progress, cancel=lambda: job.cancelled,
    )

    from engine.store import runs as store

    rec = store.save(result, state, db=ROOT / "runs" / "history.sqlite")
    job.line("saved to history as " + rec.short_id)
    m = result.metrics
    job.line(f"{len(result.folds)} folds, {m.n_trades:,} OOS trades")
    for w in result.warnings:
        job.line("! " + w)
    _write_last_run(result)
    return {"run_id": rec.short_id, "n_trades": m.n_trades,
            "net_pnl": m.net_pnl, "warnings": result.warnings}


def _write_last_run(result) -> None:
    """The results screen reads this file; a run that does not write it is
    invisible afterwards."""
    st = result.stitched
    payload = {
        "net_pnl": [t.net_pnl for t in st.trades],
        "entry_ts": [t.entry_ts for t in st.trades],
        "exit_ts": [t.exit_ts for t in st.trades],
        "folds": [
            {"index": f.index, "params": f.params, "disqualified": f.disqualified,
             "is_mar": f.is_metrics.mar, "oos_mar": f.oos_metrics.mar,
             "oos_net": f.oos_metrics.net_pnl, "oos_n": f.oos_metrics.n_trades,
             "oos_start_ts": (f.oos_bar_ts[0] if f.oos_bar_ts else 0)}
            for f in result.folds
        ],
        "stability": result.parameter_stability(),
    }
    out = ROOT / "runs" / "last_oos_trades.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")


# ------------------------------------------------------------------- routes

def route(method: str, path: str, body: dict) -> tuple[int, dict]:
    if path == "/api/health":
        return 200, {"ok": True, "app": "s3-lab", "root": str(ROOT),
                     "python": sys.version.split()[0]}

    if path == "/api/state" and method == "GET":
        from dataclasses import asdict
        return 200, asdict(app_state())

    if path == "/api/state" and method == "POST":
        state = app_state()
        for key, value in (body or {}).items():
            if key == "test":
                for k, v in value.items():
                    if hasattr(state.test, k):
                        setattr(state.test, k, v)
            elif hasattr(state, key):
                setattr(state, key, value)
        state.save(ROOT / "config.json")
        from dataclasses import asdict
        return 200, asdict(state)

    if path == "/api/data":
        return 200, data_listing()

    if path == "/api/data/import" and method == "POST":
        target = (body or {}).get("path", "")
        if not target:
            return 400, {"error": "no path given"}
        job = start_job("import", lambda j: import_work(j, target))
        return 200, job.as_dict()

    if path == "/api/strategies" and method == "GET":
        return 200, strategy_listing()

    if path == "/api/strategies" and method == "POST":
        target = (body or {}).get("path", "")
        if not target:
            return 400, {"error": "no path given"}
        job = start_job("intake", lambda j: intake_work(j, target))
        return 200, job.as_dict()

    if path == "/api/run/start" and method == "POST":
        live = JOBS.get(CURRENT.get("walkforward", ""))
        if live is not None and live.state == "running":
            return 409, {"error": "a run is already in progress",
                         "job": live.as_dict()}
        job = start_job("walkforward", walkforward_work)
        return 200, job.as_dict()

    if path == "/api/run/cancel" and method == "POST":
        live = JOBS.get(CURRENT.get("walkforward", ""))
        if live is None or live.state != "running":
            return 404, {"error": "nothing is running"}
        live.cancelled = True
        live.line("cancel requested — finishing the fold in flight")
        return 200, live.as_dict()

    if path.startswith("/api/job/"):
        job = JOBS.get(path.rsplit("/", 1)[-1])
        if job is None:
            return 404, {"error": "unknown job"}
        payload = job.as_dict()
        payload["result"] = job.result
        return 200, payload

    if path == "/api/jobs":
        return 200, {"current": {k: JOBS[v].as_dict() for k, v in CURRENT.items()
                                 if v in JOBS}}

    if path == "/api/results":
        try:
            return 200, results_payload()
        except FileNotFoundError as exc:
            return 404, {"error": str(exc)}

    if path == "/api/history":
        return 200, history_listing()

    return 404, {"error": "no such endpoint: " + path}


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "S3Lab"

    def _cors(self) -> None:
        """Allow the hosted UI to reach this machine.

        A page on https://<you>.vercel.app fetching http://127.0.0.1 is not
        mixed content -- loopback counts as a trustworthy origin -- but Chrome
        does treat it as a private-network request and requires the preflight
        to be answered, including the private-network header. Without that
        line the site simply never connects and the failure is invisible.
        """
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            code, payload = route("GET", self.path.split("?")[0], {})
            self._send(code, payload)
            return
        self._serve_static()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "body was not JSON"})
            return
        code, payload = route("POST", self.path.split("?")[0], body)
        self._send(code, payload)

    def _serve_static(self) -> None:
        """Serve the same UI locally, so the app works with no internet."""
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        target = (PUBLIC / rel).resolve()
        if not str(target).startswith(str(PUBLIC.resolve())) or not target.is_file():
            target = PUBLIC / "index.html"
        if not target.is_file():
            self._send(404, {"error": "UI not built"})
            return
        kind = {".html": "text/html", ".js": "text/javascript",
                ".css": "text/css", ".json": "application/json",
                ".svg": "image/svg+xml"}.get(target.suffix, "text/plain")
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):       # quiet; the UI is the interface
        pass


def serve(port: int = PORT, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print()
    print("  S3 LAB engine")
    print("  " + "-" * 52)
    print(f"  local UI    {url}")
    print(f"  API         {url}api/health")
    print("  data        " + str(ROOT / "data" / "cache"))
    print("  " + "-" * 52)
    print("  Leave this window open while you use the app.")
    print("  Ctrl+C to stop.")
    print()
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("  stopped")


if __name__ == "__main__":
    # The walk-forward spawns a worker pool. On Windows that re-imports this
    # module in every child, and without this guard the server would start
    # recursively until the machine is saturated. It has happened once.
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
