"""Shared pytest fixtures for no-Chrome regression tests."""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moon_cli  # noqa: E402
import moon_engine  # noqa: E402
import moon_extract  # noqa: E402

@pytest.fixture
def browser_calls(monkeypatch):
    calls = {
        "playwright": 0,
        "open_browser": 0,
        "close_browser": 0,
        "shutdown_chrome": 0,
    }

    async def fake_ff(url):
        await asyncio.sleep(0.01)
        return url.replace("fuckingfast.co", "dl.fuckingfast.co") + "?fake"

    async def fake_dn(browser, url):
        await asyncio.sleep(0.01)
        if browser is None:
            raise AssertionError("extract_datanodes got no browser")
        return url + "?fake", ""

    async def fake_download(*a, **kw):
        await asyncio.sleep(0.01)
        return True, "", 1024

    async def fake_start_playwright():
        calls["playwright"] += 1
        return object()

    async def fake_open_browser(pw, args, headless=None):
        calls["open_browser"] += 1
        return object(), True

    async def fake_close_browser(browser, shared=None):
        calls["close_browser"] += 1

    async def fake_shutdown_chrome():
        calls["shutdown_chrome"] += 1

    monkeypatch.setattr(moon_extract, "_start_playwright", fake_start_playwright)
    monkeypatch.setattr(moon_extract, "open_browser", fake_open_browser)
    monkeypatch.setattr(moon_extract, "close_browser", fake_close_browser)
    monkeypatch.setattr(moon_extract, "shutdown_chrome", fake_shutdown_chrome)

    for host in (moon_engine, moon_cli):
        monkeypatch.setattr(host, "extract_fuckingfast", fake_ff)
        monkeypatch.setattr(host, "extract_datanodes", fake_dn)
        monkeypatch.setattr(
            host,
            "close_ff_session",
            lambda: asyncio.sleep(0),
        )

    monkeypatch.setattr(moon_cli, "download_file", fake_download)

    return calls