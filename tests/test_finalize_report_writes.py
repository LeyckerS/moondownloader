"""A report-write failure must not take the rest of finalisation down with it.

`Engine._run` finishes a batch with three report writes into the app directory:
`telem.save()`, `output_links.txt`, then `failed_links.txt`. The first and third
are wrapped in `try/except OSError`, and the comment above the first says why --
"a report-write failure shouldn't crash finalize".

`output_links.txt` was the one left bare. Any OSError there (read-only install
directory, `Program Files`, the app directory on a full volume, the name already
taken by a directory) escaped `_run`, `_guarded_run` logged it as an engine
crash, and everything after that line never ran: `failed_links.txt` was silently
never written even for a batch with failures, and the completion summary was
lost. `_on_done` still fired, so the GUI showed a normal finished run while the
failure report quietly did not exist.

In `links` mode `output_links` is populated for every successful extraction, so
this write is on the ordinary path of a normal run, not an edge case.
"""
from __future__ import annotations

import asyncio
import os

import moon_engine

OK_URL = "https://fuckingfast.co/ok/file.bin"
# An unsupported host is not retried and is tallied into failed_urls.
BAD_URL = "https://not-a-supported-host.example/x/file.bin"
RESOLVED = "https://dl.invalid/resolved.bin"


class _Gate:
    def __init__(self, *_args, **_kwargs):
        pass

    async def get(self):
        return None

    async def aclose(self):
        pass


async def _noop():
    return None


async def _fake_extract(_url, _get_browser=None):
    return RESOLVED


class _NoSaveTelemetry(moon_engine.Telemetry):
    """The telemetry report is not what is under test; keep the repo clean."""

    def save(self, out_dir):
        return "(stub-log)", "(stub-json)"


def _patch(monkeypatch, module, tmp_path):
    # base = dirname(abspath(__file__)); pointing __file__ into tmp_path keeps
    # every report write inside the test's own directory.
    monkeypatch.setattr(module, "__file__", str(tmp_path / f"{module.__name__}.py"))
    monkeypatch.setattr(module, "BrowserGate", _Gate)
    monkeypatch.setattr(module, "extract_fuckingfast", _fake_extract)
    monkeypatch.setattr(module, "extract_datanodes", _fake_extract)
    monkeypatch.setattr(module, "_close_sess", _noop)
    monkeypatch.setattr(module, "close_ff_session", _noop)
    # staticmethod: as a plain class attribute _noop would bind and get self.
    monkeypatch.setattr(
        module, "_PROXY_POOL", type("_P", (), {"close_all": staticmethod(_noop)})())
    monkeypatch.setattr(module, "Telemetry", _NoSaveTelemetry)


def _links_mode_engine(tmp_path, monkeypatch):
    engine = moon_engine.Engine()
    engine._cfg["mode"] = "links"
    engine._cfg["out_folder"] = str(tmp_path / "downloads")
    return engine


def _run_links_mode(monkeypatch, tmp_path):
    _patch(monkeypatch, moon_engine, tmp_path)
    engine = _links_mode_engine(tmp_path, monkeypatch)
    asyncio.run(engine._run([OK_URL, BAD_URL], 2, 2, 0))
    snapshot = engine.snapshot(0)
    return engine, snapshot


def _log_views(snapshot):
    """(text, warned_text) views of the log.

    appendLog() in web/app.js does `span.className = tag || ""` and styles.css has
    a `.log .warn` rule, so the tag decides whether the failure is visible as a
    warning or as ordinary log text. Both sibling report writes use "warn".
    """
    text = "".join(message for message, _tag in snapshot["log"])
    warned = "".join(message for message, tag in snapshot["log"] if tag == "warn")
    return text, warned


def test_output_links_write_failure_does_not_lose_the_failed_links_report(
        monkeypatch, tmp_path):
    # output_links.txt is a directory, so open(..., "w") raises IsADirectoryError.
    (tmp_path / "output_links.txt").mkdir()

    _engine, snapshot = _run_links_mode(monkeypatch, tmp_path)
    log, warned = _log_views(snapshot)

    assert "engine crash" not in log, (
        "the report write raised out of _run and was reported as an engine "
        "crash, so the rest of finalisation never ran"
    )
    assert "Links save error" in warned, (
        "the write failed but was not reported as a warning -- the failure is "
        "silent, or buried in ordinary log text instead of standing out"
    )
    assert (tmp_path / "failed_links.txt").read_text(
        encoding="utf-8").splitlines() == [BAD_URL]
    assert "Done in" in log, "the completion summary was lost"
    assert "Links → output_links.txt" not in log, (
        "the run announced a successful write of output_links.txt; the file does "
        "not exist, so the log is telling the user the opposite of the truth"
    )


def test_a_write_that_fails_with_another_oserror_is_caught_too(
        monkeypatch, tmp_path):
    # output_links.txt points into a directory that is not there: a symlink to an
    # unplugged external drive, or a synced folder that has since been removed.
    # open() raises FileNotFoundError here, not the IsADirectoryError the other
    # tests provoke, so the guard has to be catching OSError rather than just the
    # one errno that happened to show up first.
    os.symlink(tmp_path / "gone" / "output_links.txt", tmp_path / "output_links.txt")

    _engine, snapshot = _run_links_mode(monkeypatch, tmp_path)
    log, warned = _log_views(snapshot)

    assert "Links save error" in warned, (
        "only IsADirectoryError was guarded; a FileNotFoundError from a dangling "
        "output_links.txt symlink still takes the rest of finalisation down"
    )
    assert "engine crash" not in log
    assert (tmp_path / "failed_links.txt").read_text(
        encoding="utf-8").splitlines() == [BAD_URL]
    assert "Done in" in log


def test_a_completed_batch_still_reports_its_own_tallies_when_the_write_fails(
        monkeypatch, tmp_path):
    (tmp_path / "output_links.txt").mkdir()

    _engine, snapshot = _run_links_mode(monkeypatch, tmp_path)

    # The state machine looked fine even while the report was being lost, which
    # is why this went unnoticed: nothing downstream of the crash complained.
    assert snapshot["state"] == "done"
    assert snapshot["metrics"]["ok"] == 1
    assert snapshot["metrics"]["fail"] == 1


def test_both_report_writes_failing_still_leaves_the_summary(monkeypatch, tmp_path):
    (tmp_path / "output_links.txt").mkdir()
    (tmp_path / "failed_links.txt").mkdir()

    _engine, snapshot = _run_links_mode(monkeypatch, tmp_path)
    log, warned = _log_views(snapshot)

    assert "engine crash" not in log
    assert "Links save error" in warned
    assert "Failed links save error" in warned
    assert "Links → output_links.txt" not in log
    assert "Done in" in log


def test_output_links_is_still_written_when_nothing_is_in_the_way(
        monkeypatch, tmp_path):
    # Behaviour preservation: the ordinary path must be untouched.
    _engine, snapshot = _run_links_mode(monkeypatch, tmp_path)
    log, _warned = _log_views(snapshot)

    assert (tmp_path / "output_links.txt").read_text(
        encoding="utf-8").splitlines() == [RESOLVED]
    assert (tmp_path / "failed_links.txt").exists()
    assert "Links → output_links.txt" in log
    assert "Links save error" not in log
