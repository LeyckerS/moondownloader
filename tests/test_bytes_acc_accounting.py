"""Test: every byte download_file receives is recorded in bytes_acc exactly once.

Issue #172. `bytes_acc` is the deque both front-ends read: the engine sums it
for `bytes_total` and the ETA's average-file-size term, and the CLI sums it for
the total-GB line and the final summary. Each entry is supposed to be bytes
received, once.

Every chunk was appended as it arrived, and then the tail flush re-appended the
whole leftover buffer — bytes that were already counted. With `WRITE_BUF` at
16 MiB, any file smaller than that never reaches the mid-loop flush, so its
entire size was counted twice.

The cases that probe a buffer boundary — the multi-buffer transfer, the exact
multiple, and the small-transfer sweep — derive their sizes from `WRITE_BUF` and
`RECV_CHUNK` so they keep their meaning if those change. The rest use plain
megabyte transfers, which is enough for what they assert. Accounting
expectations come from the chunks the fake session actually handed over. Fakes follow the pattern in
`test_on_event.py` and `test_resume_200.py`.
"""
from __future__ import annotations

import asyncio
import collections
import os

import pytest

import moon_download
from moon_download import FileRecord, RunFatalControl, download_file


class FakeChunkIter:
    """Yield the prepared chunks, recording every one it actually handed out.

    `delivered` is an observation, not a model of the loop. A test that assumed
    which chunks were consumed would keep passing if the order of the abort
    check and the append ever changed, which is the thing worth catching.
    """

    def __init__(self, chunks, before_chunk=None, delivered=None, aborted=None, abort_probe=None):
        self._chunks = list(chunks)
        self._before_chunk = before_chunk
        self.delivered = delivered if delivered is not None else []
        self._aborted = aborted
        self._abort_probe = abort_probe

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._before_chunk is not None:
            self._before_chunk()
        if not self._chunks:
            raise StopAsyncIteration
        chunk = self._chunks.pop(0)
        self.delivered.append(chunk)
        if self._aborted is not None:
            self._aborted.append(bool(self._abort_probe()) if self._abort_probe else False)
        return chunk


class FakeContent:
    def __init__(self, chunks, before_chunk=None, delivered=None, aborted=None, abort_probe=None):
        self._chunks = chunks
        self._before_chunk = before_chunk
        self.delivered = delivered if delivered is not None else []
        self._aborted = aborted
        self._abort_probe = abort_probe

    def iter_chunked(self, size):
        return FakeChunkIter(
            self._chunks, self._before_chunk, self.delivered, self._aborted, self._abort_probe
        )


class FakeResponse:
    def __init__(
        self, status, headers, chunks, before_chunk=None, delivered=None,
        aborted=None, abort_probe=None,
    ):
        self.status = status
        self.headers = headers
        self.content = FakeContent(chunks, before_chunk, delivered, aborted, abort_probe)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(
        self, status, headers, chunks, before_chunk=None, delivered=None,
        aborted=None, abort_probe=None,
    ):
        self._status = status
        self._headers = headers
        self._chunks = chunks
        self._before_chunk = before_chunk
        self.delivered = delivered if delivered is not None else []
        self._aborted = aborted
        self._abort_probe = abort_probe

    def get(self, *args, **kwargs):
        return FakeResponse(
            self._status, self._headers, self._chunks, self._before_chunk,
            self.delivered, self._aborted, self._abort_probe,
        )


def chunks_totalling(total: int, chunk_size: int) -> list[bytes]:
    parts = []
    remaining = total
    while remaining > 0:
        take = min(chunk_size, remaining)
        parts.append(b"\0" * take)
        remaining -= take
    return parts


def run_download(
    monkeypatch, tmp_path, chunks, *, before_chunk=None, fatal_control=None, abort_probe=None
):
    """Drive download_file over a fake 200 response.

    Returns (result, deque, dest, delivered) where `delivered` is what the fake
    iterator actually handed to the loop.
    """
    total = sum(len(chunk) for chunk in chunks)
    delivered: list[bytes] = []
    control_set_at_handoff: list[bool] = []
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(
        moon_download,
        "_sess",
        lambda: FakeSession(
            200, {"Content-Length": str(total)}, chunks, before_chunk, delivered,
            control_set_at_handoff, abort_probe,
        ),
    )

    dest = str(tmp_path / "file.bin")
    rec = FileRecord(url="http://example.com/file.bin", filename="file.bin")
    bytes_acc: collections.deque = collections.deque()
    result = asyncio.run(
        download_file(
            proxy_url="http://example.com/file.bin",
            cookies="",
            dest=dest,
            rec=rec,
            bytes_acc=bytes_acc,
            kill_evt=asyncio.Event(),
            kills_so_far=0,
            telem=None,
            on_event=None,
            fatal_control=fatal_control,
        )
    )
    return result, bytes_acc, dest, delivered, control_set_at_handoff


def recorded(bytes_acc) -> int:
    return sum(size for _, size in bytes_acc)


def test_sub_write_buffer_transfer_counts_each_byte_once(monkeypatch, tmp_path):
    """The reported symptom: a file under WRITE_BUF was counted exactly twice."""
    chunks = chunks_totalling(5 * 1024 * 1024, 1024 * 1024)
    (ok, status, _), bytes_acc, dest, delivered, _ = run_download(monkeypatch, tmp_path, chunks)

    assert (ok, status) == (True, "ok")
    assert recorded(bytes_acc) == sum(len(chunk) for chunk in delivered)
    assert os.path.getsize(dest) == recorded(bytes_acc)


