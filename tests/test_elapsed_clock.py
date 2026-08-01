import time
from moon_engine import Engine


def test_elapsed_freezes_after_done():
    engine = Engine()
    engine._t0 = time.monotonic()
    engine._on_done()

    first = engine.snapshot()["metrics"]["elapsed_s"]
    time.sleep(0.2)
    second = engine.snapshot()["metrics"]["elapsed_s"]

    assert first == second


def test_elapsed_resets_on_new_run(monkeypatch, tmp_path):
    engine = Engine()
    engine._t0 = time.monotonic()
    engine._on_done()
    time.sleep(0.2)

    # Swap out the real extraction/download path so start() doesn't touch
    # the network or a browser — only the state-reset logic is under test.
    monkeypatch.setattr(engine, "_guarded_run", lambda *a, **k: None)

    result = engine.start({"links": ["https://example.com/f1"], "out_folder": str(tmp_path)})
    assert result.get("ok") is True

    elapsed_after_restart = engine.snapshot()["metrics"]["elapsed_s"]
    assert elapsed_after_restart < 0.2


def test_elapsed_still_live_while_running():
    engine = Engine()
    engine._t0 = time.monotonic()

    first = engine.snapshot()["metrics"]["elapsed_s"]
    time.sleep(0.2)
    second = engine.snapshot()["metrics"]["elapsed_s"]

    assert second > first
