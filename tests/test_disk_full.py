"""Regression coverage for run-level ENOSPC handling (#116)."""
from __future__ import annotations

import asyncio
import builtins
import collections
import errno
import json
import os

import moon_cli
import moon_download
import moon_engine
from moon_download import FileRecord, RunFatalControl, download_file


class _Chunks:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Response:
    def __init__(self, status=206, content_length=90, chunks=(b"x" * 20,)):
        self.status = status
        self.headers = {"Content-Length": str(content_length)}
        self.content = type("Content", (), {
            "iter_chunked": lambda _self, _size: _Chunks(chunks),
        })()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, response):
        self._response = response

    def get(self, *_args, **_kwargs):
        return self._response


class _NoSpaceFile:
    def write(self, _data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def close(self):
        pass


def test_download_file_reports_errno_enospc_and_preserves_tmp(monkeypatch, tmp_path):
    """A write ENOSPC creates the shared fatal reason and keeps the partial file."""
    dest = tmp_path / "file.bin"
    partial = dest.with_suffix(".bin.tmp")
    partial.write_bytes(b"p" * 10)

    def no_space_open(path, mode="r", *args, **kwargs):
        assert os.fspath(path) == os.fspath(partial)
        assert mode == "ab"
        assert kwargs["buffering"] == 0
        return _NoSpaceFile()

    monkeypatch.setattr(moon_download, "open", no_space_open, raising=False)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: _Session(_Response()))
    control = RunFatalControl()
    events = []
    rec = FileRecord("https://example.invalid/one", "file.bin")

    ok, result, _ = asyncio.run(download_file(
        "https://download.invalid/one", "", str(dest), rec, collections.deque(),
        asyncio.Event(), 0, fatal_control=control,
        on_event=lambda message, tag: events.append((message, tag)),
    ))

    assert (ok, result) == (False, "disk_full")
    assert partial.read_bytes() == b"p" * 10
    assert control.disk_full is not None
    assert control.disk_full.folder == str(tmp_path)
    assert control.disk_full.needed_bytes == 90
    disk_events = [message for message, _tag in events if message.startswith("Disk full")]
    assert len(disk_events) == 1
    assert str(tmp_path) in disk_events[0]
    assert "90" in disk_events[0]

    peer = FileRecord("https://example.invalid/two", "two.bin")
    peer_result = asyncio.run(download_file(
        "https://download.invalid/two", "", str(tmp_path / "two.bin"), peer,
        collections.deque(), asyncio.Event(), 0, fatal_control=control,
    ))
    assert peer_result == (False, "aborted_disk_full", 0)


def test_timeout_retries_before_returning_timeout(monkeypatch, tmp_path):
    """TimeoutError stays on the existing retry path despite inheriting OSError."""
    attempts = []

    class TimeoutResponse:
        async def __aenter__(self):
            attempts.append("request")
            raise asyncio.TimeoutError()

        async def __aexit__(self, *exc):
            return False

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(moon_download, "DL_INNER_RETRIES", 2)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(moon_download, "_sess", lambda: _Session(TimeoutResponse()))
    monkeypatch.setattr(moon_download.asyncio, "sleep", no_sleep)
    events = []

    result = asyncio.run(download_file(
        "https://download.invalid/timeout", "", str(tmp_path / "timeout.bin"),
        FileRecord("timeout", "timeout.bin"), collections.deque(), asyncio.Event(), 0,
        on_event=lambda message, _tag: events.append(message),
    ))

    assert result == (False, "timeout", 0)
    assert attempts == ["request", "request"]
    assert events == ["timeout att 1", "timeout att 2"]


