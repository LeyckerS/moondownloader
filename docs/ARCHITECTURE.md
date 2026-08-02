# Architecture

How V2 is put together, and why each piece is the way it is.

---

## Why the GUI is not tkinter

Up to v15 it was, and tkinter's canvas has **no anti-aliasing and no alpha channel**.
Every circle, arc and rounded corner came out as steps, and glows were opaque rings
blended against a known background. Its type and geometry were absolute too: on a
2560×1440 screen the text measured about **8 px of ink** and roughly a third of the
window stayed empty.

V2 renders the interface in **Edge WebView2** — the same Chromium that already ships
with Windows 10/11. GPU compositing: gradients, blur, shadows, transitions, subpixel
anti-aliasing. Type scales with the window: `clamp()` moves the base from 16 px at
1280 wide up to 19 px on a large monitor instead of staying nailed down.

The tkinter app was removed in V2: it was a second interface to maintain, and it was
also the file the engine used to be generated from, which made the legacy GUI the source
of truth for the modern engine. `moon_engine.py` is that engine, standing on its own.

The async engine itself was **not** rewritten. `_run`, `_browser_worker`, `_do_dl`,
`download_file`, `Telemetry` and `ProxyPool` are the 14.8 code, plus the lazy browser
launch described below. `download_file`, `Telemetry` and `ProxyPool` now live in
`moon_download.py`, shared by `moon_engine.py` and `moon_cli.py`.

## Startup

```
start.bat
```

It no longer installs pywebview. It opens a server on `127.0.0.1` (port chosen by the
kernel) and launches **Edge** — or Chrome, whichever it finds — with `--app=`, which is a
window with no tabs and no address bar. Same Chromium, no native dependency, no backend
to guess.

### Why not pywebview

Because on a machine without the .NET bridge it silently falls back to **MSHTML**, the
IE11 engine: native blue sliders with tick marks, a serif wordmark, everything unrolled
into one column. pywebview picks its Windows backend at runtime, and when the bridge to
WebView2 fails to load (`pythonnet` missing, Evergreen runtime missing) it **switches to
Trident without a word** — no error, just a page from 2013. Inside Trident `grid`,
`clamp()`, `color-mix()`, `system-ui` and `backdrop-filter` do not exist.

The GUI now notices this itself: if the engine cannot do `grid` and `color-mix`, it shows
a notice instead of drawing itself badly.

Two alternatives remain, if you want them:

```bash
python moon_bridge.py --pywebview    # pywebview window, backend pinned to edgechromium
python moon_bridge.py --browser      # default browser
python moon_bridge.py --serve        # server only, prints the URL
```

Just want to look at the interface, without Python? Open `web/index.html` in Chrome or
Edge — it boots in **demo mode** against a synthetic engine (a `DEMO` chip appears bottom
right).

### The local server, briefly

It listens on `127.0.0.1` **only**, and every `/api/` call must carry the token minted at
startup: without it, 403. The API starts downloads and reads paths, so this is not a
formality. The process exits by itself after 12 seconds with no requests: the page polls
every 80 ms, so "no requests" means "the window is closed", and the app exits instead of
lingering as an orphan process.

---

## The pieces

```
┌──────────────────────────── moon_bridge.py ────────────────────────────┐
│  HTTP server on 127.0.0.1 + per-run token                              │
│  launches Edge/Chrome with --app=  (a window with no tabs)             │
│  API: hello · snapshot · start · stop · clear_files                    │
│       browse_folder · browse_chrome · load_txt · settings_save         │
└────────────────┬───────────────────────────────────────┬───────────────┘
                 │ POST /api/<name>                      │ direct calls
         ┌───────▼────────┐                     ┌────────▼─────────┐
         │  web/app.js    │                     │ moon_engine.py   │
         │  renders 12Hz  │◄─── snapshot() ─────│ Engine (headless)│
         │  index + css   │                     │ = the 14.8 engine│
         └────────────────┘                     └──────────────────┘
```

Native dialogs (folder, `chrome.exe`, `.txt`) run in a **child process** with
`tkinter.filedialog`: a dialog wants the mainloop of the thread that created it, and the
HTTP handler lives on a pool thread — 200 ms of subprocess is the boring, reliable answer.

