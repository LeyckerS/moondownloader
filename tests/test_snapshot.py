"""Unit tests for ``Engine.snapshot()`` — the contract every front-end reads.

``snapshot()`` is the single interface between the engine and both front-ends:
the GUI polls it several times a second, ``moon_bridge.py`` serves it over the
loopback API, and ``render_gui.py`` / ``integration_http.py`` depend on its
shape. It had no unit tests, so every display bug this project has had went
through it unverified — #58 (a clock that never stopped) needed nothing more
than calling the method twice and comparing two numbers.

The interesting assertions are about transitions, not single values. The
counters the stage machine reads are set directly on the engine rather than
through ``start()``: a mid-run state is what is under test, and driving a real
download to produce it tests the download instead (see #57).
"""
from __future__ import annotations

import time

import moon_engine

# One fuckingfast link, stubbed by the browser_calls fixture.
LINK = "https://fuckingfast.co/x1/pack.part01.rar"


def mid_run(engine: moon_engine.Engine, **counters) -> None:
    """Put the engine into a mid-run state by setting what snapshot() reads."""
    with engine._lock:
        engine._running = True
        engine._state = "running"
        for name, value in counters.items():
            setattr(engine, name, value)


# ── 1. idle before anything runs ─────────────────────────────────────────────


def test_fresh_engine_reports_idle_with_zeroed_counters():
    snap = moon_engine.Engine().snapshot(0)

    assert snap["state"] == "idle"
    metrics = snap["metrics"]
    assert metrics["stage"] == "idle"
    assert metrics["elapsed_s"] == 0
    assert metrics["extract_total"] == 0
    assert metrics["extract_done"] == 0
    assert metrics["dl_total"] == 0
    assert metrics["dl_done"] == 0
    assert metrics["ok"] == 0
    assert metrics["fail"] == 0
    assert metrics["kills"] == 0
    assert metrics["active"] == 0
    assert snap["log"] == []
    assert snap["cursor"] == 0


# ── 2. stage progression ─────────────────────────────────────────────────────


def test_stage_is_extracting_while_links_remain_unextracted():
    engine = moon_engine.Engine()
    mid_run(engine, _url_total=3, _url_done=1, _dl_total=3, _dl_done=0)

    assert engine.snapshot(0)["metrics"]["stage"] == "extracting"


def test_stage_is_downloading_once_every_link_is_extracted():
    engine = moon_engine.Engine()
    mid_run(engine, _url_total=3, _url_done=3, _dl_total=3, _dl_done=1)

    assert engine.snapshot(0)["metrics"]["stage"] == "downloading"


def test_stage_is_done_once_every_download_finished():
    engine = moon_engine.Engine()
    mid_run(engine, _url_total=3, _url_done=3, _dl_total=3, _dl_done=3)

    assert engine.snapshot(0)["metrics"]["stage"] == "done"


def test_a_finished_run_keeps_stage_done_and_stops_the_clock():
    # The shape of #58: the run is over, the state says done, and the elapsed
    # clock must freeze at the run instead of counting on forever.
    engine = moon_engine.Engine()
    with engine._lock:
        engine._running = False
        engine._state = "done"
        engine._t0 = 100.0
        engine._t_end = 112.5

    snap = engine.snapshot(0)
    assert snap["metrics"]["stage"] == "done"
    assert snap["metrics"]["elapsed_s"] == 12.5
    # Frozen: a later snapshot reports the same number, not a growing one.
    assert engine.snapshot(0)["metrics"]["elapsed_s"] == 12.5


# ── 3. the log cursor ────────────────────────────────────────────────────────


def test_log_cursor_returns_every_line_when_starting_from_zero():
    engine = moon_engine.Engine()
    engine.log("one", "info")
    engine.log("two", "warn")
    engine.log("three", "fail")

    snap = engine.snapshot(0)
    assert [line[0] for line in snap["log"]] == ["one", "two", "three"]
    assert snap["cursor"] == 3


def test_a_second_read_with_the_same_cursor_returns_nothing():
    engine = moon_engine.Engine()
    engine.log("one")
    cursor = engine.snapshot(0)["cursor"]

    snap = engine.snapshot(cursor)
    assert snap["log"] == []
    # The cursor is the total number of lines ever logged, so it does not move
    # when nothing new was logged.
    assert snap["cursor"] == cursor


def test_lines_logged_after_the_cursor_come_back_on_the_next_read():
    engine = moon_engine.Engine()
    engine.log("one")
    cursor = engine.snapshot(0)["cursor"]
    engine.log("two")

    snap = engine.snapshot(cursor)
    assert [line[0] for line in snap["log"]] == ["two"]
    assert snap["cursor"] == 2


# ── 4. ETA and speed are safe when empty ─────────────────────────────────────


def test_speed_and_eta_are_zero_before_any_run():
    metrics = moon_engine.Engine().snapshot(0)["metrics"]

    assert metrics["speed_mbs"] == 0
    assert metrics["eta_s"] == 0


def test_eta_stays_zero_once_downloads_are_in_flight():
    # The first seconds of a run: files are downloading but not one byte
    # sample exists yet. Nothing may divide by zero here.
    engine = moon_engine.Engine()
    mid_run(engine, _dl_total=5, _dl_done=1)

    metrics = engine.snapshot(0)["metrics"]
    assert metrics["speed_mbs"] == 0
    assert metrics["eta_s"] == 0


# ── 5. counters reset between runs ───────────────────────────────────────────


def test_start_clears_the_previous_runs_totals(browser_calls, tmp_path):
    engine = moon_engine.Engine()
    try:
        # Leave the engine looking like a finished seven-link run.
        with engine._lock:
            engine._url_total = 7
            engine._url_done = 7
            engine._dl_total = 7
            engine._dl_done = 7
            engine._ok = 7

        result = engine.start(
            {
                "links": [LINK],
                "mode": "download",
                "out_folder": str(tmp_path),
                "workers": 2,
                "dl_streams": 2,
                "retries": 1,
            }
        )
        assert not result.get("error"), f"start refused: {result['error']}"

        deadline = time.monotonic() + 10
        while engine._get("_running") and time.monotonic() < deadline:
            time.sleep(0.02)

        metrics = engine.snapshot(0)["metrics"]
        # The seven-link totals are gone, not carried forward...
        assert metrics["dl_total"] == 1
        assert metrics["dl_done"] == 1
        # ...and the previous run's 7 ok files did not leak into this one.
        assert metrics["ok"] == 1
    finally:
        engine.stop()
