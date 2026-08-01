"""Test: when a server ignores a resume request (HTTP 200), a note is logged.

Regression test for #90. The resume branch at moon_download.py:468 resets
`resume` to 0 when the server answers 200, which truncates the partial file.
This test fakes a 200 response and asserts the note is recorded.
"""
from __future__ import annotations

import asyncio
import collections
import os

import moon_download
from moon_download import FileRecord, download_file



# download_file does `async with dl_session.get(url) as r:` then reads
# `r.status`, `r.headers` and `r.content.iter_chunked(...)`. These three
# fakes implement exactly that surface, no network involved.


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


def _make_tmp(tmp_path, size: int) -> str:
    """Create a partial file so resume > 0, return the dest path."""
    dest = str(tmp_path / "file.bin")
    with open(dest + ".tmp", "wb") as f:
        f.write(b"x" * size)
    return dest


def test_resume_200_logs_a_note(monkeypatch, tmp_path):
    # 1. Force dl_session = _sess() by making the proxy pool yield nothing.
    # 2. Replace _sess() with a fake that returns HTTP 200.
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: FakeSession(200, {"Content-Length": "1000"}))

    dest = _make_tmp(tmp_path, 100)  # 100 bytes of partial data -> resume > 0
    rec = FileRecord(url="http://example.com/file.bin", filename="file.bin")
    bytes_acc = collections.deque()
    kill_evt = asyncio.Event()

    ok, status, resume = asyncio.run(download_file(
        proxy_url="http://example.com/file.bin",
        cookies="",
        dest=dest,
        rec=rec,
        bytes_acc=bytes_acc,
        kill_evt=kill_evt,
        kills_so_far=0,
        telem=None,
    ))

    # The note must be recorded when the server ignored the resume request.
    assert any("server ignored the resume request" in n for n in rec.notes)
