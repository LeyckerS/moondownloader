"""A stall-killed link must count once in ``extract_done`` — the second half of #178's blast radius.

A stall kill re-queues the *same* link for re-extraction. The engine's
``_browser_worker`` increments ``_url_done`` once per *queue entry*, so a link
that was stall-killed and re-extracted is counted twice against ``_url_total``:
a one-link run reports ``extract_done == 2`` of ``extract_total == 1`` until the
run ends, and the GUI's extraction counter reads 2/1 for every stall kill.

The CLI front-end has never had this bug: its accounting goes through
``mark_extraction``, which has guarded against double counting by record since
it was written (``moon_engine.py:169``). The engine's own inline counter missed
the same guard — the two front-ends disagree about the same run.

The stall is produced exactly as in ``test_reextract_accounting.py``: a
loopback host that trickles below the (monkeypatched) stall threshold once,
then answers in full so the re-extraction can succeed and the link can be
watched all the way to ``ok``.
"""
from __future__ import annotations

import asyncio
import contextlib
import pathlib
import threading
import time

from aiohttp import web

import moon_download
import moon_engine

ROOT = pathlib.Path(__file__).resolve().parents[1]
FF = "https://fuckingfast.co/x1/pack.part01.rar"
BODY = 65536


def _shrink_stall_constants(monkeypatch):
    """Make the production stall detector fire in ~1 s against the loopback host."""
    for name, value in {
        "RECV_CHUNK": 1024,
        "STALL_CHECK_S": 0.2,
        "STALL_GRACE_S": 1.0,
        "STALL_MIN_MBS": 1e9,
        "STALL_MIN_BYTES_IN_WIN": 0,
        "STALL_MIN_FILE_BYTES": 0,
    }.items():
        monkeypatch.setattr(moon_download, name, value)
    monkeypatch.setattr(moon_engine, "STALL_GRACE_S", 1.0)
    monkeypatch.setattr(moon_download, "_SESSION", None)


class _TrickleHost:
    """First request trickles (a stall on every metric); later ones answer in full."""

    def __init__(self):
        self.requests = 0
        self._ready = threading.Event()
        self._port = 0

    async def _handler(self, request):
        self.requests += 1
        first = self.requests == 1
        resp = web.StreamResponse(headers={"Content-Length": str(BODY)})
        await resp.prepare(request)
        try:
            if first:
                # ~5 KB/s forever: far below any stall threshold, forever short
                # of the declared body.
                for _ in range(120):
                    await resp.write(b"x" * 512)
                    await asyncio.sleep(0.1)
            else:
                await resp.write(b"y" * BODY)
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


def _cleanup_reports():
    for pattern in ("moontech_*.log", "moontech_*.json", "output_links.txt", "failed_links.txt"):
        for path in ROOT.glob(pattern):
            with contextlib.suppress(OSError):
                path.unlink()


def test_extract_done_counts_a_stall_reextracted_link_once(monkeypatch, tmp_path):
    _shrink_stall_constants(monkeypatch)
    slow_url = _TrickleHost().start()
    # Extraction always succeeds: the first pass stalls the download, the
    # second finishes it, so the link is watched from extraction to ok.
    async def fake_ff(url, get_browser=None):
        return slow_url
    monkeypatch.setattr(moon_engine, "extract_fuckingfast", fake_ff)

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
        assert not engine._get("_running"), "the run never finished"

        metrics = engine.snapshot(0)["metrics"]
        # The run itself is healthy: one link, one stall kill, one completed file.
        assert metrics["ok"] == 1
        assert metrics["kills"] == 1
        # The link went through extraction twice (kill + re-extract) and must
        # still count once: two queue entries, one link.
        assert metrics["extract_total"] == 1
        assert metrics["extract_done"] == 1
        # And the host was asked for the body exactly twice, as the shape above assumes.
    finally:
        _cleanup_reports()
