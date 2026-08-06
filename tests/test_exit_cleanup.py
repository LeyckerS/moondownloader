from __future__ import annotations

import asyncio
import pathlib
import threading
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


def test_engine_stop_aborts_inflight_download_without_counting_failure(
    browser_calls, monkeypatch, tmp_path
):
    download_started = threading.Event()
    release_download = threading.Event()
    failed_links = pathlib.Path(moon_engine.__file__).with_name("failed_links.txt")
    failed_links.unlink(missing_ok=True)

    async def fake_download(
        proxy_url,
        cookies,
        dest,
        rec,
        bytes_acc,
        kill_evt,
        kills_so_far,
        telem=None,
        on_event=None,
        fatal_control=None,
    ):
        download_started.set()
        partial_path = pathlib.Path(dest + ".tmp")
        partial_path.write_bytes(b"partial")
        while not release_download.is_set():
            if kill_evt.is_set():
                return False, "stall_killed", 4096
            await asyncio.sleep(0.01)
        return True, "ok", 4096

    monkeypatch.setattr(moon_engine, "download_file", fake_download)

    engine = moon_engine.Engine()
    result = engine.start(
        {
            "links": ["https://fuckingfast.co/example/file.zip"],
            "mode": "download",
            "out_folder": str(tmp_path),
            "workers": 1,
            "dl_streams": 1,
            "retries": 0,
        }
    )
    assert result == {"ok": True, "proxies": 0, "effective": result["effective"]}
    assert download_started.wait(timeout=2)

    try:
        started = time.monotonic()
        assert engine.stop(timeout=1.5) == {"ok": True}
        elapsed = time.monotonic() - started
        thread_alive_before_cleanup = engine._thread.is_alive()
        snapshot = engine.snapshot()
        partial_files = list(tmp_path.glob("*.tmp"))
    finally:
        release_download.set()
        engine.stop()
        engine._thread.join(timeout=2)

    assert not thread_alive_before_cleanup
    assert elapsed < 1.5
    assert snapshot["state"] == "done"
    assert snapshot["metrics"]["fail"] == 0
    assert snapshot["metrics"]["kills"] == 0
    assert partial_files
    assert not failed_links.exists()