**Pull model, not push.** The page asks for `snapshot(cursor)` about 12 times a second.
Pushing from Python would mean serialising a JS string per tick and touching the WebView
from a thread that is not its own; pulling keeps every DOM write on the page's own
timeline, and a late snapshot is a dropped frame, not a stall.

**The log has a cursor.** `Engine` keeps a 6000-line ring plus a monotonic counter; the
page asks for "everything after N". If it falls behind further than the ring, it gets the
oldest line still held rather than a gap it could not detect.

**Rows read live `FileRecord`s.** `download_file` publishes `rec.done_bytes` and
`rec.live_mbs` about four times a second on **its own** window (`pub_win`), kept separate
from the stall detector's: sharing one deque would have eaten the 60 s history the kill
decision depends on.

**Everything from the page is untrusted input.** `Engine.apply_cfg()` coerces and clamps
every number to its limits (`workers` 2–32, `dl_streams` 2–48, `retries` 0–5, `dn_pages`
1–8, `dn_captcha` 30–600) before it reaches a semaphore.

---

## Files

| File | Role |
|:--|:--|
| `moon_bridge.py` | window host, OS dialogs, `settings.json` |
| `moon_engine.py` | the engine with no GUI + the JSON API |
| `moon_extract.py` | extraction for both providers + the Chrome lifecycle + `BrowserGate` |
| `moon_download.py` | the download engine, `Telemetry` and `ProxyPool`, shared by the GUI engine and CLI |
| `web/index.html` | structure |
| `web/styles.css` | everything visual |
| `web/app.js` | rendering, the bridge, and a synthetic engine for the preview |
| `web/assets/` | `mark.png`, `backdrop.png`, `window.png` |
| `moon_cli.py` | the headless CLI |
| `tests/test_no_chrome.py` | asserts fuckingfast opens no browser |
| `render_gui.py` | verification renders in headless Chromium |
| `integration_http.py` | end-to-end: browser ↔ loopback HTTP ↔ Engine |
| `integration_web.py` | end-to-end: pywebview ↔ Engine |

The download engine, telemetry and proxy rotation (`download_file`, `Telemetry`,
`ProxyPool`) are shared through `moon_download.py`; the extraction layer, the Chrome
lifecycle and the launch decision are shared through `moon_extract.py`. `moon_engine.py`
and `moon_cli.py` both import from both.

---

## Chrome opens only when it is needed

Up to v15 every front-end called `open_browser()` **once per worker** at the top of the
run, before looking at a single URL. Paste nothing but fuckingfast links — pure HTTP,
~0.25 s per link, no browser — and a Chrome window still opened, plus the Playwright
driver (~1.5 s of boot). The window was necessarily visible: Turnstile issues no token to
a headless Chrome, so datanodes runs with `headless=False` and every launch is seen.

The decision now lives in one place, `moon_extract.BrowserGate`:

- `get()` is the only thing that launches, and **only** the datanodes branch calls it
- no datanodes link → no browser, no driver, no node process
- concurrent first calls collapse onto **one** shared instance (which is what the
  `cf_clearance` profile needs anyway)
- `aclose()` tears down in the right order: Chrome first, then the driver

This holds for both front-ends: the GUI and the CLI.

```bash
pytest tests/ -q
```

Covers the engine and the CLI, and checks both sources for a direct `open_browser(` call. It needs no browser, no display and no Playwright install — it runs
in CI on every push.

---

## The interface, feature by feature

**LINKS** — a textarea with links **coloured per host as you type** (a `<pre>` overlay
under a transparent textarea, scroll synced: real colours without losing selection, undo
or IME). Instant count, and a `datanodes / fuckingfast / others` mix bar.

**DESTINATION** — folder plus native dialog, and `Download` / `Links only`
(`mode="links"` writes `output_links.txt` without downloading).

**COMMON** — `Extractors` 2–32, `DL streams` 2–48, `Retries` 0–5. The recommended value
gets a **tick on the track**, not just a caption.

**DATANODES.TO** — `Pages` 1–8, `Captcha` 30–600 s, Chrome path, API key. They go through
`moon_extract.configure()` on every start: no more `setx` and a restart. The chip in the
top right says what is in use: `auto` / `chrome` / `api key`.

**FUCKINGFAST.CO** — no knobs, and it says so: pure HTTP, opens no browser, has no
captcha. It only reports whether `curl_cffi` is present.

