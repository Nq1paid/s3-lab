"""Drive the app by keystrokes only -- no function calls into the engine.

If a feature cannot be reached this way it is not done, so this is the check
that the TUI actually drives the thing rather than decorating it.
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.app import S3Lab


async def main():
    app = S3Lab()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause(); await asyncio.sleep(0.3)
        print("opened on:", type(app.screen).__name__)

        # Narrow the grid from the UI so the run is quick: three rows down to
        # OR MINUTES presets, then pick the smallest preset on each grid row.
        for _ in range(7):
            await pilot.press("down")
        await pilot.press("left")            # or_minutes -> smallest preset
        await pilot.press("down"); await pilot.press("left")   # stop_ticks
        await pilot.press("down"); await pilot.press("left")   # target_r
        await pilot.pause()
        print("grid now:", app.state.test.grid, "->", app.state.test.grid_size, "combos")

        # Fewer, wider folds so this finishes fast.
        for _ in range(5):
            await pilot.press("up")
        for _ in range(6):
            await pilot.press("right")       # is_sessions up
        await pilot.pause()
        print("is_sessions:", app.state.test.is_sessions)

        app.save_screenshot("runs/screen_test.svg")

        print("starting run via ENTER ...")
        await pilot.press("enter")
        await pilot.pause(); await asyncio.sleep(0.6)
        print("now on:", type(app.screen).__name__, "state:", app.run_state)
        app.save_screenshot("runs/screen_run.svg")

        for _ in range(240):                 # wait for the worker
            await asyncio.sleep(0.5)
            if app.run_state in ("DONE", "FAILED"):
                break
        print("run finished:", app.run_state)
        await pilot.pause(); await asyncio.sleep(0.3)
        app.save_screenshot("runs/screen_run_done.svg")

        if app.last_result is not None:
            m = app.last_result.metrics
            print(f"  folds {len(app.last_result.folds)}  trades {m.n_trades:,}  "
                  f"net {m.net_pnl:,.0f}  elapsed {app.last_result.elapsed_s}s")

        await pilot.press("e")               # results
        await pilot.pause(); await asyncio.sleep(1.5)
        print("now on:", type(app.screen).__name__)
        app.save_screenshot("runs/screen_results.svg")

        await pilot.press("t")               # back to test
        await pilot.pause()
        print("back on:", type(app.screen).__name__)
    print("OK")

# Windows spawns worker processes by RE-IMPORTING this module. Without the
# guard, every worker starts the whole app again and the run never finishes.
if __name__ == "__main__":
    asyncio.run(main())
