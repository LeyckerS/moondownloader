# Quick Start

Extended version of the README quick start, for a first run.

## Requirements

- **OS:** Windows 10 or 11 (primary). The GUI needs Edge or Chrome — both ship with
  Windows. macOS/Linux run the engine and the CLI fine, `start.bat` is Windows-only.
- **Python:** 3.10 or newer
- **Disk:** ~150 MB for the Playwright Chromium — only ever used as the datanodes
  fallback when no real Chrome is found. fuckingfast needs none of it.
- **Network:** any speed. The defaults assume fiber; lower `DL streams` on a slower link.

## Option 1 — One-click (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) with **"Add Python to
   PATH"** ticked.
2. Download or clone this repo.
3. Double-click **`start.bat`**.

First run installs `aiohttp`, `playwright` and `curl_cffi`, then the
Chromium build. `start.bat` then starts a loopback HTTP server and opens the GUI in
Edge (or Chrome) with `--app`: a window with no tabs and no address bar. There is no
native GUI dependency.

## Option 2 — Manual

```bash
git clone https://github.com/LeyckerS/moondownloader.git
cd moondownloader

pip install -r requirements.txt
playwright install chromium          # datanodes fallback only

python moon_bridge.py                # GUI
python moon_bridge.py --serve        # server only, prints the URL
```

`requirements.txt` installs the latest compatible dependency versions. For the exact
set tested by the project, use the reproducible install instead:

```bash
pip install -r requirements.txt -c constraints.txt
```

## Option 3 — CLI (headless)

```bash
python moon_cli.py --urls links.txt --output ./downloads --browsers 16 --streams 48
```

`--browsers` is the number of parallel extraction **workers**, not browsers: Chrome
opens once and only if a datanodes link shows up. `python moon_cli.py --help` for the
rest.

## Just want to look at the interface?

Open `web/index.html` in Chrome or Edge. It boots in demo mode against a synthetic
engine — no Python, no downloads.

## First run

1. Paste `datanodes.to` and/or `fuckingfast.co` links into the editor. The count and
   the per-host split update as you type.
2. Pick an output folder (defaults to `~/Downloads/datanodes`).
3. Leave the settings alone for a first run. If you want to tune:
   - **Extractors** (2–32) — parallel extraction workers
   - **DL streams** (2–48) — concurrent downloads; fewer means more bandwidth each
   - **Pages** (1–8) — datanodes only: tabs on the one shared Chrome window
   - **Captcha wait** — datanodes only: how long a manual Turnstile solve may take
4. **Download**, or switch to *Links only* to extract direct URLs without downloading.

A fuckingfast-only run never opens a browser window. The first datanodes link opens
one Chrome; solve the Turnstile there if it asks, and the clearance is reused for the
rest of the session.

## What a run leaves behind

Next to the scripts:

| File | Contents |
|:--|:--|
| `moontech_*.log` | human-readable performance report |
| `moontech_*.json` | per-file metrics |
| `output_links.txt` | extracted direct links (Links-only mode) |
| `failed_links.txt` | URLs that failed every retry |
| `settings.json` | GUI settings, pasted links, language |

Interrupted files stay as `.tmp` and resume on the next run via a Range request.