def test_short_unbuffered_writes_persist_the_full_payload(monkeypatch, tmp_path):
    """A partial FileIO.write result is retried until the chunk is complete."""
    dest = tmp_path / "short.bin"
    writes = []
    original_open = builtins.open

    class ShortFile:
        def __init__(self, raw):
            self._raw = raw

        def write(self, data):
            data = bytes(data)
            writes.append(data)
            return self._raw.write(data[:2])

        def close(self):
            self._raw.close()

    def short_open(path, mode="r", *args, **kwargs):
        assert os.fspath(path) == f"{dest}.tmp"
        assert mode == "wb"
        assert kwargs["buffering"] == 0
        return ShortFile(original_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(moon_download, "WRITE_BUF", 1)
    monkeypatch.setattr(moon_download, "open", short_open, raising=False)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(
        moon_download, "_sess", lambda: _Session(_Response(status=200, content_length=6, chunks=(b"abcdef",))),
    )

    result = asyncio.run(download_file(
        "https://download.invalid/short", "", str(dest),
        FileRecord("short", "short.bin"), collections.deque(), asyncio.Event(), 0,
    ))

    assert result[0] is True
    assert writes == [b"abcdef", b"cdef", b"ef"]
    assert dest.read_bytes() == b"abcdef"


def test_short_write_then_enospc_reports_actual_remaining_bytes(monkeypatch, tmp_path):
    """Known content length wins over the original buffer when a write is partial."""
    dest = tmp_path / "partial.bin"
    original_open = builtins.open

    class PartialThenFull:
        def __init__(self, raw):
            self._raw = raw
            self._writes = 0

        def write(self, data):
            self._writes += 1
            if self._writes == 1:
                return self._raw.write(bytes(data)[:2])
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            self._raw.close()

    def partial_open(path, mode="r", *args, **kwargs):
        assert os.fspath(path) == f"{dest}.tmp"
        assert mode == "wb"
        assert kwargs["buffering"] == 0
        return PartialThenFull(original_open(path, mode, *args, **kwargs))

    control = RunFatalControl()
    monkeypatch.setattr(moon_download, "WRITE_BUF", 1)
    monkeypatch.setattr(moon_download, "open", partial_open, raising=False)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    monkeypatch.setattr(
        moon_download, "_sess", lambda: _Session(_Response(status=200, content_length=6, chunks=(b"abcdef",))),
    )

    result = asyncio.run(download_file(
        "https://download.invalid/partial", "", str(dest),
        FileRecord("partial", "partial.bin"), collections.deque(), asyncio.Event(), 0,
        fatal_control=control,
    ))

    assert result[1] == "disk_full"
    assert (tmp_path / "partial.bin.tmp").read_bytes() == b"ab"
    assert control.disk_full is not None
    assert control.disk_full.needed_bytes == 4


def test_active_peer_unwinds_after_shared_enospc(monkeypatch, tmp_path):
    """Two live streams share the first ENOSPC without peer writes after it."""
    monkeypatch.setattr(moon_download, "WRITE_BUF", 1)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    control = RunFatalControl()
    entered = set()
    both_streaming = asyncio.Event()
    writes = {"bad": [], "peer": []}
    original_open = builtins.open
    bad_tmp = tmp_path / "bad.bin.tmp"
    peer_tmp = tmp_path / "peer.bin.tmp"
    bad_tmp.write_bytes(b"bad")
    peer_tmp.write_bytes(b"peer")

    class CoordinatedContent:
        def __init__(self, name):
            self._name = name

        async def iter_chunked(self, _size):
            entered.add(self._name)
            if len(entered) == 2:
                both_streaming.set()
            await both_streaming.wait()
            if self._name == "bad":
                yield b"boom"
            else:
                while not control.is_set():
                    await asyncio.sleep(0)
                yield b"peer"

    class CoordinatedResponse:
        status = 206
        headers = {"Content-Length": "10"}

        def __init__(self, name):
            self.content = CoordinatedContent(name)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class CoordinatedSession:
        def get(self, url, **_kwargs):
            return CoordinatedResponse("bad" if url.endswith("bad") else "peer")

    class TrackingFile:
        def __init__(self, name, raw):
            self._name = name
            self._raw = raw

        def write(self, data):
            writes[self._name].append(bytes(data))
            if self._name == "bad":
                raise OSError(errno.ENOSPC, "No space left on device")
            return self._raw.write(data)

        def close(self):
            self._raw.close()

    def tracked_open(path, mode="r", *args, **kwargs):
        path = os.fspath(path)
        assert mode == "ab"
        assert kwargs["buffering"] == 0
        if path == os.fspath(bad_tmp):
            return TrackingFile("bad", original_open(path, mode, *args, **kwargs))
        if path == os.fspath(peer_tmp):
            return TrackingFile("peer", original_open(path, mode, *args, **kwargs))
        raise AssertionError(f"unexpected file open: {path}")

    monkeypatch.setattr(moon_download, "open", tracked_open, raising=False)
    monkeypatch.setattr(moon_download, "_sess", lambda: CoordinatedSession())
    events = []
    async def run_both():
        return await asyncio.gather(
            download_file("https://download.invalid/bad", "", str(tmp_path / "bad.bin"),
                          FileRecord("bad", "bad.bin"), collections.deque(), asyncio.Event(), 0,
                          fatal_control=control, on_event=lambda message, _tag: events.append(message)),
            download_file("https://download.invalid/peer", "", str(tmp_path / "peer.bin"),
                          FileRecord("peer", "peer.bin"), collections.deque(), asyncio.Event(), 0,
                          fatal_control=control),
        )

    results = asyncio.run(run_both())

    assert results[0][1] == "disk_full"
    assert results[1] == (False, "aborted_disk_full", 4)
    assert entered == {"bad", "peer"}
    assert writes == {"bad": [b"boom"], "peer": []}
    assert bad_tmp.read_bytes() == b"bad"
    assert peer_tmp.read_bytes() == b"peer"
    assert [message for message in events if message.startswith("Disk full")] == [
        f"Disk full in {tmp_path}: need 10 bytes to continue",
    ]
    assert control.disk_full is not None
    assert control.disk_full.folder == str(tmp_path)
    assert control.disk_full.needed_bytes == 10


def test_network_exception_after_peer_enospc_is_cooperative_abort(monkeypatch, tmp_path):
    """A peer exception after ENOSPC must not become a second ordinary failure."""
    monkeypatch.setattr(moon_download, "WRITE_BUF", 1)
    monkeypatch.setattr(moon_download._PROXY_POOL, "next", lambda: None)
    control = RunFatalControl()
    entered = set()
    both_streaming = asyncio.Event()
    original_open = builtins.open
    writes = {"bad": [], "peer": []}

    class RaceContent:
        def __init__(self, name):
            self._name = name

        async def iter_chunked(self, _size):
            entered.add(self._name)
            if len(entered) == 2:
                both_streaming.set()
            await both_streaming.wait()
            if self._name == "bad":
                yield b"boom"
            else:
                while not control.is_set():
                    await asyncio.sleep(0)
                raise moon_download.aiohttp.ClientPayloadError("peer dropped")

    class RaceResponse:
        status = 200
        headers = {"Content-Length": "6"}

        def __init__(self, name):
            self.content = RaceContent(name)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class RaceSession:
        def get(self, url, **_kwargs):
            return RaceResponse("bad" if url.endswith("bad") else "peer")

    class RaceFile:
        def __init__(self, name, raw):
            self._name = name
            self._raw = raw

        def write(self, data):
            writes[self._name].append(bytes(data))
            if self._name == "bad":
                raise OSError(errno.ENOSPC, "No space left on device")
            return self._raw.write(data)

        def close(self):
            self._raw.close()

    def race_open(path, mode="r", *args, **kwargs):
        path = os.fspath(path)
        assert mode == "wb"
        assert kwargs["buffering"] == 0
        name = "bad" if path.endswith("bad.bin.tmp") else "peer"
        return RaceFile(name, original_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(moon_download, "open", race_open, raising=False)
    monkeypatch.setattr(moon_download, "_sess", lambda: RaceSession())
    events = []

    async def run_both():
        return await asyncio.gather(
            download_file("https://download.invalid/bad", "", str(tmp_path / "bad.bin"),
                          FileRecord("bad", "bad.bin"), collections.deque(), asyncio.Event(), 0,
                          fatal_control=control, on_event=lambda message, _tag: events.append(message)),
            download_file("https://download.invalid/peer", "", str(tmp_path / "peer.bin"),
                          FileRecord("peer", "peer.bin"), collections.deque(), asyncio.Event(), 0,
                          fatal_control=control),
        )

    results = asyncio.run(run_both())

    assert results[0][1] == "disk_full"
    assert results[1] == (False, "aborted_disk_full", 0)
    assert entered == {"bad", "peer"}
    assert writes == {"bad": [b"boom"], "peer": []}
    assert [message for message in events if message.startswith("Disk full")] == [
        f"Disk full in {tmp_path}: need 6 bytes to continue",
    ]


class _Gate:
    def __init__(self, *_args, **_kwargs):
        pass

    async def get(self):
        return None

    async def aclose(self):
        pass


async def _fake_extract(url, _get_browser):
    return f"https://download.invalid/{url.rsplit('/', 1)[-1]}"


async def _noop():
    pass


def _fatal_download(events, calls, completed):
    active = set()
    both_active = asyncio.Event()

    async def fake_download(_proxy_url, _cookies, dest, rec, _bytes_acc, _kill_evt,
                            _kills_so_far, telem=None, on_event=None, *, fatal_control=None):
        calls.append(rec.url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with builtins.open(dest + ".tmp", "wb") as f:
            f.write(rec.filename.encode())
        active.add(rec.url)
        if len(active) == 2:
            both_active.set()
        await both_active.wait()
        if rec.url.endswith("file-one.bin"):
            await asyncio.sleep(0)
            disk_full = fatal_control.report_disk_full(os.path.dirname(dest), 77)
            message = f"Disk full in {disk_full.folder}: need {disk_full.needed_bytes} bytes to continue"
            events.append(message)
            if on_event:
                on_event(message, "fail")
            completed.append(rec.url)
            return False, "disk_full", 0
        while not fatal_control.is_set():
            await asyncio.sleep(0)
        completed.append(rec.url)
        return False, "aborted_disk_full", 0

    return fake_download


def _patch_frontend(monkeypatch, module, tmp_path, events, calls, completed):
    monkeypatch.setattr(module, "BrowserGate", _Gate)
    monkeypatch.setattr(module, "extract_fuckingfast", _fake_extract)
    monkeypatch.setattr(module, "download_file", _fatal_download(events, calls, completed))
    monkeypatch.setattr(module, "_close_sess", _noop)
    monkeypatch.setattr(module, "close_ff_session", _noop)
    monkeypatch.setattr(module._PROXY_POOL, "close_all", _noop)
    monkeypatch.setattr(module, "__file__", str(tmp_path / f"{module.__name__}.py"))


def _reject_retry_file(monkeypatch, module):
    original_open = builtins.open

    def retry_file_full(path, mode="r", *args, **kwargs):
        if os.path.basename(os.fspath(path)) == "failed_links.txt" and "w" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(module, "open", retry_file_full, raising=False)


def _frontend_urls():
    return [
        "https://fuckingfast.co/one/file-one.bin",
        "https://fuckingfast.co/two/file-two.bin",
        "https://fuckingfast.co/three/file-three.bin",
    ]


def test_engine_stops_queue_and_waits_for_downloads(monkeypatch, tmp_path):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_engine, tmp_path, events, calls, completed)
    engine = moon_engine.Engine()
    engine._cfg["out_folder"] = str(tmp_path / "downloads")
    urls = _frontend_urls()

    asyncio.run(engine._run(urls, 2, 2, 2))

    failed = (tmp_path / "failed_links.txt").read_text(encoding="utf-8").splitlines()
    snapshot = engine.snapshot(0)
    assert set(calls) == set(urls[:2])
    assert urls[2] not in calls
    assert set(completed) == set(urls[:2])
    assert failed == [urls[0]]
    assert snapshot["metrics"]["fail"] == 1
    assert snapshot["metrics"]["active"] == 0
    assert snapshot["metrics"]["stage"] == "done"
    assert snapshot["state"] == "done"
    assert len(events) == 1 and str(tmp_path / "downloads") in events[0]
    assert (tmp_path / "downloads" / "file-one.bin.tmp").exists()
    assert (tmp_path / "downloads" / "file-two.bin.tmp").exists()
    report = json.loads(next(tmp_path.glob("moontech_*.json")).read_text(encoding="utf-8"))
    assert report["session"]["fail"] == 1
    assert any(rec.status == "aborted" for rec in engine._tracked.values())
    assert not any(file["state"] in ("extract", "download") for file in snapshot["files"])
    assert not any("✓  Done" in message for message, _tag in snapshot["log"])


def test_cli_stops_queue_and_records_only_triggering_url(monkeypatch, tmp_path, capsys):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_cli, tmp_path, events, calls, completed)
    urls = _frontend_urls()

    asyncio.run(moon_cli.run(urls, str(tmp_path / "downloads"), 2, 2, 2, "proxies.txt"))

    failed = (tmp_path / "failed_links.txt").read_text(encoding="utf-8").splitlines()
    output = capsys.readouterr().out
    assert set(calls) == set(urls[:2])
    assert urls[2] not in calls
    assert set(completed) == set(urls[:2])
    assert failed == [urls[0]]
    assert len(events) == 1 and str(tmp_path / "downloads") in events[0]
    assert (tmp_path / "downloads" / "file-one.bin.tmp").exists()
    assert (tmp_path / "downloads" / "file-two.bin.tmp").exists()
    report = json.loads(next(tmp_path.glob("moontech_cli_*.json")).read_text(encoding="utf-8"))
    assert report["fail"] == 1
    assert "Run stopped: disk full" in output
    assert "Done in" not in output


def test_engine_retry_file_enospc_keeps_disk_full_outcome(monkeypatch, tmp_path):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_engine, tmp_path, events, calls, completed)
    _reject_retry_file(monkeypatch, moon_engine)
    engine = moon_engine.Engine()
    engine._cfg["out_folder"] = str(tmp_path / "downloads")

    asyncio.run(engine._run(_frontend_urls(), 2, 2, 2))

    snapshot = engine.snapshot(0)
    messages = [message for message, _tag in snapshot["log"]]
    assert snapshot["state"] == "done"
    assert snapshot["metrics"]["fail"] == 1
    assert any("Failed links save error" in message for message in messages)
    assert any(message.startswith("\nRun stopped: disk full") for message in messages)
    assert list(tmp_path.glob("moontech_*.json"))
    assert events == [f"Disk full in {tmp_path / 'downloads'}: need 77 bytes to continue"]


