from __future__ import annotations

import asyncio

import moon_cli
import moon_engine


def test_engine_stall_reextract_failure_completes(monkeypatch, tmp_path):
    url = "https://fuckingfast.co/example/file.zip"
    extract_calls = []
    download_calls = []

    async def fake_extract(_url, _get_browser):
        extract_calls.append(_url)

        if len(extract_calls) == 1:
            return "https://dl.fuckingfast.co/fake.zip?fake"

        return None

    async def fake_download(
        _proxy_url,
        _cookies,
        _dest,
        _rec,
        _bytes_acc,
        _kill_evt,
        _kills_so_far,
        telem=None,
        on_event=None,
        fatal_control=None,
    ):
        download_calls.append(_rec.url)
        return False, "stall_killed", 4096

    monkeypatch.setattr(moon_engine, "extract_fuckingfast", fake_extract)
    monkeypatch.setattr(moon_engine, "download_file", fake_download)

    engine = moon_engine.Engine()

    result = engine.start(
        {
            "links": [url],
            "mode": "download",
            "out_folder": str(tmp_path),
            "workers": 1,
            "dl_streams": 1,
            "retries": 0,
        }
    )

    thread = engine._thread
    assert thread is not None

    try:
        assert result["ok"] is True

        # Wait for the Engine thread to finish.
        # This directly verifies that the failed re-extraction
        # does not leave the queue waiting forever.
        thread.join(timeout=2.0)

        assert not thread.is_alive()

        assert extract_calls == [url, url]
        assert download_calls == [url]

        snapshot = engine.snapshot(0)

        assert not engine._get("_running")
        assert snapshot["state"] == "done"
        assert snapshot["metrics"]["fail"] == 1
        assert snapshot["metrics"]["dl_done"] == 0
        assert engine._tracked[url].status == "fail"

    finally:
        engine.stop()

        if engine._thread is not None:
            engine._thread.join(timeout=2)


def test_cli_stall_reextract_failure_completes(monkeypatch, tmp_path):
    url = "https://fuckingfast.co/example/file.zip"
    extract_calls = []
    download_calls = []

    async def fake_extract(_url, _get_browser):
        extract_calls.append(_url)

        if len(extract_calls) == 1:
            return "https://dl.fuckingfast.co/fake.zip?fake"

        return None

    async def fake_download(
        _proxy_url,
        _cookies,
        _dest,
        _rec,
        _bytes_acc,
        _kill_evt,
        _kills_so_far,
        telem=None,
        on_event=None,
        fatal_control=None,
    ):
        download_calls.append(_rec.url)

        # Prevent the CLI's elapsed-time calculation from seeing
        # a zero-second run on a very fast fake download.
        await asyncio.sleep(0.01)

        return False, "stall_killed", 4096

    monkeypatch.setattr(moon_cli, "extract_fuckingfast", fake_extract)
    monkeypatch.setattr(moon_cli, "download_file", fake_download)

    result = asyncio.run(
        moon_cli.run(
            [url],
            str(tmp_path / "downloads"),
            1,
            1,
            0,
            "proxies.txt",
        )
    )

    assert extract_calls == [url, url]
    assert download_calls == [url]
    assert result == (0, 1, False)
