"""The HTTP layer over the engine.

The browser UI is only as honest as this. Two things matter and are tested
here: that it never invents a number, and that a failure arrives as a failure
rather than as an empty result the page would draw as zero.

The routes are tested directly rather than over a socket. What is being checked
is the contract, not http.server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web import server

ROOT = Path(__file__).resolve().parents[1]


def get(path, body=None):
    return server.route("GET" if body is None else "POST", path, body or {})


def test_health_reports_the_root_it_is_actually_serving():
    """The root is whatever the checkout is called.

    This asserted the folder was named "s3-lab", which was true on exactly one
    computer and false on every clone. What matters is that the path it reports
    is the project it is running from, not what someone named the directory.
    """
    code, payload = get("/api/health")
    assert code == 200 and payload["ok"] is True
    root = Path(payload["root"])
    assert (root / "engine").is_dir() and (root / "web" / "server.py").is_file()


def test_unknown_endpoint_is_a_404_naming_the_path():
    code, payload = get("/api/nonsense")
    assert code == 404 and "nonsense" in payload["error"]


def test_state_round_trips_without_losing_the_rest_of_the_config():
    code, before = get("/api/state")
    assert code == 200
    original = before["test"]["min_trades"]
    try:
        code, after = get("/api/state", {"test": {"min_trades": original + 1}})
        assert code == 200
        assert after["test"]["min_trades"] == original + 1
        # everything else must survive a partial update
        assert after["test"]["instrument"] == before["test"]["instrument"]
        assert after["slippage_ticks"] == before["slippage_ticks"]
    finally:
        get("/api/state", {"test": {"min_trades": original}})


def test_state_has_no_costs_block():
    """Commission was removed from the engine; the API must not resurrect it."""
    _, payload = get("/api/state")
    assert "costs" not in payload
    assert not any("commission" in k for k in payload)


def test_data_listing_marks_already_imported_files():
    code, payload = get("/api/data")
    assert code == 200
    assert isinstance(payload["raw"], list) and isinstance(payload["cached"], list)
    for row in payload["raw"]:
        assert isinstance(row["imported"], bool)
    for row in payload["cached"]:
        # A cached series without a bar count would render as a blank cell and
        # look like a UI bug rather than a data one.
        assert row["bars"] is None or row["bars"] > 0


def test_starting_a_job_without_a_path_is_refused_not_queued():
    for endpoint in ("/api/data/import", "/api/strategies"):
        code, payload = get(endpoint, {"nothing": True})
        assert code == 400, endpoint
        assert "path" in payload["error"]


def test_cancelling_nothing_is_a_404_not_a_pretend_success():
    server.CURRENT.pop("walkforward", None)
    code, payload = get("/api/run/cancel", {"_": 1})
    assert code == 404 and "nothing is running" in payload["error"]


def test_results_refuse_to_invent_a_run():
    """With no run on disk the endpoint must fail, not return zeros."""
    real = ROOT / "runs" / "last_oos_trades.json"
    if real.exists():
        code, payload = get("/api/results")
        assert code == 200
        # Sanity: these come from the engine, not from the route.
        assert payload["n_trades"] > 0
        assert "breakeven_per_side" in payload
        assert len(payload["equity"]) == payload["n_trades"]
    else:
        code, payload = get("/api/results")
        assert code == 404 and "no completed run" in payload["error"]


def test_results_and_the_terminal_screen_agree():
    """One engine, two front ends, one set of numbers."""
    if not (ROOT / "runs" / "last_oos_trades.json").exists():
        pytest.skip("no run on disk to compare")
    from tui.screens.results import load_run

    _, web = get("/api/results")
    term = load_run()
    rail = dict(term["rail"]["rows"])
    assert rail["OOS trades"] == format(web["n_trades"], ",")
    assert rail["MAR"] == format(web["mar"], ".2f")
    assert term["net_oos"] == format(web["return_pct"], "+.1f") + "%"


def test_job_ids_are_unknown_until_they_exist():
    code, payload = get("/api/job/deadbeef")
    assert code == 404 and payload["error"] == "unknown job"


def test_a_failing_job_records_the_error_rather_than_finishing_quietly():
    def explode(job):
        raise RuntimeError("the disk caught fire")

    job = server.start_job("test", explode)
    for _ in range(200):
        if job.state != "running":
            break
        import time
        time.sleep(0.01)
    assert job.state == "failed"
    assert "the disk caught fire" in job.error
    assert any("FAILED" in line for line in job.log)


def test_static_ui_is_served_from_the_public_folder():
    assert (server.PUBLIC / "index.html").is_file()
    assert (server.PUBLIC / "app.js").is_file()
    assert (server.PUBLIC / "app.css").is_file()
    # The deploy target is this folder and nothing else.
    assert (server.PUBLIC / "vercel.json").is_file()


def test_the_server_guards_against_respawning_itself():
    """The walk-forward spawns a process pool; on Windows every child
    re-imports the module. Without the guard this file starts servers until the
    machine is saturated, which has happened once already."""
    src = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src
    body = src[src.index('if __name__ == "__main__":'):]
    assert "serve(" in body
    assert "serve(" not in src[:src.index('if __name__ == "__main__":')].replace(
        "def serve(", "")
