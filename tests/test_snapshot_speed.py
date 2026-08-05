from moon_engine import Engine
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