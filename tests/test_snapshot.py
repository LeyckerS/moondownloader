"""Unit tests for Engine.snapshot() — the engine/front-end contract.

Every assertion here is about a *transition* rather than a single value, because
that is what a poller like the GUI depends on: it calls snapshot() several times
a second and has to be able to tell "nothing changed" from "it moved on".

The engine is a plain object with no I/O in snapshot(), so these tests drive it
directly. Only the filesystem reads that snapshot() already performs on an idle
engine (the proxy file and the output folder listing) are left alone.

Refs #57.
"""
from __future__ import annotations

import time

from moon_engine import Engine


def engine_log_ring_size() -> int:
    """Read the ring capacity off a throwaway engine instead of hard-coding 6000."""
    return Engine()._log_ring.maxlen


# --------------------------------------------------------------------------
# 1. A fresh engine is idle, not broken
# --------------------------------------------------------------------------


def test_snapshot_on_a_fresh_engine_reports_the_idle_stage():
    # The very first paint of the GUI is a snapshot() of an engine that has never
    # run. It has to answer "idle" rather than raise.
    engine = Engine()

    assert engine.snapshot()["metrics"]["stage"] == "idle"


def test_snapshot_on_a_fresh_engine_reports_zero_elapsed():
    # _t0 is 0.0 until start(), so the elapsed clock must read as exactly zero
    # and not as the machine's uptime.
    engine = Engine()

    assert engine.snapshot()["metrics"]["elapsed_s"] == 0.0


def test_snapshot_on_a_fresh_engine_reports_zeroed_counters():
    # Nothing has been extracted, downloaded, killed or queued yet.
    engine = Engine()

    metrics = engine.snapshot()["metrics"]

    assert metrics["extract_done"] == 0
    assert metrics["extract_total"] == 0
    assert metrics["dl_done"] == 0
    assert metrics["dl_total"] == 0
    assert metrics["ok"] == 0
    assert metrics["fail"] == 0
    assert metrics["kills"] == 0
    assert metrics["active"] == 0
    assert metrics["bytes_total"] == 0


# --------------------------------------------------------------------------
# 2. Stage progression
# --------------------------------------------------------------------------


def test_snapshot_reports_extracting_while_urls_are_outstanding():
    # A running engine with extracted < total is still in the extraction phase.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    engine._url_total = 4
    engine._url_done = 1

    assert engine.snapshot()["metrics"]["stage"] == "extracting"


def test_snapshot_reports_downloading_once_extraction_is_complete():
    # Extraction finished but downloads are outstanding: the phase has moved on.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    engine._url_total = 4
    engine._url_done = 4
    engine._dl_total = 4
    engine._dl_done = 1

    assert engine.snapshot()["metrics"]["stage"] == "downloading"


def test_snapshot_reports_done_when_nothing_is_left_to_do():
    # Every URL extracted and every download finished, but the run has not been
    # torn down yet — the stage is still "done" rather than reverting.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    engine._url_total = 4
    engine._url_done = 4
    engine._dl_total = 4
    engine._dl_done = 4

    assert engine.snapshot()["metrics"]["stage"] == "done"


def test_snapshot_reports_done_once_the_run_has_ended():
    # _on_done() clears _running and sets _state="done"; the stage the GUI shows
    # after a run must be "done", not "idle".
    engine = Engine()
    engine._on_done()

    assert engine.snapshot()["metrics"]["stage"] == "done"


# --------------------------------------------------------------------------
# 3. The log cursor
# --------------------------------------------------------------------------


def test_snapshot_returns_the_log_lines_written_so_far():
    engine = Engine()
    engine.log("first", "info")
    engine.log("second", "dim")

    lines = engine.snapshot()["log"]

    assert [line[0] for line in lines] == ["first", "second"]


def test_snapshot_returns_the_tag_alongside_each_log_line():
    # The GUI styles each line by its tag, so the pair has to survive the trip.
    engine = Engine()
    engine.log("a line", "ok")
    engine.log("another", "dim")

    lines = engine.snapshot()["log"]

    assert lines == [["a line", "ok"], ["another", "dim"]]


def test_snapshot_returns_nothing_when_read_again_from_the_returned_cursor():
    # This is the contract that stops the GUI re-rendering the whole log on
    # every poll: feeding the cursor back in must yield an empty batch.
    engine = Engine()
    for i in range(3):
        engine.log(f"line {i}")

    first = engine.snapshot()
    second = engine.snapshot(cursor=first["cursor"])

    assert first["cursor"] == 3
    assert [line[0] for line in second["log"]] == []


def test_snapshot_cursor_advances_when_new_lines_arrive():
    # A poll in between must not lose lines written after the previous cursor.
    engine = Engine()
    engine.log("before")

    first = engine.snapshot()
    engine.log("after")
    second = engine.snapshot(cursor=first["cursor"])

    assert [line[0] for line in second["log"]] == ["after"]
    assert second["cursor"] > first["cursor"]


def test_snapshot_hands_back_the_oldest_retained_line_when_the_ring_overflowed():
    # _log_ring keeps 6000 lines. A poller that fell further behind than that
    # must receive the oldest line still held rather than a gap it cannot detect.
    ring_size = engine_log_ring_size()
    engine = Engine()
    for i in range(ring_size + 5):
        engine.log(f"line-{i}")

    lines = engine.snapshot(cursor=0)["log"]

    # 5 lines were pushed out of the ring, so it starts at index 5.
    assert lines[0][0] == "line-5"
    assert len(lines) == ring_size


