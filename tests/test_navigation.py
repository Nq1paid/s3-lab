"""Every section is reachable from every other section, by one keypress.

This is the requirement the whole application is built around -- if a feature
cannot be driven from the TUI it is not done -- and it is the requirement with
no natural unit test, so it gets an explicit one.

It exists because it was already broken once. The Strategies screen focused its
drop field on mount, a focused Input correctly swallows every letter, and so
D, T, R, E and M typed themselves into the path box instead of navigating. The
screen looked fine in a screenshot and was a dead end in use.
"""

from __future__ import annotations

import asyncio
import functools

from tui.app import S3Lab
from tui.screens.base import SECTIONS


def sync(fn):
    """Run an async test body without a pytest async plugin.

    Textual's run_test is async, but pytest-asyncio is one more dependency on
    the install path for no benefit here -- these tests are sequential and
    each owns its own event loop.
    """

    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper

#: key -> the screen class name it must land on.
DESTINATIONS = {
    "d": "DataScreen",
    "s": "StrategiesScreen",
    "t": "TestScreen",
    "r": "RunScreen",
    "m": "SettingsScreen",
    "h": "HistoryScreen",
}

SIZE = (140, 45)


def current(app) -> str:
    return type(app.screen).__name__


@sync
async def test_every_section_reaches_every_other_section():
    """From each screen, each section key lands on that section.

    Results is excluded as a destination only because it refuses to open with
    no run loaded, which is itself correct behaviour -- it is covered below.
    """
    app = S3Lab()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        for origin_key, origin_name in DESTINATIONS.items():
            for key, expected in DESTINATIONS.items():
                await pilot.press(origin_key)
                await pilot.pause()
                assert current(app) == origin_name, (
                    "could not get to " + origin_name + " to start from")
                await pilot.press(key)
                await pilot.pause()
                assert current(app) == expected, (
                    origin_name + " + '" + key + "' went to " + current(app)
                    + ", not " + expected)


@sync
async def test_results_key_either_opens_results_or_leaves_you_where_you_are():
    """A missing run is reported, never a blank screen and never a trap."""
    app = S3Lab()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert current(app) in ("ResultsScreen", "TestScreen")
        # still navigable either way
        await pilot.press("d")
        await pilot.pause()
        assert current(app) == "DataScreen"


@sync
async def test_strategies_does_not_swallow_navigation_keys():
    """The specific regression: nothing is focused on the Strategies screen."""
    app = S3Lab()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert current(app) == "StrategiesScreen"
        assert app.focused is None, (
            "a focused widget on Strategies eats every section key")
        await pilot.press("t")
        await pilot.pause()
        assert current(app) == "TestScreen"


@sync
async def test_strategies_enter_opens_the_field_and_escape_leaves_it():
    """Typing a path is opt-in, and there is always a way back out."""
    from textual.widgets import Input

    app = S3Lab()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.focused, Input)
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is None
        await pilot.press("f5")         # re-validate: the screen own key
        await pilot.pause()
        assert current(app) == "StrategiesScreen"


@sync
async def test_dragged_path_starts_intake_without_focusing_anything():
    """A drag is a paste. It must work with nothing focused, which is the
    whole reason the drop field no longer steals focus."""
    from textual import events

    app = S3Lab()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        seen = []
        screen.start_intake = lambda raw: seen.append(raw)
        screen.post_message(events.Paste('"C:/some/strategy folder"'))
        await pilot.pause()
        assert seen == ['"C:/some/strategy folder"']


def test_section_table_and_bindings_agree():
    """The bottom bar, the bindings and the lookup come from one table."""
    keys = {key for _label, key, _name in SECTIONS}
    bound = {b[0] for b in S3Lab.BINDINGS} | {
        b[0] for b in __import__(
            "tui.screens.base", fromlist=["LabScreen"]).LabScreen.BINDINGS}
    assert keys <= bound, "a section with no key binding is unreachable"


def test_no_screen_borrows_a_navigation_key():
    """D S T R E M H Q belong to navigation on every screen.

    A screen that rebinds one wins the keypress and becomes a place you cannot
    leave. This already happened twice -- Data bound R to "rescan" and
    Strategies bound R to "re-validate", and both printed that key in their own
    hint line while the key actually went to the Run screen.
    """
    import importlib
    import pkgutil

    import tui.screens as pkg
    from tui.screens.base import LabScreen

    reserved = {key for _label, key, _name in SECTIONS} | {"h", "q"}
    offenders = {}
    for mod in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module("tui.screens." + mod.name)
        for obj in vars(module).values():
            if not (isinstance(obj, type) and issubclass(obj, LabScreen)):
                continue
            if obj is LabScreen:
                continue
            own = [b for b in obj.BINDINGS if b not in LabScreen.BINDINGS]
            clash = sorted(b[0] for b in own if b[0] in reserved)
            if clash:
                offenders[obj.__name__] = clash
    assert not offenders, "screens rebinding navigation keys: " + str(offenders)


def test_every_screen_hint_only_advertises_keys_it_has():
    """A hint that names a key the screen does not own is a lie on screen."""
    import importlib
    import pkgutil
    import re

    import tui.screens as pkg
    from tui.screens.base import LabScreen

    wrong = {}
    for mod in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module("tui.screens." + mod.name)
        for obj in vars(module).values():
            if not (isinstance(obj, type) and issubclass(obj, LabScreen)):
                continue
            if obj is LabScreen or not obj.HINT:
                continue
            have = {b[0].lower() for b in obj.BINDINGS}
            # Tokens the hint presents as keys: F5, or a lone capital letter.
            claimed = set(re.findall(r"(?<![A-Za-z])(F5|[A-Z])(?![A-Za-z])",
                                     obj.HINT))
            missing = sorted(k for k in claimed if k.lower() not in have)
            if missing:
                wrong[obj.__name__] = missing
    assert not wrong, "hints naming keys the screen does not bind: " + str(wrong)


@sync
async def test_scroll_keys_work_everywhere_and_never_navigate_away():
    """Long content stays reachable from the keyboard.

    Nothing is focused on arrival, so the scroll region never receives arrow
    keys of its own. PageUp/PageDown/Home/End are handled by the screen
    instead. On a screen with nothing to scroll they must do nothing at all --
    an action that raises leaves the screen unresponsive to every later key,
    which is the failure mode this file exists to prevent.
    """
    app = S3Lab()
    async with app.run_test(size=(140, 20)) as pilot:
        await pilot.pause()
        for key, expected in DESTINATIONS.items():
            await pilot.press(key)
            await pilot.pause()
            for scroll in ("pagedown", "pagedown", "end", "home", "pageup"):
                await pilot.press(scroll)
                await pilot.pause()
            assert current(app) == expected, (
                "scrolling moved off " + expected + " to " + current(app))
            await pilot.press("t")      # still navigable afterwards
            await pilot.pause()
            assert current(app) == "TestScreen"
