from moon_engine import Engine, FileRecord
import pytest
import time

MIB = 1_048_576

def engine_at_2_mibs():
    """An engine whose 10-second ETA window holds 20 MiB, so the ETA rate is exactly 2 MiB/s."""
    e = Engine()
    now = time.monotonic()
    e._t0 = now - 1000.0
    e._bytes_acc.extend([(now - 5.0, 10 * MIB), (now - 1.0, 10 * MIB)])
    return e

def test_snapshot_speed_is_average_not_peak():
    e = Engine()
    now = time.monotonic()
    e._bytes_acc.extend([(now - 0.20, 1_000_000),(now - 0.10, 1_000_000),(now - 0.00, 1_000_000),])
    e._t0 = now - 1000.0
    got = e.snapshot()["metrics"]["speed_mbs"]
    assert 0.5 < got < 1.5
    e._bytes_acc.clear()
    e._bytes_acc.extend([(now - 0.20, 1_000_000),(now - 0.10, 1_000_000),(now - 0.00, 1_000_000),])
    e._t0 = now - 1.5
    got = e.snapshot()["metrics"]["speed_mbs"]
    assert 1.5 < got < 2.5

def test_snapshot_eta_is_none_without_speed():
    """No bytes yet means no rate and no estimate: None, not the 0.0 main reported."""
    e = Engine()
    snap = e.snapshot()["metrics"]
    assert snap["eta_s"] is None

def test_snapshot_eta_is_none_past_two_hours():
    """#85: past two hours the ETA is not an estimate, so it is None, not a clamped 7200.

    The first case is the control: same rate, same single file, a real number just under
    the limit. That is what proves the two Nones after it come from the clamp rather than
    from the ETA branch being skipped.
    """
    e = engine_at_2_mibs()
    rec = FileRecord(url="big", filename="big.bin", status="downloading")
    e._tracked["big"] = rec
    e._dl_total = 1

    rec.file_bytes = 14_398 * MIB       # 7199 s at 2 MiB/s
    assert e.snapshot()["metrics"]["eta_s"] == 7199.0
    rec.file_bytes = 14_400 * MIB       # exactly 7200 s: the old clamp, shown as "2h 00m"
    assert e.snapshot()["metrics"]["eta_s"] is None
    rec.file_bytes = 1024 * 1024 * MIB  # 1 TiB
    assert e.snapshot()["metrics"]["eta_s"] is None

@pytest.mark.parametrize("status", ["ok", "fail", "aborted", "stopped"])
def test_snapshot_eta_ignores_terminal_states(status):
    """A file that has ended must not leave its unfetched bytes in the ETA.

    One file is still downloading with 50 MiB to go; the other ended in `status` with
    90 MiB it will never fetch. At 2 MiB/s only the first counts: 25 s. Without the
    terminal-state filter the same setup gives 70 s.
    """
    e = engine_at_2_mibs()
    e._tracked["active"] = FileRecord(url="active", filename="active.bin", status="downloading",
                                      file_bytes=100 * MIB, done_bytes=50 * MIB)
    e._tracked["ended"] = FileRecord(url="ended", filename="ended.bin", status=status,
                                     file_bytes=100 * MIB, done_bytes=10 * MIB)
    # One of two files outstanding, so the ETA branch runs; the active record accounts
    # for it, so no queued-file estimate is added on top.
    e._dl_total = 2
    e._dl_done = 1

    assert e.snapshot()["metrics"]["eta_s"] == 25.0
