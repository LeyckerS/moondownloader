# Contributing to Moon Downloader

Thanks for your interest in contributing!

## How to contribute

1. **Fork** the repository
2. **Create a branch** for your feature or fix
3. **Test** your changes against both providers (datanodes.to and fuckingfast.co)
4. **Run the verification suite** (below) — it is fast and catches the regressions that
   actually happened
5. **Submit a pull request** describing what changed and why

## Where to start

Looking for something to pick up:

- **[good first issue](https://github.com/LeyckerS/moondownloader/labels/good%20first%20issue)** —
  scoped small, with the files to touch and the acceptance criteria already written out
- **[help wanted](https://github.com/LeyckerS/moondownloader/labels/help%20wanted)** — everything
  open to outside contributors, including the larger items

Each issue says up front whether it needs Windows. Several do not — documentation, CI and dependency
work all run anywhere, and the no-Chrome test suite stubs the browser and the network at the
`moon_extract` boundary so it runs on any OS.

Comment on an issue before you start, so two people don't write the same patch.

## Architecture

Two front-ends, one engine, one extraction layer.

```
moon_bridge.py     loopback HTTP + token, launches Edge/Chrome --app, OS dialogs
  web/             index.html · styles.css · app.js        the GUI
  moon_engine.py   the engine with no GUI: start/stop/snapshot
    moon_extract.py  datanodes (real Chrome over CDP) · fuckingfast (curl_cffi)
                     BrowserGate: the launch, deferred until a datanodes link
    moon_download.py download_file · Telemetry · ProxyPool

moon_cli.py        argparse CLI, same engine, same extraction layer
```

Layers inside the engine:

- **Extraction** — `moon_extract.py`, shared by all three front-ends
- **Download engine** — `moon_download.py`, shared by the GUI engine and CLI
- **Telemetry** — `moon_download.py`, 1 Hz snapshots, `.txt` + `.json` output
- **GUI** — `web/` over the loopback API, hosted by `moon_bridge.py`

## Rules that are not style preferences

- **Shared logic goes in `moon_extract.py` or `moon_download.py`**, not copy-pasted between
  front-ends. If a change touches extraction, the Chrome lifecycle, downloading,
  telemetry or proxy rotation, it must land in one place and be visible from both
  `moon_engine.py` and `moon_cli.py`.
- **Never open a browser before you know you need one.** Ask `BrowserGate.get()` inside
  the provider branch that requires it. A launch at the top of a run is the bug
  `tests/test_no_chrome.py` exists to prevent.
- **No new dependencies without a strong reason.** The stack is deliberately small:
  `aiohttp`, `playwright`, `curl_cffi`.
- **English only.** Code, comments, log lines, dialog titles and docs. The GUI's EN/IT
  dictionary in `web/app.js` is the one exception — that is the runtime language switch.

## Verification

```bash
pytest tests/ -q               # no browser for fuckingfast, exactly one for datanodes
python integration_http.py     # browser -> loopback HTTP -> engine
python integration_web.py      # pywebview path
python render_gui.py out/           # GUI renders + overflow audit
```

`tests/test_no_chrome.py` stubs Chrome and the network at the `moon_extract` boundary, so it
needs no browser, no display and no Playwright install.

Live testing: at least 10 links per provider, including one guaranteed-dead one so
dead-link detection is exercised, and one session long enough (40+ files) to hit the
concurrency paths.

## Reporting bugs

Use the [bug report template](https://github.com/LeyckerS/moondownloader/issues/new?template=bug_report.yml)
and attach `moontech_*.log` (GUI) or `moontech_cli_*.log` (CLI). `MOON_DEBUG=1` adds
extraction-level tracing.

## Coding style

- 4-space indentation, no tabs.
- f-strings over `%` or `.format()`.
- Top-level constants uppercase (`RECV_CHUNK`, `WRITE_BUF`, `DN_LANES`).
- No blanket `except:` — name the exception, or `except Exception:` with a comment when
  the swallow is deliberate.
- Comments explain **why**, not what. The gotchas in `moon_extract.py` are the model:
  each one states a specific fact that cost a debugging session.
- Match the surrounding style. Read the nearby code first.
