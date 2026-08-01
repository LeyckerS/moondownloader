"""Regression tests for concurrent downloads with the same filename."""
from __future__ import annotations

import asyncio
import collections

import moon_download
from moon_download import Telemetry, download_file


class FakeContent:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def iter_chunked(self, size):
        for offset in range(0, len(self.payload), 2):
            await asyncio.sleep(0)
            yield self.payload[offset:offset + 2]


class FakeResponse:
    def __init__(self, payload: bytes):
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}
        self.content = FakeContent(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads

    def get(self, url, **kwargs):
        return FakeResponse(self.payloads[url])


def test_colliding_names_download_to_distinct_files(monkeypatch, tmp_path):
    urls = ["https://example.com/first", "https://example.com/second"]
    payloads = {urls[0]: b"first payload", urls[1]: b"second payload"}
    telem = Telemetry({"total_links": 2})
    records = [telem.reg(url, "archive.tar.gz") for url in urls]

    assert [rec.filename for rec in records] == [
        "archive.tar.gz",
        "archive.tar (2).gz",
    ]
    assert records[1].notes == [
        'filename collision: "archive.tar.gz" renamed to "archive.tar (2).gz"'
    ]
    tmp_paths = [tmp_path / f"{rec.filename}.tmp" for rec in records]
    assert tmp_paths[0] != tmp_paths[1]

    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: FakeSession(payloads))

    async def run_downloads():
        return await asyncio.gather(*[
            download_file(
                proxy_url=rec.url,
                cookies="",
                dest=str(tmp_path / rec.filename),
                rec=rec,
                bytes_acc=collections.deque(),
                kill_evt=asyncio.Event(),
                kills_so_far=0,
            )
            for rec in records
        ])

    results = asyncio.run(run_downloads())

    assert all(result[0] for result in results)
    for rec, tmp in zip(records, tmp_paths):
        assert (tmp_path / rec.filename).read_bytes() == payloads[rec.url]
        assert not tmp.exists()


def test_collision_check_is_case_insensitive():
    telem = Telemetry({"total_links": 2})

    first = telem.reg("https://example.com/first", "Release.RAR")
    second = telem.reg("https://example.com/second", "release.rar")

    assert first.filename == "Release.RAR"
    assert second.filename == "release (2).rar"
