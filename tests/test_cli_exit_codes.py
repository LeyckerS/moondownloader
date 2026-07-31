"""Exit codes for unattended moon_cli runs (issue #32).

| Code | Meaning |
|:--|:--|
| 0 | every file completed |
| 1 | run finished, ≥1 file failed |
| 2 | pre-flight (bad/missing URL file) — no network |

Network is stubbed the same way as test_no_chrome: we never hit the real wire.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
from unittest import mock

import moon_cli

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _write_urls(path: pathlib.Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_preflight_missing_urls_file_exits_2():
    code = moon_cli.main(["--urls", "/no/such/urls.txt", "--output", "/tmp/out"])
    assert code == 2


def test_preflight_empty_urls_file_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        urls = pathlib.Path(tmp) / "empty.txt"
        _write_urls(urls, ["# only comments", ""])
        out = pathlib.Path(tmp) / "out"
        code = moon_cli.main(["--urls", str(urls), "--output", str(out)])
        assert code == 2


def test_all_success_exits_0(monkeypatch):
    async def fake_run(urls, *args, **kwargs):
        assert len(urls) == 2
        return 0  # fail_count

    monkeypatch.setattr(moon_cli, "run", fake_run)
    with tempfile.TemporaryDirectory() as tmp:
        urls = pathlib.Path(tmp) / "ok.txt"
        _write_urls(urls, [
            "https://fuckingfast.co/a#a.zip",
            "https://fuckingfast.co/b#b.zip",
        ])
        out = pathlib.Path(tmp) / "out"
        code = moon_cli.main(["--urls", str(urls), "--output", str(out)])
        assert code == 0


def test_partial_failure_exits_1(monkeypatch):
    async def fake_run(urls, *args, **kwargs):
        # One dead link among the batch — fail_count=1, rest still attempted.
        assert len(urls) == 3
        return 1

    monkeypatch.setattr(moon_cli, "run", fake_run)
    with tempfile.TemporaryDirectory() as tmp:
        urls = pathlib.Path(tmp) / "mixed.txt"
        _write_urls(urls, [
            "https://fuckingfast.co/a#a.zip",
            "https://example.invalid/dead",
            "https://fuckingfast.co/c#c.zip",
        ])
        out = pathlib.Path(tmp) / "out"
        code = moon_cli.main(["--urls", str(urls), "--output", str(out)])
        assert code == 1


def test_run_returns_fail_count_not_none():
    """Contract: run() must return fail_count for main() exit mapping."""
    import inspect
    src = inspect.getsource(moon_cli.run)
    assert "return fail_count" in src
