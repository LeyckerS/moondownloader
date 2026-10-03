"""Test: the live speed and the final average for one file are in the same unit.

Regression test for #84. `moon_download.py` divided the same byte stream by
1_048_576 when it published `rec.live_mbs` while the transfer ran, and by 1e6
when it recorded `rec.avg_mbs` at the end. Both are printed as "MB/s", so a file
downloading at a perfectly steady rate showed one number while it ran and a
4.8576% higher number the moment it completed.

`download_file` reads `time.monotonic()` at exactly three places -- once for
`dl_t0`, once per received chunk, and once for the final `dl_s` -- so the clock
below is scripted and every transfer below takes an exact, reproducible amount
of time. Every expected number is derived from the documented rule

    live_mbs = bytes received in the trailing 3.0 s / min(3.0, elapsed) / 1e6
    avg_mbs  = (bytes received - bytes resumed) / elapsed / 1e6

rather than from whatever the code happens to print. Nothing here sleeps and no
assertion needs a loose tolerance.
"""
from __future__ import annotations

import ast
import asyncio
import collections
import pathlib
import time

import pytest

import moon_download
from moon_download import FileRecord, download_file


MB = 1_000_000
STEADY = (1_000_000,) * 8        # 8 MB
STEADY_STEP = 0.25              # one chunk every 0.25 s -> 2.0 s in total


class ScriptedClock:
    """Stands in for the `time` module inside moon_download only.

    `monkeypatch.setattr(moon_download, "time", ...)` is used rather than
    patching `time.monotonic` globally so asyncio keeps its real clock.
    """

    def __init__(self, times: list[float]):
        self._times = times
        self.calls = 0

    def monotonic(self) -> float:
        value = self._times[min(self.calls, len(self._times) - 1)]
        self.calls += 1
        return value

    def __getattr__(self, name):
        return getattr(time, name)


def _arrivals(delays: list[float], t0: float = 1000.0) -> list[float]:
    """Clock reading at which each chunk lands, given the gaps between them."""
    out, t = [], t0
    for gap in delays:
        t += gap
        out.append(t)
    return out


def _clock(delays: list[float], t0: float = 1000.0) -> ScriptedClock:
    """dl_t0, one reading per chunk, then the final reading.

    The last reading repeats the arrival time of the final chunk: the transfer
    is complete at that instant, so the total elapsed span starts and ends at
    the same points as the bytes it covers.
    """
    arrivals = _arrivals(delays, t0)
    return ScriptedClock([t0] + arrivals + [arrivals[-1]])


class FakeChunkIter:
    """Yields the scheduled chunk sizes to r.content.iter_chunked()."""

    def __init__(self, sizes: list[int]):
        self._sizes = list(sizes)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._sizes:
            raise StopAsyncIteration
        return b"\0" * self._sizes.pop(0)


class FakeContent:
    def __init__(self, sizes: list[int]):
        self._sizes = sizes

    def iter_chunked(self, size):
        return FakeChunkIter(self._sizes)


