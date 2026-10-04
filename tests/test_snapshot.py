"""Regression tests for the Engine.snapshot() metrics contract."""

import moon_engine


def make_engine():
    return moon_engine.Engine()


def set_snapshot_metrics(engine, *, dl_total, dl_done, ok, fail, kills=0):
    """Set metric counters atomically, matching Engine.snapshot()'s lock use."""
    with engine._lock:
        engine._dl_total = dl_total
        engine._dl_done = dl_done
        engine._ok = ok
        engine._fail = fail
        engine._kills = kills


def test_snapshot_reports_all_success_counts():
    engine = make_engine()
    set_snapshot_metrics(engine, dl_total=58, dl_done=58, ok=58, fail=0)

    metrics = engine.snapshot()["metrics"]

    assert metrics["dl_total"] == 58
    assert metrics["dl_done"] == 58
    assert metrics["ok"] == 58
    assert metrics["fail"] == 0
    assert metrics["kills"] == 0


def test_snapshot_reports_mixed_success_and_failure_counts():
    engine = make_engine()
    set_snapshot_metrics(engine, dl_total=58, dl_done=58, ok=12, fail=46)

    metrics = engine.snapshot()["metrics"]

    assert metrics["dl_total"] == 58
    assert metrics["dl_done"] == 58
    assert metrics["ok"] == 12
    assert metrics["fail"] == 46
    assert metrics["kills"] == 0


def test_snapshot_reports_all_failed_counts():
    engine = make_engine()
    set_snapshot_metrics(engine, dl_total=58, dl_done=58, ok=0, fail=58)

    metrics = engine.snapshot()["metrics"]

    assert metrics["dl_total"] == 58
    assert metrics["dl_done"] == 58
    assert metrics["ok"] == 0
    assert metrics["fail"] == 58
    assert metrics["kills"] == 0
