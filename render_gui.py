"""Visual check for the web GUI: render index.html in headless Chromium.

    python3 render_gui.py OUT_DIR [width height]...

WebView2 is Chromium, so a Chromium screenshot is what the Windows app shows --
same layout engine, same compositor, same font rasteriser. Runs the page's own
MockApi, so the render path under test is the one that ships.
"""
from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
PAGE = (HERE / "web" / "index.html").resolve()
ROW_TIMEOUT_MS = 8000


def shoot(out_dir: pathlib.Path, sizes: list[tuple[int, int]]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb",
                                          "--font-render-hinting=none"])
        for width, height in sizes:
            page = browser.new_page(viewport={"width": width, "height": height},
                                    device_scale_factor=1)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            try:
                page.goto(PAGE.as_uri())
                page.wait_for_selector(".frow", timeout=ROW_TIMEOUT_MS)
            except PlaywrightTimeout:
                # A page that never draws its rows is the usual failure, and the
                # picture of it is the useful part -- a traceback from deep
                # inside Playwright is not. Name the viewport, keep the shot,
                # and go on to the next size so one run reports every failure
                # rather than only the first.
                problems.append(
                    f"{width}x{height} no .frow rendered in {ROW_TIMEOUT_MS} ms")
                page.screenshot(path=str(out_dir / f"web_{width}x{height}_broken.png"))
                page.close()
                continue
            page.wait_for_timeout(2600)           # let the mock engine fill the spark
            shot = out_dir / f"web_{width}x{height}_files.png"
            page.screenshot(path=str(shot))

            page.click('button[data-tab="log"]')
            page.wait_for_timeout(400)
            page.screenshot(path=str(out_dir / f"web_{width}x{height}_log.png"))
            page.click('button[data-tab="files"]')

            # Overflow audit: anything wider than the viewport means a layout bug.
            overflow = page.evaluate(
                """() => {
                    const bad = [];
                    for (const el of document.querySelectorAll('*')) {
                        const r = el.getBoundingClientRect();
                        if (r.width && (r.right > window.innerWidth + 1 || r.left < -1))
                            bad.push(el.tagName + '.' + (el.className || '') + ' right=' + Math.round(r.right));
                    }
                    return bad.slice(0, 8);
                }"""
            )
            metrics = page.evaluate(
                """() => ({
                    rows: document.querySelectorAll('.frow').length,
                    speed: document.querySelector('#vSpeed').textContent,
                    spark: (document.querySelector('#sparkLine').getAttribute('points') || '').length,
                    scrollW: document.documentElement.scrollWidth,
                    innerW: window.innerWidth,
                    fontPx: getComputedStyle(document.documentElement).fontSize,
                    label: getComputedStyle(document.querySelector('.s-label')).fontSize,
                })"""
            )
            print(f"{width}x{height}: rows={metrics['rows']} speed={metrics['speed']} "
                  f"sparkpts={metrics['spark']} root={metrics['fontPx']} "
                  f"slider-label={metrics['label']} scrollW={metrics['scrollW']}/{metrics['innerW']}")
            if errors:
                problems.append(f"{width}x{height} js errors: {errors[:3]}")
            if overflow:
                problems.append(f"{width}x{height} overflow: {overflow}")
            if metrics["scrollW"] > metrics["innerW"] + 1:
                problems.append(f"{width}x{height} horizontal scroll")
            if metrics["rows"] < 8:
                problems.append(f"{width}x{height} only {metrics['rows']} rows rendered")
            page.close()
        browser.close()

    for line in problems:
        print("[FAIL]", line)
    if not problems:
        print("[OK] no js errors, no overflow, no horizontal scroll")
    return 1 if problems else 0


def main() -> int:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "shots")
    raw = sys.argv[2:]
    sizes = [(2554, 1400), (1440, 900)] if not raw else [
        (int(raw[i]), int(raw[i + 1])) for i in range(0, len(raw) - 1, 2)]
    return shoot(out, sizes)


if __name__ == "__main__":
    raise SystemExit(main())
