"""Attribution slicing tests -- especially the conclusions it refuses to draw."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.analysis.attribution import (  # noqa: E402
    CONCENTRATION, MIN_TRADES, attribute, build_table,
)

BASE_TS = 1_600_000_000      # a Monday


def trades(n=100, pnl=None, hour_offsets=None, direction=1, fold=0, start=BASE_TS):
    rng = np.random.default_rng(0)
    pnl = rng.normal(10, 100, n) if pnl is None else np.asarray(pnl, dtype="float64")
    offs = np.zeros(n, dtype=int) if hour_offsets is None else np.asarray(hour_offsets)
    entry = start + offs * 3600 + np.arange(n) * 86_400
    return pd.DataFrame({
        "entry_ts": entry, "exit_ts": entry + 1800,
        "direction": np.full(n, direction), "net_pnl": pnl,
        "bars_held": np.full(n, 20), "fold": np.full(n, fold),
    })


def test_build_table_adds_every_dimension():
    t = build_table(trades(50))
    for col in ("time_of_day", "day_of_week", "year", "month",
                "session", "direction", "duration", "fold", "vol_regime"):
        assert col in t.columns


def test_long_and_short_are_never_netted():
    a = trades(40, pnl=np.full(40, 100.0), direction=1)
    b = trades(40, pnl=np.full(40, -100.0), direction=-1)
    res = attribute(build_table(pd.concat([a, b], ignore_index=True)), "direction")
    keys = {s.key for s in res.slices}
    assert keys == {"long", "short"}
    assert {round(s.net_pnl) for s in res.slices} == {4000, -4000}


def test_thin_slices_are_marked_not_significant():
    t = build_table(pd.concat([
        trades(MIN_TRADES + 5, direction=1),
        trades(3, direction=-1),
    ], ignore_index=True))
    res = attribute(t, "direction")
    thin = [s for s in res.slices if s.key == "short"][0]
    assert not thin.significant
    assert [s for s in res.slices if s.key == "long"][0].significant


def test_concentration_above_the_threshold_is_stated_plainly():
    """One slice carrying most of the P&L is the headline finding."""
    big = trades(40, pnl=np.full(40, 1000.0), direction=1)
    small = trades(40, pnl=np.full(40, 50.0), direction=-1)
    res = attribute(build_table(pd.concat([big, small], ignore_index=True)), "direction")
    assert res.concentration
    assert "not an edge" in res.concentration
    top = max(res.slices, key=lambda s: s.net_pnl)
    assert top.share > CONCENTRATION


def test_evenly_spread_pnl_raises_no_concentration_warning():
    a = trades(40, pnl=np.full(40, 100.0), direction=1)
    b = trades(40, pnl=np.full(40, 100.0), direction=-1)
    res = attribute(build_table(pd.concat([a, b], ignore_index=True)), "direction")
    assert res.concentration == ""


def test_a_losing_book_does_not_report_where_the_edge_is():
    """Shares of a negative total describe the loss, not an edge."""
    t = build_table(trades(60, pnl=np.full(60, -100.0)))
    res = attribute(t, "direction")
    assert res.concentration == ""
    assert any("not positive" in n for n in res.notes)


def test_all_thin_slices_are_flagged_as_supporting_nothing():
    t = build_table(trades(10))
    res = attribute(t, "day_of_week")
    assert any("supports a conclusion" in n for n in res.notes)
    assert res.significant == []


def test_shares_sum_to_one_when_the_total_is_positive():
    t = build_table(trades(90, pnl=np.abs(np.random.default_rng(1).normal(50, 20, 90))))
    res = attribute(t, "day_of_week")
    assert sum(s.share for s in res.slices) == pytest.approx(1.0)


def test_overnight_trades_land_in_one_bucket_not_many():
    """Thin overnight activity is one bucket, not a dozen unusable slices."""
    # Anchor to a known exchange-time midnight so the offsets are unambiguous.
    midnight = int(pd.Timestamp("2020-09-14 00:00", tz="America/Chicago").timestamp())
    t = build_table(trades(40, hour_offsets=np.tile([1, 3, 20, 22], 10), start=midnight))
    keys = {s.key for s in attribute(t, "time_of_day").slices}
    assert keys == {"overnight"}, f"expected one overnight bucket, got {keys}"


def test_unknown_dimension_raises():
    with pytest.raises(ValueError, match="unknown dimension"):
        attribute(build_table(trades(10)), "phase_of_moon")


def test_an_unprepared_table_is_refused_not_silently_grouped():
    """A raw table has numeric direction; grouping it would give +1/-1 slices."""
    with pytest.raises(ValueError, match="not prepared"):
        attribute(trades(10), "direction")


def test_a_thin_slice_cannot_raise_the_concentration_warning():
    """The warning is a conclusion, so the significance rule must apply to it."""
    many = trades(200, pnl=np.full(200, 10.0), direction=1)
    few = trades(5, pnl=np.full(5, 4000.0), direction=-1)
    res = attribute(build_table(pd.concat([many, few], ignore_index=True)), "direction")
    top = max(res.slices, key=lambda s: s.net_pnl)
    assert top.key == "short" and not top.significant
    assert res.concentration == "", "a not-significant slice must not raise a conclusion"


def test_a_dimension_that_does_not_split_the_book_raises_no_warning():
    """An RTH-only strategy is not 'concentrated in RTH'."""
    t = build_table(trades(200, pnl=np.full(200, 50.0)))
    res = attribute(t, "session")
    assert res.concentration == ""
    assert any("does not split the book" in n for n in res.notes)


def test_mar_is_annualised_over_the_calendar_span_not_summed_durations():
    """Summing trade durations produced MAR values in the tens of thousands.

    Asserted against the arithmetic rather than a round threshold: 60 daily
    trades span ~59 days, while their durations sum to 30 hours. Using the
    latter inflates MAR by roughly 48x.
    """
    n = 60
    t = build_table(trades(n, pnl=np.tile([200.0, -100.0], n // 2)))
    res = attribute(t, "direction")
    got = res.slices[0].mar

    net, max_dd, equity = 200.0 * (n // 2) - 100.0 * (n // 2), 100.0, 100_000.0
    calendar_years = ((n - 1) * 86_400 + 1800) / (365.25 * 24 * 3600)
    expected = (net / equity * 100 / calendar_years) / (max_dd / equity * 100)
    assert got == pytest.approx(expected, rel=0.02)

    duration_years = (n * 1800) / (365.25 * 24 * 3600)
    inflated = (net / equity * 100 / duration_years) / (max_dd / equity * 100)
    assert inflated > got * 20, "fixture must actually distinguish the two"