def test_cli_retry_file_enospc_keeps_disk_full_outcome(monkeypatch, tmp_path, capsys):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_cli, tmp_path, events, calls, completed)
    _reject_retry_file(monkeypatch, moon_cli)

    asyncio.run(moon_cli.run(_frontend_urls(), str(tmp_path / "downloads"), 2, 2, 2, "proxies.txt"))

    output = capsys.readouterr().out
    assert "[warn] Failed links save error" in output
    assert "Run stopped: disk full" in output
    assert list(tmp_path.glob("moontech_cli_*.json"))
    assert events == [f"Disk full in {tmp_path / 'downloads'}: need 77 bytes to continue"]


def _install_backoff_fatal(monkeypatch, module, tmp_path):
    controls = []

    class CapturingControl(RunFatalControl):
        def __init__(self):
            super().__init__()
            controls.append(self)

    async def fatal_sleep(_seconds):
        controls[-1].report_disk_full(str(tmp_path / "downloads"), 33)

    monkeypatch.setattr(module, "RunFatalControl", CapturingControl)
    monkeypatch.setattr(module.asyncio, "sleep", fatal_sleep)
    return controls


def test_engine_does_not_enqueue_retry_after_fatal_backoff(monkeypatch, tmp_path):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_engine, tmp_path, events, calls, completed)
    controls = _install_backoff_fatal(monkeypatch, moon_engine, tmp_path)
    extract_calls = []

    async def no_link(url, _get_browser):
        extract_calls.append(url)
        return None

    monkeypatch.setattr(moon_engine, "extract_fuckingfast", no_link)
    engine = moon_engine.Engine()
    engine._cfg["out_folder"] = str(tmp_path / "downloads")
    url = "https://fuckingfast.co/retry/file.bin"
    asyncio.run(engine._run([url], 2, 2, 2))

    snapshot = engine.snapshot(0)
    assert extract_calls == [url]
    assert controls[0].disk_full is not None
    assert snapshot["metrics"]["stage"] == "done"
    assert engine._tracked[url].status == "aborted"
    assert not any(file["state"] in ("extract", "download") for file in snapshot["files"])


