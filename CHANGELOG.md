# Changelog

All notable changes to Moon Downloader will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Versioning.** The public releases are **V1** (tag `v14.1`), **V2** (tag `v2.0`) and
> **V3** (tag `v3.0`). V2 is where the numbering reset: the 14.x and 15.0 entries below keep
> the numbers they shipped with, and `v14.1` keeps its tag so its download link never
> breaks. V3 is a major because the interface was rebuilt, not because the engine changed —
> extraction, downloading and the CLI are the same code they were in 2.1.

## [Unreleased]

## [3.0] — 2026-08-02

### Added
- **`constraints.txt`**, the tested dependency set including transitives, for a reproducible
  install: `pip install -r requirements.txt -c constraints.txt`. `docs/QUICKSTART.md`
  documents both modes (#136, @PomPomSaturin)
- **A `dependency-contract` CI job** that fails when any runtime requirement lacks an upper
  bound, and installs both the constrained and unconstrained sets under `pip check`. The
  bounds were added once in #12 and silently lost in an unrelated engine rewrite; this is what
  stops that happening again (#136, @PomPomSaturin)
- CI now runs on Python **3.13 and 3.14** as well as 3.10-3.12. The project advertises 3.10+
  and two of the versions covered by that promise had never been exercised — including the
  one development happens on. All five pass (#129, @Vam-si-krish)
- **A cold open.** The mark draws itself on as a vector, the wordmark resolves out of a
  blur, a raking beam crosses the field, and the card cascade is *held* — not merely
  delayed — until it hands over. Under two seconds, ended instantly by any key or click,
  and removed from the DOM when it finishes.
- **The mark is an SVG and it moves.** Crescent and orbit are separate shapes, so the ring
  tilts continuously: the minor radius is what animates, which reads as a rotation in
  depth. The same drawing now serves the topbar, the cold open and the empty state, and
  the PNG survives only as the favicon.
- **A hero band replaces the four stat cards.** The rate is a 49px figure with the plot
  spanning the card underneath; completed, downloaded and ETA are reference and sit small.
- **The settings column is one surface.** Five floating cards became four sections of a
  single panel divided by a hairline, with Start built into the same object.
- **Transfer rows carry their provider**, read off the source URL, and their weight follows
  their state: what is moving gets room and a brighter name, what has finished tightens
  and recedes.
- **Filter box and state chips over the transfer list.** Presentation only — rows are
  hidden, and no count the engine owns is recomputed.
- **Drag and drop** of links or a `.txt` anywhere on the window, appended rather than
  replacing what is already there.
- **Keyboard shortcuts** — `Ctrl+Enter`, `Ctrl+O`, `/`, `?`, `Esc` — with a shortcut panel.
- **Copy the source link per row**, and an end-of-run summary that reads the last snapshot
  and can copy every failed link.
- **Idle motion.** The blooms drift, a glint travels the topbar hairline, the wordmark
  catches a sheen and Start shows one while it waits. All slow, and none of them claiming
  that anything is happening.

### Changed
- **One higher-contrast palette.** The dimmest text tier moves from `#55677f` to `#97a9c1`
  and the background photograph is pushed back — a real legibility gain in a bright room.
- **The speed plot.** The head dot leaves the SVG: the plot is drawn with
  `preserveAspectRatio="none"`, which was rendering a `<circle>` as a flat ellipse. The
  area wash comes down, and a recessive hairline marks the window mean, since a plot
  scaled to its own peak cannot otherwise tell steady from spiky. It is drawn only when
  there is signal to average.
- **View Transitions** on the Transfers/Log swap, behind a capability check.
- **Scroll-driven edge fades** on the scrollers, applied to the edge that actually has
  content past it; when a list does not overflow there is no mask at all.
- **The `prefers-reduced-motion` block is gone, deliberately.** Windows reports "reduce"
  whenever its own animation setting is off, and every version of honouring that here
  removed the thing the release is for — first the whole design, then the cold open, then
  the logo orbit and the wordmark sheen. The GUI now animates for everyone. Nothing
  flashes and every loop is slow, but this is a stated choice rather than an oversight,
  and a motion preference is the obvious thing to add if anyone asks for one.
- `VERSION` was still `v2.0` after the 2.1 release, so the app under-reported itself for a
  whole version. It now tracks the tag again, at `v3.0`.

### Fixed
- **Runtime dependencies have upper bounds again** — `aiohttp>=3.9,<4`, `playwright>=1.40,<2`,
  `curl_cffi>=0.7,<1`, with the lower bounds left where they were. Dependabot is set to
  `increase-if-necessary` so it stops proposing floor-only raises, which forbid old versions
  rather than permitting new ones (#136, @PomPomSaturin)
- **Every `except Exception:` in the extraction and engine layers now names what it catches or
  says why it cannot.** The last slice narrows the datanodes page flow to `PlaywrightError`
  and the CDP probe to `(URLError, OSError, JSONDecodeError, KeyError)`, completing the
  30-handler cleanup begun in #54 across four slices and three contributors
  (#130, @AdvaitVarhade)
- **Nothing said that proxies cover downloads only.** `_PROXY_POOL.next()` is consulted in one
  place — `download_file` — so the download session is proxied and nothing else is: the
  datanodes Chrome that answers the Turnstile challenge and the fuckingfast `curl_cffi`
  session both connect directly, from the user's own address, on every run. `README.md`,
  `docs/CLI.md`, `docs/CONFIGURATION.md` and `docs/FAQ.md` now state the scope, and name the
  symptom it produces: pages opening normally while every download fails is an unusable proxy
  list, not a broken extractor (#128, @basisworks)
- **Closing the app mid-run left Chrome alive.** Teardown only happened when a run ended
  normally: `Engine.stop()` set a flag and returned, the worker ran on a daemon thread, and
  the interpreter tore it down before `BrowserGate.aclose()` could execute — so the browser
  survived, still holding its profile lock. `stop()` now closes the gate from the caller's
  thread and waits briefly for the worker, and the gate refuses to reopen once closed, so a
  late worker cannot launch a second Chrome on the way out (#122, @Guflly)
- **The preview mock no longer ships identifiable sample content.** Its filenames named a
  specific title and a repack site, its link list carried them in readable URLs, and its
  destination path was a real one — and every screenshot in the README is rendered from
  that mock, so all of it was on display on the project front page. The sample data is now
  neutral (`sample-archive.partNN.rar`, opaque link ids, `D:\downloads`) and the
  screenshots have been regenerated from it.
- The mock reported `v2.0 - preview`, so the screenshots taken for the V3 release announced
  the previous version.

## [2.1] — 2026-08-01

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
- **Two links resolving to the same filename shared one `.tmp` and corrupted each other.** The
  destination was derived from the filename alone, so two transfers opened the same partial
  file and raced to rename it — visible on Windows as `WinError 32`, silent on POSIX, where
  `os.replace` does not refuse and the interleaved survivor was reported `ok`. Names are now
  reserved case-insensitively when each record is registered, before either transfer is
  scheduled, and the rename is reported in both front-ends (#119, @felix-windsor)
- **Mid-transfer failures never reached the screen.** A dead proxy, a dropped connection or a
  refused resume wrote a note that only appeared in `moontech_*.log` after the run, so a run
  sat at `0 KB/s` with no explanation. `download_file` now takes an optional `on_event`
  callback; the engine passes its log, the CLI prints, and the report keeps everything it had.
  Also fixes an `UnboundLocalError` when a connection failed before the first byte arrived
  (#120, @XEDAB)
- The eight `except Exception:` handlers in the Chrome-lifecycle slice of `moon_extract.py`
  are narrowed or explained; the CDP attach now catches `PlaywrightError` and the Chrome
  process teardown `(subprocess.TimeoutExpired, OSError)` (#118, @AashishGupta2007)
- **Datanodes extraction hung on every link and failed silently.** The host now serves step 2
  as a chain — *Free Download / Standard Speed* reveals *Start Download / Your file is ready*,
  and only the second starts the transfer — while the trigger handler latched after one click.
  The page was never unreadable: `DN_STEP2_JS` matched the new button on every poll and the
  latch discarded it, so each link burned the full 420s budget and returned nothing. The
  handler now tracks which labels it has clicked instead of whether it has clicked, so each
  new trigger in the chain gets exactly one press, capped by `DN_STEP2_MAX_CLICKS` (#111)
- **An unsupported host was retried like a network failure.** A link from any host the
  program does not handle matched neither dispatcher branch, so nothing ran, nothing was
  logged, and the URL went back on the queue to be tried again with backoff — burning two
  extra attempts and a worker slot on something that could never succeed, then reporting it
  as an ordinary failure. It now fails on the first dispatch with a message naming the host
  and the supported ones, and `moontech_*.log` records it distinctly from an extraction
  failure. The host names now live once in `moon_extract.py` instead of four times
  (#108, @Vam-si-krish)
- The headless CLI example in `README.md` documented `python moon_cli.py <url> ... -o <folder>`:
  positional URLs the parser never accepted, and a `-o` short flag that does not exist. It now
  reads `--urls` / `--output`, which are the real flags and both required (#53, @tomatotomata)
- **The documentation guard could not see the line it was written to catch.** `FLAG_RE` in
  `tests/test_docs_cli_flags.py` matched only `--long` flags, so the bogus `-o` above passed
  every check. It now validates single-dash flags against the parser as well (#104, @kocaemre)
- **A partial download was discarded in silence.** When a server answers `200` instead of
  `206` it is refusing the resume request, and the code correctly restarts from zero — but
  said nothing, so a multi-gigabyte transfer appeared to begin again for no reason. The
  restart is now recorded on the file's record and appears in `moontech_*.log`, with a
  regression test that fakes the `200` response without touching the network
  (#98, @XEDAB)
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
