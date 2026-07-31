"""Regression: only a datanodes.to link may open a browser.

Network, extraction and Chrome are stubbed at the moon_extract level: what is
under test is the dispatcher's decision to launch, not the extractors.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import re
import tempfile
import time

import moon_cli
import moon_engine

ROOT = pathlib.Path(__file__).resolve().parents[1]
FF = [f"https://fuckingfast.co/x{n}/pack.part{n:02d}.rar" for n in range(1, 4)]
DN = ["https://datanodes.to/y1/pack.part99.rar"]
FRONT_ENDS = ("moon_engine.py", "moon_cli.py")
DOWNLOAD_DEFS = (
    "async def download_file",
    "class Telemetry",
    "class ProxyPool",
)


def run_engine(engine, urls) -> dict:
    res = engine.start(
        {
            "links": urls,
            "mode": "links",
            "out_folder": str(ROOT / "_tmp_out"),
            "workers": 4,
            "dl_streams": 4,
            "retries": 1,
        }
    )
    assert not res.get("error"), f"start refused: {res['error']}"

    deadline = time.monotonic() + 25
    while engine._get("_running") and time.monotonic() < deadline:
        time.sleep(0.15)

    metrics = engine.snapshot(0)["metrics"]
    return {"ok": metrics["ok"], "fail": metrics["fail"]}


def run_cli(urls) -> dict:
    with tempfile.TemporaryDirectory() as out:
        asyncio.run(moon_cli.run(urls, out, 4, 4, 1, "proxies.txt"))
        done = len(os.listdir(out))
    return {"ok": len(urls) if done == 0 else done, "fail": 0}


def assert_browser_counts(result, calls, urls, want_browsers: int) -> None:
    assert calls["open_browser"] == want_browsers
    assert calls["playwright"] == want_browsers
    assert calls["close_browser"] + calls["shutdown_chrome"] == want_browsers
    assert result["ok"] == len(urls)


def cleanup() -> None:
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
    with contextlib.suppress(OSError):
        (ROOT / "_tmp_out").rmdir()


def test_engine_fuckingfast_only_launches_no_browser(browser_calls):
    engine = moon_engine.Engine()

    try:
        result = run_engine(engine, FF)
        assert_browser_counts(result, browser_calls, FF, want_browsers=0)
    finally:
        cleanup()


def test_engine_mixed_batch_launches_one_shared_browser(browser_calls):
    engine = moon_engine.Engine()

    try:
        result = run_engine(engine, FF + DN)
        assert_browser_counts(result, browser_calls, FF + DN, want_browsers=1)
    finally:
        cleanup()


def test_cli_fuckingfast_only_launches_no_browser(browser_calls):
    try:
        result = run_cli(FF)
        assert_browser_counts(result, browser_calls, FF, want_browsers=0)
    finally:
        cleanup()


def test_cli_mixed_batch_launches_one_shared_browser(browser_calls):
    try:
        result = run_cli(FF + DN)
        assert_browser_counts(result, browser_calls, FF + DN, want_browsers=1)
    finally:
        cleanup()


def test_front_ends_route_browser_launches_through_browser_gate():
    for name in FRONT_ENDS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"(?<!def )(?<!fake_)open_browser\s*\(", text)
        assert "BrowserGate(" in text
        assert "await get_browser()" in text or "gate.get" in text


def test_front_ends_import_shared_download_engine():
    shared = (ROOT / "moon_download.py").read_text(encoding="utf-8")
    for marker in DOWNLOAD_DEFS:
        assert marker in shared

    for name in FRONT_ENDS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for marker in DOWNLOAD_DEFS:
            assert marker not in text
        assert "from moon_download import" in text