**The hero band** — `SPEED` (3 s rolling window) is the figure being watched, so it is set
at 49px with the sparkline spanning the card underneath; `COMPLETED`, `DOWNLOADED`
(`ok / ko / kill`) and `ETA` (estimated on bytes, not on files) are reference and sit small
to its right. Four equal cards gave four equal weights to numbers that are not equal.
`PIPELINE` keeps extraction and download separate, because they run at the same time.

**ACTIVE FILES** — one row per file: SVG progress ring, state
(`queue / extracting / downloading / saved / failed / restart`), percentage, instantaneous
speed, the provider it came from (read off the source URL, which is the row key), and a bar
along the foot. Row weight follows state: what is moving gets room and a brighter name,
what has finished tightens and recedes. `content-visibility: auto`, so a 400-row list costs
nothing while it is off screen. Above the list, a filter box and state chips hide rows
without touching any count the engine owns.

**LOG** — the same lines as always, same tags and colours, capped at 2000.

**Footer** — a `PROXY n` chip when `proxies.txt` is loaded, a `.TMP TO RESUME` chip when
partial files are left over.

Settings (and the pasted links) are saved in `settings.json` next to the script with an
atomic write: a crash mid-save cannot leave a truncated file.

---

## The last pass — what changed

**The badge said 40 with 124 files downloaded.** That was the cap on rows kept in memory
(`_ROWS_KEEP`), not a count: above that threshold the engine retired finished rows and the
badge photographed the list, not reality. The badge now counts **only transfers in
flight** (downloading, extracting, restarting, queued) and the cap went up to 120. The
real total lives where it belongs: the COMPLETED card.

**Active rows sort to the top.** With 124 files the tail of "saved" rows buried the four
real downloads. The order is now download → extraction → restart → queue → finished (most
recent first), with the finished tail slightly dimmed. Sorting uses the CSS `order`
property, so no node moves in the DOM: zero reflow to reorder 120 rows.

**The LOG tab showed the same thing as ACTIVE FILES.** Same trap as `.empty`:
`.files { display: grid }` outranks the user-agent `[hidden]` rule, so the list stayed
painted over the log. `.files[hidden]` and `.log[hidden]` are explicit now, and the
end-to-end test verifies it with `getComputedStyle`.

**New defaults:** `Captcha 30 s`, `Pages 8`. They live in the engine (`Engine._cfg`) and
in the markup, so they apply on a first run with no `settings.json` too.

**Language button, English by default.** EN|IT top right, the choice saved in
`settings.json`. The engine no longer speaks a language in its status sentences: it sends
numbers and a stage name (`idle` / `extracting` / `downloading` / `done`) and the page
builds the sentence. Switching language relabels what is already on screen, rows included.

**Reactive details added:**

- animated counters (interpolated in `requestAnimationFrame`) on speed, completed and GB:
  you see *how* a number moves, not only where it landed
- a spotlight that follows the cursor across the cards — two custom properties on
  `pointermove`, no layout
- staggered card entrance on startup (`--d` for the delay)
- a global progress bar under the header, shimmering only while something is actually
  moving
- rows entering from the left, and a **green flash** when a file finishes
- a status stripe that pulses on active transfers
- a sparkline with a blue→teal gradient stroke and a lit head
- tactile button press, visible focus rings, animated toast exit
- `prefers-reduced-motion` respected: all of it switches off

---

## Verification

```bash
pytest tests/ -q       # no browser for fuckingfast, exactly one for datanodes
python integration_http.py     # browser → loopback HTTP → Engine (the path start.bat takes)
python integration_web.py      # pywebview bridge → Engine (the --pywebview path)
python render_gui.py out/           # renders at 2554x1400 and 1440x900 + an overflow audit
python moon_engine.py          # headless engine: prints a snapshot and exits
```

`integration_http.py` is the one that matters: it starts the real server, opens the real
page in Chromium, checks that **the token gate answers 403** to a call without a
credential, then starts a run and verifies that speed, rows, bytes, stage, progress rings
and log all arrive from the engine; finally it presses STOP and verifies the engine stops.

Last run: `token 403 ok · 8 rows · 25.4 MB/s · 2 completed · ring 32% · log tab isolated ·
language en→it relabels · badge 6 = 6 active rows · stop → done`.
