# Troubleshooting

## `start.bat` says "Python not found"

Python is missing, or not on `PATH`.

1. Reinstall from <https://www.python.org/downloads/>
2. Tick **"Add Python to PATH"** on the first installer screen
3. Open a fresh Command Prompt: `python --version` must print 3.10 or higher

## The GUI window never appears

`start.bat` launches Edge (or Chrome) with `--app` against a loopback server. Run
`python moon_bridge.py --serve`, open the printed URL in any Chromium browser, and
read the console: a missing dependency or a refused port shows up there.

The server exits by itself after 12 s with no requests — the page polls every 80 ms,
so "no requests" means "the window is closed". If your browser was still opening
slowly, just run it again.

## `fuckingfast.co` returns 403 on every link

`curl_cffi` is missing. Cloudflare fingerprints the TLS ClientHello, and aiohttp's
scores as a bot no matter which headers it sends.

```bash
pip install curl_cffi
```

The engine prints a warning at startup when the import fails.

## `datanodes.to`: "Verification failed" / Error 600010 in the Turnstile widget

Turnstile is refusing the browser, not the account.

- Make sure a **real Chrome or Edge** is installed. Playwright's Chromium is not a
  Google-branded build and Turnstile rejects it. Set the path explicitly in the GUI,
  or `MOON_CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe`.
- Do not force headless. `MOON_DN_HEADLESS=1` breaks Turnstile by design — the
  challenge platform answers 401 and the token never arrives.
- Lower **Pages** to 1–2. Many tabs solving challenges at once from one IP looks like
  a farm.
- Solve one manually if it asks: the clearance lands in the persistent profile and the
  rest of the session reuses it.

## Chrome opens even though I only pasted fuckingfast links

Fixed in V2. `moon_extract.BrowserGate` launches on the first datanodes link and never
before, in the WebView GUI and the CLI alike.

If you still see it, you are running pre-V2 files: check that `moon_extract.py`
contains `class BrowserGate` and that `moon_engine.py` / `moon_cli.py` contain no bare
`open_browser(` call. `pytest tests/ -q` answers this in a
second.

## Chrome will not attach: "could not start chrome.exe on port 9222"

Chrome refuses `--remote-debugging-port` on a `--user-data-dir` that another Chrome
process already has open.

- The app uses its own profile (`%LOCALAPPDATA%\MoonDownloader\chrome-profile`), so this
  usually means a leftover MoonDownloader Chrome. Close it, or kill the process.
- Port 9222 taken by something else: `MOON_CDP_PORT=9333`.
- Never point `MOON_CHROME_PROFILE` at your daily-driver profile — you get a browser
  you cannot attach to.

## Downloads stall at low speed and get killed

Two different things look identical:

1. **A slow datanodes lane.** The CDN pins your session to a lane; some are slow. The
   stall killer re-extracts to get a new one, up to `STALL_MAX_KILL` times.
2. **A saturated uplink.** If the pipe is already full, every stream looks stalled.
   Lower **DL streams**.

## Extraction dies partway through a long session

Fixed in 14.6. The shared Chrome used to die after ~80 sequential extractions and every
later link failed with "Target page, context or browser has been closed".
`ensure_live_browser()` now re-validates and respawns it transparently. If you see that
message again, attach `moontech_*.log` to a bug report.

## Where are my logs?

Next to the scripts:

- `moontech_YYYYMMDD_HHMMSS.log` — human-readable
- `moontech_YYYYMMDD_HHMMSS.json` — per-file metrics
- CLI runs write `moontech_cli_*.log` / `.json`

Attach the `.txt` (or `.log`) to any bug report. `MOON_DEBUG=1` adds extraction-level
tracing.