def test_multi_buffer_transfer_counts_each_byte_once(monkeypatch, tmp_path):
    """A transfer past WRITE_BUF over-counted by whatever the tail buffer held.

    The layout is built so a partial tail is left whatever `WRITE_BUF` and
    `RECV_CHUNK` are set to: full-size chunks up to the buffer, then one short
    chunk that cannot trigger the mid-loop flush on its own.
    """
    tail = max(1, moon_download.RECV_CHUNK // 2)
    total = moon_download.WRITE_BUF + tail
    chunks = chunks_totalling(moon_download.WRITE_BUF, moon_download.RECV_CHUNK)
    chunks.append(bytes(tail))
    (ok, status, _), bytes_acc, dest, delivered, _ = run_download(monkeypatch, tmp_path, chunks)

    assert (ok, status) == (True, "ok")
    assert recorded(bytes_acc) == total
    assert os.path.getsize(dest) == total


def test_exact_multiple_of_write_buffer_leaves_no_tail(monkeypatch, tmp_path):
    """An exact multiple never enters the tail flush, so it pins the other half.

    This case was always correct, because `buf` is empty by the time the loop
    ends. It is here to hold the per-chunk append honest: a change that moved
    accounting to the flush sites would record one entry for the whole buffer
    instead of one per chunk, which the entry vector below catches.
    """
    total = moon_download.WRITE_BUF
    chunks = chunks_totalling(total, moon_download.RECV_CHUNK)
    (ok, status, _), bytes_acc, dest, delivered, _ = run_download(monkeypatch, tmp_path, chunks)

    assert (ok, status) == (True, "ok")
    assert [size for _, size in bytes_acc] == [len(chunk) for chunk in delivered]
    assert recorded(bytes_acc) == total
    assert os.path.getsize(dest) == total


def test_one_entry_per_received_chunk(monkeypatch, tmp_path):
    """Both front-ends window the deque at three seconds for the live speed.

    So an entry has to arrive when its bytes did. Counting the right total with
    the wrong number of entries would still report speed in buffer-sized steps.
    """
    chunks = chunks_totalling(5 * 1024 * 1024, 1024 * 1024)
    _, bytes_acc, _, delivered, _ = run_download(monkeypatch, tmp_path, chunks)

    assert len(bytes_acc) == len(delivered)
    assert [size for _, size in bytes_acc] == [len(chunk) for chunk in delivered]


def test_entry_timestamps_stay_within_the_transfer(monkeypatch, tmp_path):
    """No entry may be stamped before the call started or after it returned.

    This is a bound, not a per-chunk claim: it rejects an entry stamped outside
    the transfer, which is what moving the append to a flush site would not by
    itself produce. `test_one_entry_per_received_chunk` carries the shape.
    """
    import time

    chunks = chunks_totalling(3 * 1024 * 1024, 1024 * 1024)
    started = time.monotonic()
    _, bytes_acc, _, delivered, _ = run_download(monkeypatch, tmp_path, chunks)
    finished = time.monotonic()

    assert bytes_acc
    for stamp, _ in bytes_acc:
        assert started <= stamp <= finished


def test_aborted_transfer_records_only_what_it_received(monkeypatch, tmp_path):
    """A run cut short must not record bytes it never got, from either site."""
    chunks = chunks_totalling(8 * 1024 * 1024, 1024 * 1024)
    fatal_control = RunFatalControl()
    state = {"seen": 0}

    def trip_after_two_chunks():
        state["seen"] += 1
        if state["seen"] > 2:
            fatal_control.report_disk_full(str(tmp_path), 1)

    (ok, status, _), bytes_acc, _, delivered, control_set = run_download(
        monkeypatch,
        tmp_path,
        chunks,
        before_chunk=trip_after_two_chunks,
        fatal_control=fatal_control,
        abort_probe=fatal_control.is_set,
    )

    # The oracle is observed rather than modelled: the fake recorded whether the
    # fatal control was already set as it handed each chunk over, so the deque
    # must hold exactly the chunks handed over while it was still clear. A
    # prefix check would have accepted under-recording too.
    accounted = [chunk for chunk, stop in zip(delivered, control_set) if not stop]
    assert ok is False
    assert status == "aborted_disk_full"
    assert len(accounted) >= 2, "the control must trip after some real progress"
    assert len(delivered) > len(accounted), "the run must have been cut short"
    assert [size for _, size in bytes_acc] == [len(chunk) for chunk in accounted]


@pytest.mark.parametrize("fraction", [8, 4, 2])
def test_small_transfers_are_never_double_counted(monkeypatch, tmp_path, fraction):
    """Every size below WRITE_BUF took the doubling path, so sweep the range.

    Sizes are fractions of the buffer rather than fixed megabytes, so the sweep
    stays below it whatever `WRITE_BUF` is set to.
    """
    total = max(1, moon_download.WRITE_BUF // fraction)
    chunks = chunks_totalling(total, max(1, total // 4))
    _, bytes_acc, dest, delivered, _ = run_download(monkeypatch, tmp_path, chunks)

    assert recorded(bytes_acc) == total
    assert os.path.getsize(dest) == total