class FakeResponse:
    def __init__(self, status: int, sizes: list[int], headers: dict):
        self.status = status
        self.headers = headers
        self.content = FakeContent(sizes)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers one request. 206 is what a resume-aware server returns."""

    def __init__(self, status: int, sizes: list[int], headers: dict):
        self._status = status
        self._sizes = sizes
        self._headers = headers

    def get(self, *args, **kwargs):
        return FakeResponse(self._status, self._sizes, self._headers)


def _install(
    monkeypatch,
    delays: list[float],
    sizes: list[int],
    status: int = 200,
    already_on_disk: int = 0,
) -> ScriptedClock:
    """Stub the proxy pool, the session and the clock, and return the clock."""
    served = sum(sizes)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(
        moon_download,
        "_sess",
        lambda: FakeSession(status, sizes, {"Content-Length": str(served)}),
    )
    clock = _clock(delays)
    monkeypatch.setattr(moon_download, "time", clock)
    return clock


def _run(
    monkeypatch,
    tmp_path,
    delays: list[float] | None = None,
    sizes: tuple[int, ...] = STEADY,
    status: int = 200,
    already_on_disk: int = 0,
) -> FileRecord:
    """Drive download_file over a fake response on a scripted clock."""
    delays = delays if delays is not None else [STEADY_STEP] * len(sizes)
    assert len(delays) == len(sizes)
    clock = _install(monkeypatch, delays, list(sizes), status, already_on_disk)

    if already_on_disk:
        (tmp_path / "file.bin.tmp").write_bytes(b"\0" * already_on_disk)

    rec = FileRecord(url="http://example.com/file.bin", filename="file.bin")
    ok, result, _resume = asyncio.run(download_file(
        proxy_url="http://example.com/file.bin",
        cookies="",
        dest=str(tmp_path / "file.bin"),
        rec=rec,
        bytes_acc=collections.deque(),
        kill_evt=asyncio.Event(),
        kills_so_far=0,
        telem=None,
    ))
    assert (ok, result) == (True, "ok")
    assert rec.done_bytes == sum(sizes) + already_on_disk
    assert clock.calls == len(sizes) + 2, f"clock drifted: {clock.calls} readings"
    return rec


# --- the unit itself -------------------------------------------------------


def test_live_and_final_speed_agree_for_a_steady_transfer(monkeypatch, tmp_path):
    # 8 MB in 2.0 s of scripted time, arriving at an unchanging 1 MB per chunk.
    # The rolling window covers the whole transfer, so both spans are 2.0 s and
    # both numerators are 8 MB: the two numbers must be identical, and 4.0.
    rec = _run(monkeypatch, tmp_path)

    assert rec.live_mbs == pytest.approx(rec.avg_mbs), (
        f"live {rec.live_mbs} != final {rec.avg_mbs}"
    )
    assert rec.live_mbs == pytest.approx(4.0)
    assert rec.avg_mbs == pytest.approx(4.0)


def test_agreement_does_not_depend_on_the_transfer_length(monkeypatch, tmp_path):
    # Two chunks is the shortest transfer that still publishes once. 2 MB over
    # 0.5 s is 4 MB/s, the same rate as the eight-chunk case above.
    rec = _run(monkeypatch, tmp_path, sizes=(MB, MB))

    assert rec.live_mbs == pytest.approx(rec.avg_mbs)
    assert rec.live_mbs == pytest.approx(4.0)


def test_chunk_granularity_does_not_change_the_rate(monkeypatch, tmp_path):
    # Same eight steps over the same 2.0 s as the first test, but half as many
    # bytes in each: 4 MB instead of 8 MB, so 2.0 MB/s rather than 4.0. The rate
    # follows from the total, not from how the bytes were chopped up.
    rec = _run(monkeypatch, tmp_path, sizes=(500_000,) * 8)

    assert rec.live_mbs == pytest.approx(rec.avg_mbs)
    assert rec.live_mbs == pytest.approx(2.0)


def test_short_transfer_uses_the_window_it_actually_has(monkeypatch, tmp_path):
    # 1 MB in a single 0.3 s window, shorter than the 0.25 s publish floor plus
    # change. The window is 0.3 s -- not clamped up to some minimum -- so the
    # live rate is 1 MB / 0.3 s, and the total span is the same 0.3 s.
    rec = _run(monkeypatch, tmp_path, delays=[0.3], sizes=(MB,))

    assert rec.live_mbs == pytest.approx(MB / 0.3 / MB)
    assert rec.avg_mbs == pytest.approx(MB / 0.3 / MB)


# --- the rolling window, which is a separate thing from the unit ------------


def test_live_speed_is_the_trailing_three_second_average(monkeypatch, tmp_path):
    # 8 MB over 4.0 s, one chunk every 0.5 s. The window holds only the last
    # 3.0 s of it, so chunks landing before t=1.0 are dropped and 7 MB are
    # averaged over a span clamped to 3.0 s. The final average is over the
    # whole file, so here the two are *meant* to differ.
    rec = _run(monkeypatch, tmp_path, delays=[0.5] * 8)

    assert rec.live_mbs == pytest.approx(7 * MB / 3.0 / MB)
    assert rec.avg_mbs == pytest.approx(8 * MB / 4.0 / MB)
    assert rec.live_mbs != pytest.approx(rec.avg_mbs)


def test_a_quiet_final_chunk_does_not_move_the_published_average(monkeypatch, tmp_path):
    # 4 MB with a 0.05 s gap before the last chunk, which is below the 0.25 s
    # publish interval. The last sample therefore stands at the third chunk:
    # 3 MB measured over the 0.9 s that had elapsed by then. The fourth chunk
    # arrives too late to be counted, so the live figure lags the final one
    # rather than tracking it.
    rec = _run(monkeypatch, tmp_path, delays=[0.3, 0.3, 0.3, 0.05], sizes=(MB,) * 4)

    assert rec.live_mbs == pytest.approx(3 * MB / 0.9 / MB)
    assert rec.avg_mbs == pytest.approx(4 * MB / 0.95 / MB)


# --- resume ----------------------------------------------------------------


def test_resume_offsets_the_average_but_not_the_live_window(monkeypatch, tmp_path):
    # 1 MB already on disk, answered with 206 so the Range header is honoured.
    # `net` excludes the resumed bytes while the rolling window only ever saw
    # the newly arrived ones, so both still have to read as MB/s.
    rec = _run(monkeypatch, tmp_path, status=206, already_on_disk=MB)

    assert not rec.notes, f"the resume was not honoured: {rec.notes}"
    assert rec.file_bytes == 9 * MB
    assert rec.live_mbs == pytest.approx(4.0)
    assert rec.avg_mbs == pytest.approx(4.0)


# --- the convention itself -------------------------------------------------


def test_only_one_speed_divisor_in_the_download_module():
    """Guard the convention, so a second unit cannot creep back in unnoticed."""
    source = pathlib.Path(moon_download.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    divisors: dict[str, set[float]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.BinOp):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Attribute)]
        if not targets or not targets[0].attr.endswith("_mbs"):
            continue
        found = {
            b.right.value
            for b in ast.walk(node.value)
            if isinstance(b, ast.BinOp)
            and isinstance(b.op, ast.Div)
            and isinstance(b.right, ast.Constant)
            and isinstance(b.right.value, (int, float))
            and not isinstance(b.right.value, bool)
        }
        divisors.setdefault(targets[0].attr, set()).update(found)

    assert divisors, "no *_mbs assignment found; this test needs updating"
    for name, found in sorted(divisors.items()):
        assert found == {1e6}, f"{name} is divided by {sorted(found)}, not 1e6"