def test_cli_does_not_enqueue_retry_after_fatal_backoff(monkeypatch, tmp_path, capsys):
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_cli, tmp_path, events, calls, completed)
    controls = _install_backoff_fatal(monkeypatch, moon_cli, tmp_path)
    extract_calls = []

    async def no_link(url, _get_browser):
        extract_calls.append(url)
        return None

    monkeypatch.setattr(moon_cli, "extract_fuckingfast", no_link)
    url = "https://fuckingfast.co/retry/file.bin"
    asyncio.run(moon_cli.run([url], str(tmp_path / "downloads"), 2, 2, 2, "proxies.txt"))

    assert extract_calls == [url]
    assert controls[0].disk_full is not None
    assert "Run stopped: disk full" in capsys.readouterr().out


def test_cli_observes_completed_download_task_errors(monkeypatch, tmp_path):
    """Done tasks are gathered too, so their exceptions do not reach the loop handler."""
    events, calls, completed = [], [], []
    _patch_frontend(monkeypatch, moon_cli, tmp_path, events, calls, completed)

    async def exploding_download(_proxy_url, _cookies, dest, _rec, _bytes_acc, _kill_evt,
                                 _kills_so_far, telem=None, on_event=None, *, fatal_control=None):
        fatal_control.report_disk_full(os.path.dirname(dest), 11)
        raise RuntimeError("already-completed task")

    monkeypatch.setattr(moon_cli, "download_file", exploding_download)
    loop_errors = []

    async def run_cli():
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        try:
            await moon_cli.run(
                ["https://fuckingfast.co/error/file.bin"], str(tmp_path / "downloads"),
                2, 2, 2, "proxies.txt")
        finally:
            loop.set_exception_handler(old_handler)

    asyncio.run(run_cli())
    assert loop_errors == []
