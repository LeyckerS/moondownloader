"""Test: download_file surfaces mid-transfer events to on_event when one is passed.

Issue #99. download_file could only write to rec.notes, which is read once,
after the run, inside report writing. The optional `on_event` callback lets a
front-end (GUI engine, CLI) surface these events live. This test fakes a 200
response and asserts the event reaches the callback AND still lands in notes.
"""
from __future__ import annotations

import asyncio
import collections

import moon_download
from moon_download import FileRecord, download_file


# Same fakes as test_resume_200.py, kept here so this file stands alone:
# download_file does `async with dl_session.get(url) as r:` then reads
# `r.status`, `r.headers` and `r.content.iter_chunked(...)`.


class FakeChunkIter:
    """Empty async iterator for r.content.iter_chunked()."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeContent:
    def iter_chunked(self, size):
        return FakeChunkIter()


class FakeResponse:
    def __init__(self, status: int, headers: dict):
        self.status = status
        self.headers = headers
        self.content = FakeContent()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status: int, headers: dict):
        self._status = status
        self._headers = headers

    def get(self, *args, **kwargs):
        return FakeResponse(self._status, self._headers)


class FailResponse:
    """Response whose __aenter__ raises — the connect-failed path."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


class FakeFailSession:
    def __init__(self, exc: Exception):
        self._exc = exc

    def get(self, *args, **kwargs):
        return FailResponse(self._exc)


def _make_tmp(tmp_path, size: int) -> str:
    """Create a partial file so resume > 0, return the dest path."""
    dest = str(tmp_path / "file.bin")
    with open(dest + ".tmp", "wb") as f:
        f.write(b"x" * size)
    return dest


def test_on_event_receives_resume_200(monkeypatch, tmp_path):
    # Force dl_session = _sess() and make _sess() return a fake HTTP 200.
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: FakeSession(200, {"Content-Length": "1000"}))

    dest = _make_tmp(tmp_path, 100)  # 100 bytes of partial data -> resume > 0
    rec = FileRecord(url="http://example.com/file.bin", filename="file.bin")
    events = []

    ok, status, resume = asyncio.run(download_file(
        proxy_url="http://example.com/file.bin",
        cookies="",
        dest=dest,
        rec=rec,
        bytes_acc=collections.deque(),
        kill_evt=asyncio.Event(),
        kills_so_far=0,
        telem=None,
        on_event=lambda msg, tag: events.append((msg, tag)),
    ))

    # The event must reach the callback (the live front-ends) ...
    assert any("server ignored the resume request" in m for m, _ in events)
    # ... and still land in rec.notes (the report must not lose anything).
    assert any("server ignored the resume request" in n for n in rec.notes)


def test_connect_failure_returns_and_events(monkeypatch, tmp_path):
    # The connect itself fails before the response body exists. download_file
    # must not crash on the `downloaded` variable and must still report the
    # failure to both the callback and rec.notes.
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: FakeFailSession(RuntimeError("boom")))

    rec = FileRecord(url="http://example.com/file.bin", filename="file.bin")
    events = []

    ok, status, bytes_done = asyncio.run(download_file(
        proxy_url="http://example.com/file.bin",
        cookies="",
        dest=str(tmp_path / "file.bin"),
        rec=rec,
        bytes_acc=collections.deque(),
        kill_evt=asyncio.Event(),
        kills_so_far=0,
        telem=None,
        on_event=lambda msg, tag: events.append((msg, tag)),
    ))

    assert ok is False
    # The failure reached the live callback...
    assert any("error att" in m and "boom" in m for m, _ in events)
    # ... and still landed in the report notes.
    assert any("error att" in n for n in rec.notes)
