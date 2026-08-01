# Changelog

All notable changes to Moon Downloader will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Versioning.** The public releases are **V1** (tag `v14.1`) and **V2** (tag `v2.0`).
> V2 is where the numbering resets: the 14.x and 15.0 entries below keep the numbers they
> shipped with, and `v14.1` keeps its tag so its download link never breaks.

## [Unreleased]

### Added
- **CI runs on Python 3.10, 3.11 and 3.12, and lints with `ruff`.** The matrix uses
  `fail-fast: false` so one version failing still reports the others. `ruff.toml` selects
  `E`/`F`/`W`/`I` and parks the rules that fire on the codebase as it stands, each with its
  count and the reason — the goal is a baseline that catches regressions, not a reformat
  (#69, @Moferanoluwa)
- **Documentation-only pull requests are checked against the real CLI parser.** A new
  `Docs CLI Check` workflow triggers on `**.md` and runs `tests/test_docs_cli_flags.py`,
  which reads the true flag set from `moon_cli.py --help` at test time and fails on any
  `moon_cli.py` invocation in a tracked Markdown file that uses a flag the parser does not
  accept. Reading `--help` rather than hardcoding means adding a flag never requires
  touching the test (#70, @Moferanoluwa)
- `CONTRIBUTING.md` states what counts as a contribution here, what gets labelled `invalid`
  or `spam`, and that the bar does not move during Hacktoberfest (#72)
- `docs/CLI.md` — a reference for `moon_cli.py`: every flag, its default, and what it
  actually controls (#37, @kushin25)
- `moon_cli.py --version`, and the version is now recorded in both report flavours: the
  CLI log header and JSON report gained it, the engine JSON report gained it for parity.
  One `VERSION` constant, imported everywhere (#61, @Moferanoluwa)
- `docs/CLI.md` documented the pre-#48 `--proxies` behaviour — that a missing proxy file is
  silent — which is the opposite of what the code does now. The row and a new prose block
  distinguish the four real cases, including that the zero-yield warning fires for the
  implicit default too (#63, @Moferanoluwa)
- **The verification suite is a pytest suite.** The no-Chrome regression moved from a
  standalone script into `tests/test_no_chrome.py` with a shared `tests/conftest.py`, and CI
  runs `pytest tests/ -q` (#38, @pollychen-lab)

### Changed
- **The download engine exists once.** `download_file`, `Telemetry` and `ProxyPool` moved
  into `moon_download.py`; `moon_engine.py` and `moon_cli.py` now import them instead of each
  carrying their own copy (#41, @pollychen-lab). A fix in the download path is a one-file
  change from here on, and the two copies can no longer drift apart

### Fixed
- **The CI byte-compile list was hand-maintained and had drifted.** The job named
  "Byte-compile every module" compiled an explicit list that stopped at
  `tests/test_no_chrome.py`, so `moon_download.py` — the shared download engine both
  front-ends import — was never compiled, along with two newer test files. It now derives
  the list from `git ls-files '*.py'`, which cannot drift and pulls in nothing untracked.
  The workflow also watches its own file now, so a change to it is actually checked
  (#77, @darlenepolek)
- The eight `except Exception:` handlers in the first slice of `moon_extract.py` are
  narrowed or documented: the `curl_cffi` import guard catches `ImportError`, the
  Playwright probes catch `PlaywrightError`, and the ones that must stay broad say why.
  The deferred playwright import is preserved, so a fuckingfast-only batch still never
  imports it (#82, @AdvaitVarhade)
- **The elapsed-time clock kept counting after a run finished.** `snapshot()` computed
  `elapsed_s` from `now - t0` unconditionally, so the GUI carried on ticking once the last
  file had landed. The engine now records `_t_end` in `_on_done()` — the single exit point
  for both a normal finish and a crash — resets it in `start()`, and freezes `elapsed_s` at
  `t_end - t0` once a run has ended (#73, @RubenSanosh)
- `docs/ARCHITECTURE.md` still said `moon_engine.py` and `moon_cli.py` each carry their own
  copy of the download engine. #41 made that untrue; the file table and the closing summary
  now describe `moon_download.py` (#68, @Moferanoluwa)
- The three `except Exception:` handlers in `moon_engine.py` now say why they are there. The
  report-save handler is narrowed to `except (OSError, TypeError)` — a write failure or a
  non-serializable field — instead of swallowing everything after a run that succeeded
  (#71, @Moferanoluwa)
- `AUTHORS.md` linked to a contributor profile that no longer exists, and cited an issue
  number in a column that otherwise lists merged pull requests. The contribution stays
  credited; only the dead link and the reference were corrected (#67)
- The documented verification commands pointed at `python test_no_chrome.py`, which stopped
  existing when the tests moved. They now point at `pytest tests/` (#43, NanoRisk6)
- `native_dialog` swallowed every exception, including a broken `_DIALOG_SRC`. It now catches
  only `subprocess.TimeoutExpired` and `OSError`, so a real failure surfaces instead of
  returning an empty path. Four other deliberate swallows in `moon_bridge.py`, `moon_cli.py`
  and `moon_download.py` now say why they are there (#54, @AdvaitVarhade)
- **`--proxies` failed silently.** A misspelled path, or a file in a format the parser did not
  recognise, loaded zero proxies and printed nothing — so a run started specifically to avoid
  direct connections made them anyway, with no indication. `ProxyPool.load()` now returns
  `(loaded, skipped)` and warns when an explicitly passed file is missing, when a file parses
  to zero proxies, and how many lines it skipped. The implicit `proxies.txt` stays quiet when
  absent, which is the normal no-proxy state (#48, @AdvaitVarhade)

## [2.0] — V2

The GUI moved off tkinter. Both extraction methods were rebuilt in the 14.2–14.8 line
and no longer share a mechanism, so the interface stopped pretending they do.

### Added
- **New GUI on Edge WebView2** (`web/index.html`, `web/styles.css`, `web/app.js`) — Chromium
  rendering: real anti-aliasing, real alpha, gradients, blur, GPU transitions
- `moon_bridge.py` — loopback HTTP host with a per-run token; launches Edge/Chrome with `--app`
  (a window with no tabs and no address bar), OS file dialogs, atomic `settings.json`
- `moon_engine.py` — the download engine with no GUI attached: `start()` / `stop()` /
  `snapshot(cursor)` / `scan_tmp()`, all JSON-able
- `build_engine.py` — generator that produces `moon_engine.py` from a pristine `moon_tk.py`,
  so there is one source of truth for the engine
- **Live transfer rows** — progress ring, state, percentage and instantaneous speed per file,
  fed by live `FileRecord`s (`done_bytes` / `live_mbs`, published ~4 Hz on their own window,
  kept separate from the stall detector's 60 s history)
- **English / Italian** switch, English by default; the engine ships numbers and a stage name,
  the page writes the sentence
- Fluid type scale (`clamp()`): the interface scales with the window instead of staying at an
  8 px ink height on a 2560×1440 screen
- `test_no_chrome.py`, `integration_http.py`, `integration_web.py`, `render_gui.py` — the verification suite.
  `test_no_chrome.py` stubs Chrome and the network at the `moon_extract` boundary, so it needs no
  browser, no display and no Playwright install, and covers the engine, the CLI and (statically)
  `moon_tk.py`
- `moon_extract.BrowserGate` — the deferred launch: `get()` opens Playwright and Chrome on first
  demand, collapses concurrent first calls onto one instance, and tears both down in order
- Byte-based ETA, host split of the pasted links, per-host colouring in the link editor,
  `proxies.txt` count and `.tmp` resume count in the status bar

### Changed
- **Chrome is opened lazily** — on the first datanodes link, never before. The decision lives in
  `moon_extract.BrowserGate` and is shared by the WebView engine, the Tk GUI and the CLI
- `Captcha` default 240 s → **30 s**, `Pages` default 3 → **8**
- Settings and pasted links persist across restarts in `settings.json`
- Every value the GUI sends is coerced and clamped in `Engine.apply_cfg()` before it reaches
  a semaphore
- `moon_tk.py` (the tkinter GUI) still runs unchanged from `start_tk.bat`; the only edit it took is
  the lazy launch, so the two GUIs and the CLI cannot drift apart on it
- `moon_cli.py --browsers` is documented as what it always was: parallel extraction workers, not
  one browser each
- CI byte-compiles every module, runs `test_no_chrome.py`, and regenerates `moon_engine.py` from
  `moon_tk.py` to prove the two have not drifted
- **Files renamed** so every name is English and says what it is:
  `avvia.bat` → `start.bat`, `avvia_tk.bat` → `start_tk.bat`, `gen_1.py` → `moon_tk.py`,
  `gen_cli.py` → `moon_cli.py`, `apply_web_v16.py` → `build_engine.py`
- **The repository is English throughout** — launcher output, engine warnings, Tk labels, OS dialog
  titles, module docstrings and test assertions. The GUI's runtime EN/IT switch is unaffected
- Documentation restructured: the two Italian guides became
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
  [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) and
  [`docs/ENGINEERING_NOTES.md`](docs/ENGINEERING_NOTES.md), and the README carries a documentation
  index

### Fixed
- **fuckingfast batches launched Chrome.** Every front-end called `open_browser()` once per worker
  at the top of the run, before reading a single URL, so a pure-HTTP batch paid ~1.5 s of Playwright
  driver boot and put a Chrome window on screen — visible, because Turnstile issues no token to a
  headless build, so datanodes forces `headless=False` and every launch is therefore seen
- On the fallback path (no real Chrome found) each worker got its **own** Playwright Chromium while
  the extraction layer only ever used one shared context — N browsers, one of them used
- Transfer count showed the row cap (40) instead of the transfers in flight — a 124-file
  session reported "40 active"
- The Log tab rendered the transfer list on top of the log: `.files { display: grid }` outranks
  the user-agent `[hidden]` rule
- Progress rings always rendered empty: a CSS declaration beats an SVG presentation attribute,
  so `setAttribute("stroke-dasharray")` lost to the stylesheet
- Loopback API replied 403 without draining the request body, so the next keep-alive request on
  that connection was parsed as garbage and answered 501

### Removed
- **The tkinter GUI** (`moon_tk.py`, `start_tk.bat`) and **`build_engine.py`**, the generator
  that produced `moon_engine.py` from it. Keeping it meant maintaining a second interface for
  the same engine — and, worse, it made the legacy GUI the source of truth for the modern
  engine. `moon_engine.py` is now a normal module. `pillow` drops out of the requirements
  with it
- `apply_patch.py` — the v14.1 → v14.8 migration patcher. Against the current tree it
  half-applies instead of failing: in testing it silently reverted `moon_cli.py`'s imports
  to the pre-`BrowserGate` API
- `prep_assets.py` — one-shot asset builder whose inputs (the raw renders) were never in
  the repo; the assets it produced are committed
- Three orphan v14 screenshots in the repo root that nothing linked

## [15.0]

### Added
- `moon_ui.py` — the tkinter layer rebuilt from scratch: canvas-drawn cards, sliders, progress
  lanes, sparkline, status pill and per-file rows
- `apply_ui_v15.py` — exact-string patch that swaps the GUI layer and leaves the async engine
  byte-identical
- Generated brand assets (`assets/mark.png`, `assets/backdrop.png`) with `prep_assets.py`

### Known limits (the reason V2 exists)
- Tk's canvas has no anti-aliasing and no alpha channel: arcs, rounded corners and glows
  render as steps and bands
- Absolute type and geometry: on a large monitor the interface stays small and the layout
  does not redistribute

## [14.8]

### Changed
- **GUI settings split per method.** One "Browsers" slider described an architecture that no
  longer existed: fuckingfast opens no browser at all, datanodes is Chrome + Turnstile.
  Three panels instead: common, datanodes, fuckingfast
- datanodes knobs (`Pages`, captcha wait, Chrome path, API key) moved from environment
  variables to the GUI and are pushed into the extraction layer on every run through
  `moon_extract.configure()` — no more `setx` and restart

## [14.7]

### Changed
- Back to **one shared Chrome window**. Separate windows meant separate identities, and
  Cloudflare re-challenged each of them; one window and one profile means one `cf_clearance`

## [14.6]

### Fixed
- **The shared browser died after ~80 sequential extractions** and every later extraction
  stayed broken for the rest of the session, because nothing checked whether it was still
  alive. `open_browser()` now verifies `is_connected()` on every call and respawns the
  instance transparently
- Too many heavy tabs on one window slowed everything down: tabs are pooled per lane instead
  of opened per extraction

## [14.4]

### Added
- **fuckingfast.co over curl_cffi** — Chrome TLS fingerprint plus the `hx-redirect` header,
  ~0.25 s per link, no browser and no captcha. Without it Cloudflare answers 403 on every link
- **datanodes.to on real Chrome** driven over CDP with a persistent profile, instead of the
  Playwright Chromium: the profile is the point, because the Turnstile clearance survives
- Optional datanodes **premium API key** — a single JSON GET, no browser, no captcha
- `moon_extract.py` — the extraction layer split out of `gen_1.py`, shared by the GUI and the CLI

### Changed
- `curl_cffi` is now a hard requirement for fuckingfast.co

## [14.1] — V1

### Added
- Stall detection with automatic lane kills for genuinely slow downloads
- Per-URL retry with exponential backoff
- Live telemetry with `.log` and `.json` output
- CLI variant (`gen_cli.py`) for headless / multi-IP deployment
- Ad overlay bypass and popup dismissal on datanodes.to

### Changed
- Default browser worker count tuned to 16 for typical 40+ file sessions
- Improved dead-link detection so failures fail fast instead of timing out
- Resource blocking widened to cover more analytics/ad domains

### Fixed
- Resume interrupted downloads via `.tmp` files instead of restarting
- Range-header edge case when server returns 200 instead of 206

## [14.0]

### Added
- Initial public release
- datanodes.to and fuckingfast.co provider support
- Tkinter GUI with dual progress bars and color-coded log
