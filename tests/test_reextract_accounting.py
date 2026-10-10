"""A stall-killed link whose re-extraction fails must still be counted — #178.

A stall-killed download is re-queued for re-extraction with the same record;
if that re-extraction then fails, the link must reach the run's completion
accounting. On main it does not, and the two front-ends fail differently:

* the **engine** never counts it at all — ``n_done`` stays short of the link
  count, ``all_done`` is never set, the workers never exit and ``run()`` never
  returns. The GUI waits forever.
* the **CLI** never increments ``rec.stall_kills`` on a stall kill (only the
  engine does, at ``moon_engine.py:255``), so its ``is_re`` guard is dead code:
  the re-extraction is treated as a first attempt and burns the full retry
  budget — three extra extraction attempts behind 1s/2s/4s of backoff — before
  finally being counted.

The stall is driven against a loopback aiohttp server that trickles far below
any stall threshold; the constants are shrunk by monkeypatch so production's
"50 MB file, 90 s of grace" fits into a test. Chrome and the network beyond
loopback are not involved: extraction is stubbed at the ``moon_extract``
boundary, exactly as ``tests/conftest.py`` does, and only the real
``download_file`` is exercised — that is where the stall kill lives.
"""
from __future__ import annotations

import asyncio
import contextlib
import pathlib
import threading
import time

from aiohttp import web

import moon_cli
import moon_download
import moon_engine

ROOT = pathlib.Path(__file__).resolve().parents[1]
FF = "https://fuckingfast.co/x1/pack.part01.rar"


def _shrink_stall_constants(monkeypatch):
    """Make the production stall detector fire in ~1 s against the loopback host."""
    # moon_download reads every one of these as a module global at run time.
    for name, value in {
        "RECV_CHUNK": 1024,            # the read loop waits for a full chunk
        "STALL_CHECK_S": 0.2,
        "STALL_GRACE_S": 1.0,
        "STALL_MIN_MBS": 1e9,          # any real speed counts as stalled
        "STALL_MIN_BYTES_IN_WIN": 0,
        "STALL_MIN_FILE_BYTES": 0,
    }.items():
        monkeypatch.setattr(moon_download, name, value)
    # moon_engine keeps its own copy for the banner line and Telemetry cfg.
    monkeypatch.setattr(moon_engine, "STALL_GRACE_S", 1.0)
    # A session is bound to the loop that created it: force a fresh one per test.
    monkeypatch.setattr(moon_download, "_SESSION", None)


class _TrickleHost:
    """A loopback host that answers with a slow trickle: every stall on it."""

    def __init__(self):
        self.requests = 0
        self._ready = threading.Event()
        self._port = 0

    async def _handler(self, request):
        self.requests += 1
        resp = web.StreamResponse(headers={"Content-Length": "65536"})
        await resp.prepare(request)
        try:
            # 5 KB/s forever, until the client gives up on the stall kill.
            for _ in range(120):
                await resp.write(b"x" * 512)
                await asyncio.sleep(0.1)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp

    def start(self) -> str:
        def serve():
            async def _serve():
                app = web.Application()
                app.router.add_route("*", "/{tail:.*}", self._handler)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                self._port = runner.addresses[0][1]
                self._ready.set()
                await asyncio.Event().wait()
            asyncio.run(_serve())

        threading.Thread(target=serve, daemon=True).start()
        assert self._ready.wait(10), "the loopback test host did not start"
        return f"http://127.0.0.1:{self._port}/pack.part01.rar"


def _stub_extractor(monkeypatch, front_end, slow_url: str) -> list:
    """Extraction succeeds once, then fails — the re-extraction of #178."""
    calls: list = []

    async def fake_ff(url, get_browser=None):
        calls.append(url)
        return slow_url if len(calls) == 1 else ""

    monkeypatch.setattr(front_end, "extract_fuckingfast", fake_ff)
    return calls


def _cleanup_reports():
    """Both front-ends write their reports next to the source, not the out folder."""
    for pattern in (
        "moontech_*.log",
        "moontech_*.json",
        "moon_log_*.txt",
        "moon_log_*.json",
        "output_links.txt",
        "failed_links.txt",
    ):
        for path in ROOT.glob(pattern):
            with contextlib.suppress(OSError):
                path.unlink()


def test_engine_counts_a_failed_reextraction_instead_of_hanging(monkeypatch, tmp_path):
    _shrink_stall_constants(monkeypatch)
    slow_url = _TrickleHost().start()
    calls = _stub_extractor(monkeypatch, moon_engine, slow_url)

    engine = moon_engine.Engine()
    try:
        result = engine.start(
            {
                "links": [FF],
                "mode": "download",
                "out_folder": str(tmp_path),
                "workers": 2,
                "dl_streams": 2,
                "retries": 1,
            }
        )
        assert not result.get("error"), f"start refused: {result['error']}"

        deadline = time.monotonic() + 15
        while engine._get("_running") and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not engine._get("_running"), (
            "the run never finished: the failed re-extraction was never counted "
            "and the run waited for it forever"
        )
        metrics = engine.snapshot(0)["metrics"]
        # Counted exactly once, as a failure — not retried, not lost.
        assert metrics["fail"] == 1
        assert metrics["ok"] == 0
        assert metrics["kills"] == 1
        # dl_done stays 0: an extraction failure is not a download, which is the
        # existing convention for a first-attempt extraction failure too.
        assert metrics["dl_done"] == 0
        # One extraction, one re-extraction, no retries in between.
        assert len(calls) == 2
        # The failed link is reported, not silently dropped.
        failed = ROOT / "failed_links.txt"
        assert failed.exists() and FF in failed.read_text(encoding="utf-8")
    finally:
        _cleanup_reports()


def test_cli_counts_a_failed_reextraction_without_burning_retries(monkeypatch, tmp_path):
    _shrink_stall_constants(monkeypatch)
    slow_url = _TrickleHost().start()
    calls = _stub_extractor(monkeypatch, moon_cli, slow_url)

    started = time.monotonic()
    try:
        ok, fail, aborted = asyncio.run(
            asyncio.wait_for(
                moon_cli.run([FF], str(tmp_path), 2, 2, 3, "proxies.txt"),
                timeout=20,
            )
        )
    finally:
        _cleanup_reports()

    elapsed = time.monotonic() - started
    # On main the CLI treats the re-extraction as a fresh attempt: 4 extractions
    # behind 1s + 2s + 4s of backoff before it is counted at all.
    assert len(calls) == 2, f"the re-extraction went through the retry budget: {len(calls)} attempts"
    assert (ok, fail, aborted) == (0, 1, False)
    assert elapsed < 6, f"the run spent {elapsed:.1f}s retrying what is already a re-extraction"