def test_snapshot_returns_nothing_when_read_from_a_cursor_behind_an_overflow():
    # Same overflow, but the poller catches up afterwards: the stale cursor must
    # not replay lines it has already been shown.
    ring_size = engine_log_ring_size()
    engine = Engine()
    for i in range(ring_size + 5):
        engine.log(f"line-{i}")

    caught_up = engine.snapshot(cursor=ring_size + 5)["log"]

    assert caught_up == []


# --------------------------------------------------------------------------
# 4. Speed and ETA are safe when there is nothing to measure
# --------------------------------------------------------------------------


def test_snapshot_reports_zero_speed_with_no_byte_samples():
    # A fresh engine has an empty byte accumulator. Dividing by it, or by a zero
    # elapsed time, must not raise — the GUI polls this before anything runs.
    engine = Engine()

    assert engine.snapshot()["metrics"]["speed_mbs"] == 0.0


def test_snapshot_reports_zero_eta_with_no_byte_samples():
    # Same empty case, for the estimate. It must read 0 rather than crash.
    engine = Engine()

    assert engine.snapshot()["metrics"]["eta_s"] == 0.0


def test_snapshot_reports_zero_speed_from_a_lone_recent_sample():
    # One sample is not a rate. A download that has just started leaves exactly
    # one entry in the accumulator, and dividing it by the elapsed time would
    # report a wild speed for a transfer that has barely begun.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    now = time.monotonic()
    engine._t0 = now - 30.0
    engine._bytes_acc.extend([(now - 0.1, 4_000_000)])

    assert engine.snapshot()["metrics"]["speed_mbs"] == 0.0


def test_snapshot_reports_zero_speed_when_every_sample_has_gone_stale():
    # Only samples from the last 3 seconds count. A stalled run keeps its old
    # samples, which must read as "no speed" rather than as a stale average.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    engine._t0 = time.monotonic()
    old = engine._t0 - 60.0
    engine._bytes_acc.extend([(old, 1_000_000), (old + 1.0, 1_000_000)])

    metrics = engine.snapshot()["metrics"]

    assert metrics["speed_mbs"] == 0.0
    assert metrics["eta_s"] == 0.0


def test_snapshot_reports_no_eta_before_the_first_file_completes():
    # eta needs an average file size, which needs a completed download. With
    # bytes moving but nothing finished yet there is no estimate to give.
    engine = Engine()
    engine._running = True
    engine._state = "running"
    now = time.monotonic()
    engine._t0 = now - 5.0
    engine._dl_total = 4
    engine._dl_done = 0
    engine._bytes_acc.extend([(now - 0.5, 2_000_000), (now - 0.1, 2_000_000)])

    metrics = engine.snapshot()["metrics"]

    assert metrics["speed_mbs"] > 0.0
    assert metrics["eta_s"] == 0.0


# --------------------------------------------------------------------------
# 5. Counters reset between runs
# --------------------------------------------------------------------------


def test_start_clears_the_previous_runs_counters(monkeypatch, tmp_path):
    # Totals left over from a finished run must not bleed into the next one.
    # _guarded_run is swapped out so start() resets state without doing any work.
    engine = Engine()
    monkeypatch.setattr(engine, "_guarded_run", lambda *a, **k: None)
    engine._ok = 7
    engine._fail = 3
    engine._kills = 2
    engine._dls = 5
    engine._url_done = 9
    engine._dl_done = 9
    engine._bytes_acc.extend([(time.monotonic(), 4_096)])

    engine.start({"links": ["https://example.com/a"], "out_folder": str(tmp_path)})

    metrics = engine.snapshot()["metrics"]
    assert metrics["ok"] == 0
    assert metrics["fail"] == 0
    assert metrics["kills"] == 0
    assert metrics["active"] == 0
    assert metrics["bytes_total"] == 0


def test_start_reports_the_new_run_totals_from_the_link_list(monkeypatch, tmp_path):
    # The totals are rebuilt from the links actually pasted, not left at zero.
    engine = Engine()
    monkeypatch.setattr(engine, "_guarded_run", lambda *a, **k: None)

    engine.start(
        {
            "links": ["https://example.com/a", "https://example.com/b"],
            "out_folder": str(tmp_path),
        }
    )

    metrics = engine.snapshot()["metrics"]
    assert metrics["extract_total"] == 2
    assert metrics["dl_total"] == 2
    assert metrics["extract_done"] == 0
    assert metrics["dl_done"] == 0


def test_start_clears_the_byte_accumulator(monkeypatch, tmp_path):
    # A stale accumulator would report the previous run's speed against the new
    # run's elapsed time.
    engine = Engine()
    monkeypatch.setattr(engine, "_guarded_run", lambda *a, **k: None)
    engine._bytes_acc.extend([(time.monotonic(), 9_000_000)])

    engine.start({"links": ["https://example.com/a"], "out_folder": str(tmp_path)})

    assert engine.snapshot()["metrics"]["speed_mbs"] == 0.0


def test_start_clears_the_log_so_a_new_run_starts_from_a_clean_console(monkeypatch, tmp_path):
    # The log ring is reset alongside the counters, so run two does not open with
    # run one's transcript still on screen.
    engine = Engine()
    monkeypatch.setattr(engine, "_guarded_run", lambda *a, **k: None)
    engine.log("left over from the previous run")

    engine.start({"links": ["https://example.com/a"], "out_folder": str(tmp_path)})

    lines = [line[0] for line in engine.snapshot()["log"]]
    assert "left over from the previous run" not in lines
