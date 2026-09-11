import asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.app import S3Lab

WINDOW_DOTS = re.compile(r'<circle[^>]*fill="#(?:ff5f57|febc2e|28c840)"[^>]*/>', re.I)


def clean(path: str) -> None:
    p = Path(path)
    p.write_text(WINDOW_DOTS.sub("", p.read_text(encoding="utf-8")), encoding="utf-8")


async def main():
    app = S3Lab()
    async with app.run_test(size=(150, 52)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.4)
        app.save_screenshot("runs/results_curve.svg")
        await pilot.press("right", "right")
        await pilot.pause()
        await asyncio.sleep(0.3)
        app.save_screenshot("runs/results_diverge.svg")
        # app.screen under run_test is a TestScreen wrapper, not our screen
    for f in ("runs/results_curve.svg", "runs/results_diverge.svg"):
        clean(f)
    print("saved and de-chromed both screenshots")

asyncio.run(main())
