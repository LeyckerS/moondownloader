"""End-to-end over the SHIPPING transport: browser -> loopback HTTP -> Engine.

    python3 integration_http.py [screenshot.png]

This is the path start.bat actually takes on Windows: moon_bridge serves web/ on
127.0.0.1 with a per-run token, Edge (Chromium) loads it in --app mode. Here the
same server is driven by Playwright's Chromium, so the transport, the token
check, the JSON routing and every render function are all under test.

Only the network layer is faked: Engine._run is replaced with a coroutine that
moves the same counters, FileRecords and byte samples the real one does.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import moon_bridge                                    # noqa: E402
import moon_engine                                    # noqa: E402

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
    size = 90_000_000
    recs = []
    for i, url in enumerate(urls):
        rec = moon_engine.FileRecord(url=url, filename=url.rsplit("/", 1)[-1])
        rec.file_bytes = size
        if i < 2:
            rec.done_bytes = int(size * 0.93)          # like a resumed .tmp
        recs.append(rec)

    for step in range(240):
        if self._get("_stop_flag"):
            self.log("⏹  fermato", "warn")
            break
        for i, rec in enumerate(recs):
            if rec.status == "pending" and i <= step // 6:
                rec.status = "extracting"
                self._track(rec)
                self.log(f"  → {rec.filename}", "dim")
            elif rec.status == "extracting":
                rec.status = "downloading"
                self._inc("_url_done"); self._inc("_dls")
                self._track(rec)
            elif rec.status == "downloading":
                rec.done_bytes = min(size, rec.done_bytes + 700_000)
                rec.live_mbs = 6.0 + (i % 4) * 2.5
                self._bytes_acc.append((time.monotonic(), 700_000))
                if rec.done_bytes >= size:
                    rec.status = "ok"; rec.avg_mbs = rec.live_mbs
                    self._inc("_ok"); self._inc("_dl_done"); self._inc("_dls", -1)
                    self.log(f"    ✓  Saved: {rec.filename}  ({rec.avg_mbs:.1f} MB/s)", "ok")
        if all(r.status == "ok" for r in recs):
            break
        await asyncio.sleep(0.12)
    self._on_done()


def main() -> int:
    # Imported here, not at module level, so importing the module (to read
    # VERSION_HANDSHAKE_JS, for example) works without the browser driver.
    from playwright.sync_api import sync_playwright

    moon_engine.Engine._run = fake_run
    moon_bridge.SETTINGS = pathlib.Path(tempfile.mkdtemp()) / "settings.json"

    engine = moon_engine.Engine()
    api = moon_bridge.Api(engine, dialogs=lambda kind: "")
    server, url = moon_bridge.serve(api)
    print("server:", url.split("?")[0], "· token len", len(server.token))

    out_dir = tempfile.mkdtemp(prefix="moon-dl-")
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(url)
        page.wait_for_function(VERSION_HANDSHAKE_JS, timeout=8000)
        print("handshake over HTTP ok ·", page.eval_on_selector("#version", "e => e.textContent"))

        # The token gate must actually gate.
        forbidden = page.evaluate(
            """async () => {
                const r = await fetch('api/snapshot', {method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-Moon-Token': 'wrong'},
                    body: JSON.stringify({args: [0]})});
                return r.status;
            }""")
        print("api con token sbagliato ->", forbidden)

        page.fill("#links", LINKS)
        page.fill("#outFolder", out_dir)
        page.wait_for_timeout(350)

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
                ring: document.querySelector('.frow[data-state="download"] .ring-fg')
                        ?.style.strokeDasharray || '',
                demo: !document.querySelector('#demoChip').hidden,
            })""")
        print("stato a schermo:", state)

        page.click('button[data-tab="log"]')
        page.wait_for_timeout(500)
        log_text = page.eval_on_selector("#log", "e => e.textContent")
        # The two tabs used to show the same thing: `.files { display: grid }`
        # outranked the UA [hidden] rule, so the list stayed painted over the log.
        tab_isolated = page.evaluate(
            """() => {
                const files = document.querySelector('#files');
                const log = document.querySelector('#log');
                return getComputedStyle(files).display === 'none'
                    && getComputedStyle(log).display !== 'none'
                    && log.textContent.length > 0;
            }""")
        print("log tab isolato:", tab_isolated)
        if len(sys.argv) > 1:
            page.screenshot(path=sys.argv[1].replace(".png", "_log.png"))
        page.click('button[data-tab="files"]')
        page.wait_for_timeout(400)
        if len(sys.argv) > 1:
            page.screenshot(path=sys.argv[1])

        # Default language is English; the switch must relabel already-rendered
        # chrome, not just the static strings.
        lang_default = page.eval_on_selector("#lang button.on", "e => e.dataset.lang")
        en_state = page.eval_on_selector(".frow[data-state='ok'] .fstate em", "e => e.textContent")
        page.click('#lang button[data-lang="it"]')
        page.wait_for_timeout(500)
        it_state = page.eval_on_selector(".frow[data-state='ok'] .fstate em", "e => e.textContent")
        it_tab = page.eval_on_selector('#tabs button[data-tab="files"] span', "e => e.textContent")
        page.click('#lang button[data-lang="en"]')
        page.wait_for_timeout(300)
        print(f"lang: default={lang_default} en={en_state!r} it={it_state!r} tab_it={it_tab!r}")

        badge = page.eval_on_selector("#fileBadge", "e => e.textContent")
        active_rows = page.evaluate(
            """() => document.querySelectorAll('.frow[data-state="download"], .frow[data-state="extract"], .frow[data-state="kill"], .frow[data-state="queue"]').length""")
        print(f"badge={badge!r} active rows={active_rows}")

        checks = [
            (forbidden == 403, f"wrong token accepted: HTTP {forbidden}"),
            (not state["demo"], "the page thinks it is in demo mode, not connected"),
            (state["pill"] in ("RUNNING", "DONE"), f"pill: {state['pill']!r}"),
            (state["rows"] >= 4, f"only {state['rows']} rows"),
            (float(state["speed"]) > 0, f"speed {state['speed']}"),
            (float(state["bytes"]) > 0, f"downloaded bytes {state['bytes']}"),
            (int(state["done"]) >= 1, f"no file completed: {state['done']}"),
            (state["ring"] not in ("", "0 100"), f"empty progress ring: {state['ring']!r}"),
            ("fake:" in log_text, "the engine log lines never reach the page"),
            (tab_isolated, "the LOG tab still shows the transfer list"),
            (lang_default == "en", f"default language {lang_default!r}, expected en"),
            (en_state == "saved" and it_state == "salvato",
             f"switching language does not relabel the rows: {en_state!r} -> {it_state!r}"),
            (it_tab == "Trasferimenti", f"tab not translated: {it_tab!r}"),
            (badge == str(active_rows) or (badge == "" and active_rows == 0),
             f"badge {badge!r} does not match the {active_rows} active rows"),
        ]
        for ok, msg in checks:
            if not ok:
                problems.append(msg)

        if engine._get("_running"):
            page.click("#btnStart")
            page.wait_for_timeout(1200)
            if engine._get("_running"):
                problems.append("stop never reached the engine")
            else:
                print("stop ok · engine state:", engine._state)

        # The wrong-token probe above deliberately provokes one 403; anything
        # else on the console is a real fault.
        real_errors = [e for e in errors if "403" not in e]
        if real_errors:
            problems.append(f"errori js: {real_errors[:3]}")
        page.close()
        browser.close()

    server.shutdown()
    for line in problems:
        print("[FAIL]", line)
    if problems:
        return 1
    print("[OK] browser → HTTP loopback → Engine: token, start, metriche live, righe, log, stop")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
