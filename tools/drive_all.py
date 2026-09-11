"""Visit every screen by keystroke and exercise its real actions."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.app import S3Lab

ORB_CLI = str(Path(__file__).resolve().parents[1] / "strategies" / "reference" / "orb_cli.py")


async def main():
    app = S3Lab()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause(); await asyncio.sleep(0.3)

        for key, expect in (("d", "DataScreen"), ("s", "StrategiesScreen"),
                            ("m", "SettingsScreen"), ("t", "TestScreen"),
                            ("r", "RunScreen")):
            # Strategies focuses its drop field, which correctly swallows
            # letters; ESC is the documented way back to navigation.
            await pilot.press("escape")
            await pilot.press(key)
            await pilot.pause(); await asyncio.sleep(0.25)
            got = type(app.screen).__name__
            print(f"  {key} -> {got:<20} {'OK' if got == expect else 'MISMATCH ' + expect}")
            app.save_screenshot(f"runs/scr_{expect}.svg")

        # Settings: change slippage from the keyboard only, then put it back.
        await pilot.press("m"); await pilot.pause()
        before = app.state.slippage_ticks
        await pilot.press("right"); await pilot.pause()
        print(f"  slippage : {before:g} -> {app.state.slippage_ticks:g} ticks")
        await pilot.press("left"); await pilot.pause()
        app.save_screenshot("runs/scr_SettingsScreen.svg")

        # Strategies: register a real strategy through the drop field.
        await pilot.press("s"); await pilot.pause(); await asyncio.sleep(0.4)
        inp = app.screen.query_one("#droppath")
        inp.value = ORB_CLI
        await pilot.press("enter")
        for _ in range(60):
            await asyncio.sleep(0.25)
            if not app.screen._busy:
                break
        reg = app.screen._last
        print(f"  intake   : state={reg.state} name={reg.name} lang={reg.language} "
              f"steps={len(reg.steps)}")
        await pilot.pause(); await asyncio.sleep(0.3)
        app.save_screenshot("runs/scr_StrategiesScreen.svg")

        # Data: rescan finds the raw files and the cached series.
        await pilot.press("escape")
        await pilot.press("d"); await pilot.pause(); await asyncio.sleep(0.3)
        print(f"  data     : {len(app.screen.raw)} raw, {len(app.screen.cached)} cached")
        app.save_screenshot("runs/scr_DataScreen.svg")
        app.state.save()
        print(f"  slippage : left at {app.state.slippage_ticks:g} ticks")
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
