from __future__ import annotations

import asyncio
import time

import moon_engine


def test_engine_stop_closes_browser_before_returning(browser_calls, monkeypatch, tmp_path):
    release = False

    async def blocked_extract(browser, url):
        nonlocal release
        while not release and browser_calls["shutdown_chrome"] == 0:
            await asyncio.sleep(0.01)
        if browser_calls["shutdown_chrome"]:
            raise RuntimeError("browser closed")
        return url + "?fake", ""

    monkeypatch.setattr(moon_engine, "extract_datanodes", blocked_extract)
    engine = moon_engine.Engine()
    result = engine.start(
        {
            "links": ["https://datanodes.to/example/file.zip"],
            "mode": "links",
            "out_folder": str(tmp_path),
            "workers": 2,
            "dl_streams": 2,
            "retries": 0,
        }
    )
    assert result == {"ok": True, "proxies": 0, "effective": result["effective"]}

    deadline = time.monotonic() + 2
    while browser_calls["open_browser"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert browser_calls["open_browser"] == 1

    try:
        started = time.monotonic()
        assert engine.stop(timeout=1.5) == {"ok": True}
        elapsed = time.monotonic() - started
        closed_before_return = browser_calls["shutdown_chrome"]
        thread_alive_before_return = engine._thread.is_alive()
    finally:
        release = True
        engine.stop()
        engine._thread.join(timeout=2)

    assert closed_before_return == 1
    assert not thread_alive_before_return
    assert elapsed < 1.5
