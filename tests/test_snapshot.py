"""Tests for Engine.snapshot() contract."""

from __future__ import annotations

import time
import moon_engine


def test_snapshot_initial_state_is_idle():
    engine = moon_engine.Engine()

    snap = engine.snapshot()

    assert snap["metrics"]["stage"] == "idle"
    assert snap["metrics"]["elapsed_s"] == 0.0
    assert snap["metrics"]["ok"] == 0
    assert snap["metrics"]["fail"] == 0


def test_snapshot_reports_extracting_downloading_done_stages():
    engine = moon_engine.Engine()

    engine._running = True
    engine._url_total = 10
    engine._url_done = 2

    assert engine.snapshot()["metrics"]["stage"] == "extracting"

    engine._url_done = 10
    engine._dl_total = 5
    engine._dl_done = 2

    assert engine.snapshot()["metrics"]["stage"] == "downloading"

    engine._dl_done = 5

    assert engine.snapshot()["metrics"]["stage"] == "done"


def test_snapshot_empty_speed_and_eta_are_zero():
    engine = moon_engine.Engine()

    metrics = engine.snapshot()["metrics"]

    assert metrics["speed_mbs"] == 0.0
    assert metrics["eta_s"] == 0.0


def test_snapshot_log_cursor_returns_only_new_logs():
    engine = moon_engine.Engine()

    first = engine.snapshot()

    cursor = first["cursor"]

    engine.log("hello", "info")

    second = engine.snapshot(cursor)

    assert len(second["log"]) == 1
    assert second["log"][0][0] == "hello"

    third = engine.snapshot(second["cursor"])

    assert third["log"] == []
def test_start_resets_previous_run_counters(tmp_path):
    engine = moon_engine.Engine()

    engine._ok = 20
    engine._fail = 5
    engine._kills = 3

    # prevent real worker execution from changing counters
    engine._guarded_run = lambda *args: None

    result = engine.start(
        {
            "links": [
                "https://example.com/file"
            ],
            "mode": "links",
            "out_folder": str(tmp_path),
            "workers": 1,
            "dl_streams": 1,
            "retries": 1,
        }
    )

    assert "error" not in result

    metrics = engine.snapshot()["metrics"]

    assert metrics["ok"] == 0
    assert metrics["fail"] == 0
    assert metrics["kills"] == 0