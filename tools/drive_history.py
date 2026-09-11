"""Run twice from the UI, then browse and compare, by keystroke only."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.app import S3Lab


async def run_once(pilot, app, label):
    await pilot.press("t")
    await pilot.pause(); await asyncio.sleep(0.3)
    await pilot.press("enter")
    await pilot.pause()
    for _ in range(400):
        await asyncio.sleep(0.5)
        if app.run_state in ("DONE", "FAILED"):
            break
    print(f"  {label}: {app.run_state} run_id={app.run_id}")


async def main():
    app = S3Lab()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause(); await asyncio.sleep(0.3)

        # Small grid so two runs finish quickly.
        await pilot.press("t"); await pilot.pause()
        for _ in range(7):
            await pilot.press("down")
        await pilot.press("left")
        await pilot.press("down"); await pilot.press("left")
        await pilot.press("down"); await pilot.press("left")
        await pilot.pause()
        print("  grid:", app.state.test.grid_size, "combos")

        await run_once(pilot, app, "run 1")

        # Change slippage so the second run is genuinely different.
        await pilot.press("m"); await pilot.pause(); await asyncio.sleep(0.2)
        for _ in range(2):
            await pilot.press("down")
        await pilot.press("right")
        await pilot.pause()
        print(f"  slippage now {app.state.slippage_ticks}")

        await run_once(pilot, app, "run 2")

        await pilot.press("h")
        await pilot.pause(); await asyncio.sleep(0.6)
        scr = app.screen
        print(f"  history  : {type(scr).__name__}, {len(scr.records)} runs")

        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.pause(); await asyncio.sleep(0.4)
        print(f"  marked   : {len(scr.marked)}")
        app.save_screenshot("runs/scr_HistoryScreen.svg")

        from engine.store import runs as store
        if len(scr.marked) == 2:
            a, b = store.get(scr.marked[0]), store.get(scr.marked[1])
            print(store.compare(a, b).render())

        await pilot.press("x")
        await pilot.pause(); await asyncio.sleep(0.5)
        csvs = sorted(Path("runs").glob("trades_*.csv"))
        print(f"  exported : {[c.name for c in csvs]}")
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
