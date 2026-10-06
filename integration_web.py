"""End-to-end check: web GUI -> pywebview-shaped bridge -> real Engine.

    python3 integration_web.py [screenshot.png]

Everything on the path is the shipping code except the network: Engine._run is
replaced by a coroutine that moves the same counters, FileRecords and byte
samples the real one does. So this exercises Api.start/stop/snapshot, the
snapshot arithmetic, the log ring cursor, and every render function in app.js.

The page's bridge is installed with Playwright's expose_function, which has the
same promise shape as pywebview's injected api -- app.js cannot tell them apart.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
import time
import traceback

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import moon_bridge                                    # noqa: E402
import moon_engine                                    # noqa: E402

PAGE = (HERE / "web" / "index.html").resolve()
LINKS = "\n".join(
    f"https://{'datanodes.to' if n % 3 else 'fuckingfast.co'}/x{n}/"
    f"CoD_-_Black_Ops_3_--_fitgirl-repacks.site_--_.part{n:02d}.rar"
    for n in range(1, 9))

# Wait for the page's boot sequence to have stamped the #version line. This is
# checked against whatever the app *ships* -- the old literal "v2" match became
# a permanent timeout when the app moved past v2.x (it ships v4.2 today).
VERSION_HANDSHAKE_JS = (
    "() => document.querySelector('#version').textContent.startsWith('v')"
)


async def fake_run(self, urls, n_workers, max_dl, max_retries):
    """Stand-in for the asyncio core: same state mutations, no sockets."""
    self.log(f"   fake: {n_workers} extractors · {max_dl} streams · {max_retries} retries", "dim")
    # Tuned so the sampled speed lands around 25-30 MB/s and a few files finish
    # inside the test window: 0.7 MB per tick per active file at ~8 ticks/s.
    size = 90_000_000
    recs = []
    for i, url in enumerate(urls):
        rec = moon_engine.FileRecord(url=url, filename=url.rsplit("/", 1)[-1])
        rec.file_bytes = size
        # First two start nearly finished, the way a resumed .tmp does, so the
        # test sees real completions without waiting a whole file out.
        if i < 2:
            rec.done_bytes = int(size * 0.93)
        recs.append(rec)

    for step in range(240):
        if self._get("_stop_flag"):
            self.log("⏹  fermato", "warn")
            break
        # extraction advances first, downloads trail it -- the real pipeline shape
        for i, rec in enumerate(recs):
            if rec.status == "pending" and i <= step // 6:
                rec.status = "extracting"
                self._track(rec)
                self.log(f"  → {rec.filename}", "dim")
            elif rec.status == "extracting":
                rec.status = "downloading"
                self._inc("_url_done")
                self._inc("_dls")
                self._track(rec)
            elif rec.status == "downloading":
                chunk = 700_000
                rec.done_bytes = min(size, rec.done_bytes + chunk)
                rec.live_mbs = 6.0 + (i % 4) * 2.5
                self._bytes_acc.append((time.monotonic(), chunk))
                if rec.done_bytes >= size:
                    rec.status = "ok"
                    rec.avg_mbs = rec.live_mbs
                    self._inc("_ok"); self._inc("_dl_done"); self._inc("_dls", -1)
                    self.log(f"    ✓  Saved: {rec.filename}  ({rec.avg_mbs:.1f} MB/s)", "ok")
        if all(r.status == "ok" for r in recs):
            break
        await asyncio.sleep(0.12)
    self._on_done()


def install_bridge(page, api) -> None:
    names = ["hello", "snapshot", "start", "stop", "clear_files", "load_txt",
             "browse_folder", "browse_chrome", "settings_save", "settings_load"]
    for name in names:
        page.expose_function(f"__api_{name}", getattr(api, name))
    calls = ",\n".join(f"{n}: (...a) => window.__api_{n}(...a)" for n in names)
    page.add_init_script(f"window.pywebview = {{ api: {{\n{calls}\n}} }};")


def main() -> int:
    moon_engine.Engine._run = fake_run
    moon_bridge.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "settings.json"

    engine = moon_engine.Engine()
    api = moon_bridge.Api(engine, dialogs=lambda kind, *a: None)
    out_dir = tempfile.mkdtemp(prefix="moon-dl-")

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        install_bridge(page, api)
        page.goto(PAGE.as_uri())
        page.wait_for_function(VERSION_HANDSHAKE_JS, timeout=8000)
        print("bridge handshake ok ·", page.eval_on_selector("#version", "e => e.textContent"))

        page.fill("#links", LINKS)
        page.fill("#outFolder", out_dir)
        page.wait_for_timeout(350)
        counts = page.evaluate("() => [document.querySelector('#cntDn').textContent, document.querySelector('#cntFf').textContent]")
        print("host split from the page:", counts)

        page.click("#btnStart")
        page.wait_for_selector(".frow", timeout=8000)
        page.wait_for_function(
            "() => parseFloat(document.querySelector('#vSpeed').textContent) > 0", timeout=8000)
        page.wait_for_timeout(6000)

        state = page.evaluate(
            """() => ({
                pill: document.querySelector('#stateLabel').textContent,
                rows: document.querySelectorAll('.frow').length,
                speed: document.querySelector('#vSpeed').textContent,
                done: document.querySelector('#vDone').textContent,
                bytes: document.querySelector('#vBytes').textContent,
                phase: document.querySelector('#phase').textContent,
                extract: document.querySelector('#cntExtract').textContent,
            })""")
        print("engine state on screen:", state)

        page.click('button[data-tab="log"]')
        page.wait_for_timeout(500)
        log_text = page.eval_on_selector("#log", "e => e.textContent")
        if len(sys.argv) > 1:
            page.screenshot(path=sys.argv[1].replace(".png", "_log.png"))
        page.click('button[data-tab="files"]')
        page.wait_for_timeout(400)
        if len(sys.argv) > 1:
            page.screenshot(path=sys.argv[1])

        # ── assertions ─────────────────────────────────────────────────────
        running_or_done = engine._get("_running") or engine._state == "done"
        checks = [
            (state["pill"] in ("RUNNING", "DONE"), f"pill shows {state['pill']!r}"),
            (state["rows"] >= 4, f"only {state['rows']} rows rendered"),
            (float(state["speed"]) > 0, f"speed is {state['speed']}"),
            (float(state["bytes"]) > 0, f"bytes downloaded is {state['bytes']}"),
            ("xtracting" in state["phase"] or "ownloading" in state["phase"],
             f"phase text is {state['phase']!r}"),
            ("fake:" in log_text, "engine log lines never reached the page"),
            ("Saved:" in log_text or "→" in log_text, "no per-file log lines on the page"),
            (counts == ["6", "2"], f"host split wrong: {counts}"),
            (running_or_done, "engine neither running nor done"),
            (int(state["done"]) >= 1, f"no file completed: {state['done']}"),
        ]
        for ok, msg in checks:
            if not ok:
                problems.append(msg)

        if engine._get("_running"):
            page.click("#btnStart")                    # now a STOP
            # The engine clears _stop_flag inside _on_done, so the observable
            # result of a stop is "no longer running", not the flag itself.
            page.wait_for_timeout(1200)
            if engine._get("_running"):
                problems.append("stop did not reach the engine")
            else:
                print("stop ok · engine state:", engine._state)
        else:
            print("note: fake run finished before the stop check")

        if errors:
            problems.append(f"js errors: {errors[:3]}")
        page.close()
        browser.close()

    for line in problems:
        print("[FAIL]", line)
    if problems:
        return 1
    print("[OK] GUI ↔ bridge ↔ engine: start, live metrics, rows, log, stop")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
