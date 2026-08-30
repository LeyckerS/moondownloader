from moon_engine import Engine, FileRecord
import time

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

def test_snapshot_eta_clamp_and_none():
    e = Engine()
    snap = e.snapshot()["metrics"]
    assert snap["eta_s"] is None

def test_snapshot_eta_ignores_terminal_states():
    """
    Ensures files in terminal states ('ok', 'fail', 'aborted', 'stopped')
    dont contribute leftover bytes to ETA calculation.
    """
    now = time.monotonic()
    e = Engine()

    # 1. Fake recent speed (~2 MB/s)
    e._bytes_acc.extend([(now - 9.0, 18_000_000), (now - 0.1, 200_000)])

    # 2. Create a record that stopped mid-download (90MB left)
    terminal_rec = FileRecord(url='u1', filename='f1.bin')
    terminal_rec.file_bytes = 100_000_000
    terminal_rec.done_bytes = 10_000_000
    e._tracked['u1'] = terminal_rec

    # 3. Set total/done to 1 so no leftover untracked files are calculated
    e._dl_total = 1
    e._dl_done = 1

    # 4. Test all terminal states -> ensure none generate a ghost ETA
    terminal_states = ("ok", "fail", "aborted", "stopped")

    for state in terminal_states:
        terminal_rec.status = state
        eta = e.snapshot()['metrics']['eta_s']
        assert eta in (0, None), f"Failed on state '{state}': Expected ETA to be 0 or None, got {eta}"
