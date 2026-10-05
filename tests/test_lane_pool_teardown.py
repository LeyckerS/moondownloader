"""A closed BrowserGate must not leave its datanodes lane pool behind.

`_lanes` and `_lane_queue` are module globals, but the browser they point at is
per-gate. `_ensure_lanes()` only rebuilds the pool when `_lane_queue is None`, so
a teardown that closes the browser without dropping the pool leaves the next run
holding a closed context: `acquire_lane()` hands it out, `new_page()` raises
"Target page, context or browser has been closed", and every datanodes link fails.

The shared/CDP branch called `shutdown_chrome()`, which drops the pool. The
non-shared branch -- taken whenever Playwright's own Chromium is used instead of
real Chrome -- did not.
"""
from __future__ import annotations

import asyncio

import pytest

import moon_extract


class _ClosedContextError(Exception):
    pass


class FakeContext:
    """Models the one property that matters: a closed context refuses new_page()."""

    def __init__(self):
        self.closed = False

    async def new_page(self):
        if self.closed:
            raise _ClosedContextError("Target page, context or browser has been closed")
        return object()

    async def close(self):
        self.closed = True

    def __repr__(self):
        return f"<context closed={self.closed}>"


class FakeBrowser:
    def __init__(self):
        self.contexts: list[FakeContext] = []
        self.closed = False

    async def new_context(self, **_kw):
        ctx = FakeContext()
        self.contexts.append(ctx)
        return ctx

    def is_connected(self):
        return not self.closed

    async def close(self):
        self.closed = True
        for ctx in self.contexts:
            await ctx.close()


class FakePlaywright:
    def __init__(self):
        self.launches = 0

    def launch(self):
        self.launches += 1
        return FakeBrowser()

    async def stop(self):
        pass


@pytest.fixture
def non_shared_browser(monkeypatch):
    """Every non-shared open_browser() return: (a fresh browser, False)."""
    pw = FakePlaywright()

    async def fake_start_playwright():
        return pw

    async def fake_open_browser(_pw, _launch_args, _headless=None):
        return pw.launch(), False

    async def fake_prepare(_ctx):
        pass

    monkeypatch.setattr(moon_extract, "_start_playwright", fake_start_playwright)
    monkeypatch.setattr(moon_extract, "open_browser", fake_open_browser)
    monkeypatch.setattr(moon_extract, "prepare_datanodes_context", fake_prepare)
    monkeypatch.setattr(moon_extract, "dn_launch_kwargs", lambda *_a, **_k: {})
    return pw


async def _one_run():
    """One full BrowserGate lifecycle, the way Engine.start() drives it.

    Returns the exception the extraction raised, or None.
    """
    gate = moon_extract.BrowserGate([])
    browser = await gate.get()
    try:
        async with moon_extract.acquire_lane(browser) as ctx:
            await ctx.new_page()
        return None
    except _ClosedContextError as e:
        return str(e)
    finally:
        await gate.aclose()


def test_closing_the_gate_drops_the_lane_pool(non_shared_browser):
    async def scenario():
        await moon_extract._drop_lanes()          # start from a known-clean process
        assert moon_extract._lane_queue is None
        first = await _one_run()
        assert first is None, "the first run should extract normally"
        # The decisive state, straight after teardown.
        pool_after_close = moon_extract._lane_queue
        second = await _one_run()
        return first, pool_after_close, second

    first, pool_after_close, second = asyncio.run(scenario())

    assert pool_after_close is None, (
        "BrowserGate.aclose() left the lane pool in place after closing its "
        f"browser (_lane_queue={pool_after_close!r}); _ensure_lanes() returns "
        "early whenever it is not None, so the next run reuses a dead context"
    )
    assert second is None, (
        f"the second run in the same process failed with: {second}"
    )
    assert non_shared_browser.launches == 2, "each run should have launched its own browser"


def test_a_live_pool_is_not_shared_across_runs(non_shared_browser):
    """Belt and braces: nothing from run 1's context may be reachable in run 2."""
    contexts = []
    errors = []

    async def scenario():
        await moon_extract._drop_lanes()
        for _ in range(3):
            gate = moon_extract.BrowserGate([])
            browser = await gate.get()
            try:
                async with moon_extract.acquire_lane(browser) as ctx:
                    await ctx.new_page()
                    contexts.append(ctx)
            except _ClosedContextError as e:
                errors.append(str(e))
            await gate.aclose()

    asyncio.run(scenario())

    assert not errors, (
        f"{len(errors)} of 3 consecutive runs in the same process failed with "
        f"{errors[0]!r} -- the pool outlived the browser it belonged to"
    )
    assert len({id(c) for c in contexts}) == 3, (
        "consecutive runs reused the same pooled context: "
        f"{[repr(c) for c in contexts]}"
    )
    assert all(c.closed for c in contexts), "each run's context was closed at teardown"
