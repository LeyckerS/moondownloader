# Configuration

Every setting, where it lives, and what it actually controls.

---

## GUI settings

The panel used to be one "Browsers" slider for two methods that no longer share anything:
fuckingfast opens no browser at all (pure HTTP via `curl_cffi`), datanodes is Chrome +
Turnstile. It is split in three.

### Common — both methods

| Setting | Range | Default | What it does |
|:--|:--:|:--:|:--|
| `Extractors` | 2–32 | 16 | how many extractions run in parallel |
| `DL streams` | 2–48 | 48 | concurrent download connections |
| `Retries` | 0–5 | 3 | extraction retries per URL (network retries are separate) |

> **On stream count.** Measured on the same line: 48 streams gave 31.9 MB/s aggregate at
> ~1.9 MB/s per file with ~17 active; 8 streams gave 62.2 MB/s aggregate at 8–13 MB/s per
> file with 5 active. The ceiling is **your total bandwidth**, divided by the number of
> streams — not a per-connection cap on the free tier. Fewer streams means more bandwidth
> per file and, in that comparison, a higher aggregate too.

### fuckingfast.co — HTTP, no browser

No settings. The panel only reports status: `curl_cffi` active (green) or missing (red).
If it is missing every link gets a 403, and you see it immediately instead of finding out
from the log.

### datanodes.to — Chrome + Turnstile

| Setting | Range | Default | What it does |
|:--|:--:|:--:|:--|
| `Pages` | 1–8 | 8 | tabs open at once **on the one shared window/identity** |
| `Captcha s` | 30–600 | 30 | how long a manual Turnstile solve may take |
| `Chrome` | path | autodetect | `chrome.exe` to drive over CDP, with a file picker |
| `API key` | string | — | datanodes premium key, masked in the field |

**None of this needs `setx` any more.** Each value used to be read once at process start,
so the only way to change it was an environment variable plus a restart. The GUI calls
`moon_extract.configure()` immediately before a run: what is on screen is what runs.
Environment variables still work as defaults.

The start banner reports what is in use per host:

```
▶  85 links  ·  16 extractors  ·  8 streams  ·  3 retries  ·  v2.0
   fuckingfast: direct HTTP   ·   datanodes: 8 pages, captcha 30s
   chrome: C:\Program Files\Google\Chrome\Application\chrome.exe
```

---

## Environment variables

All optional. The GUI overrides them at run time; the CLI reads them directly.

| Variable | Default | What it does |
|:--|:--|:--|
| `MOON_CHROME_PATH` | *(auto)* | path to `chrome.exe` when autodetection misses it |
| `MOON_CHROME_PROFILE` | `%LOCALAPPDATA%\MoonDownloader\chrome-profile` | the dedicated profile |
| `MOON_REAL_CHROME` | `1` | `0` = go back to Playwright's Chromium |
| `MOON_CDP_PORT` | `9222` | the debugging port |
| `MOON_DN_LANES` | `3` | pages open at once on the shared window (1–8) |
| `MOON_DN_API_KEY` | *(empty)* | datanodes key (`direct_link` needs premium) |
| `MOON_DN_HEADLESS` | `0` | `1` = headless — **the captcha will not solve** |
| `MOON_DN_CAPTCHA_WAIT` | `240` | seconds to wait for a manual solve |
| `MOON_DEBUG` | *(off)* | `1` = trace every gate of the extraction |

`setx` only affects **new** processes: after setting a variable you have to close and
reopen the prompt (or relaunch `start.bat` from a fresh one), otherwise nothing appears to
change.

> `MOON_DN_LANES` does **not** open more windows. It bounds how many heavy pages
> (Turnstile + ads) stay open at once on the one shared window. Setting it very high is
> what broke Cloudflare in 14.6 — see `ENGINEERING_NOTES.md`.

---

## The Chrome profile

datanodes runs on a **dedicated** profile, never your daily-driver one:

```
%LOCALAPPDATA%\MoonDownloader\chrome-profile
```

*Chrome refuses `--remote-debugging-port` on a `--user-data-dir` that another Chrome
process already has open, so pointing this at your everyday profile silently produces a
browser you cannot attach to.*

Chrome is launched with **only** these arguments — every extra flag is a signal:

```
--remote-debugging-port=9222
--user-data-dir=%LOCALAPPDATA%\MoonDownloader\chrome-profile
--no-first-run --no-default-browser-check
--disable-features=Translate,OptimizationHints
--disable-dev-shm-usage
--window-size=1440,960
```

All workers share **one** Chrome and **one** context, so there is one `cf_clearance`:
solve the captcha once and Cloudflare stops re-challenging on later links instead of
treating every worker as a new visitor. The profile survives between runs — the more you
use it, the less you get challenged.

---

## The datanodes API key

`file/direct_link` is **premium-only**. A free key authenticates fine
(`account/info` → `status:200`) and then answers:

```json
{"msg":"This function not allowed in API","status":403}
```

`file/download`, `file/dl`, `file/link`, `file/url`, `file/get` and `file/direct` all
return `Invalid operation`. There is no free endpoint that hands out a direct link.

Leaving the key set is still worth it: the extractor tries it first, so the day the
account goes premium extraction becomes instant — one JSON GET, no browser, no captcha.

---

## Optional files

Place them next to the scripts:

| File | Purpose |
|:--|:--|
| `proxies.txt` | proxy list — `ip:port:user:pass` or `http://user:pass@ip:port`. **downloads only**, see below |
| `settings.json` | written by the GUI: settings, pasted links, language |

### What proxies cover

The pool is consulted in exactly one place — `download_file` in `moon_download.py`, where
`_PROXY_POOL.next()` picks the entry the download session is built from. Nothing in
`moon_extract.py` takes a proxy: the Chrome instance datanodes needs for its Turnstile
challenge is launched with no `--proxy-server`, and the `curl_cffi` session fuckingfast
uses is constructed without one. Both connect directly.

So a run with `proxies.txt` loaded has the file host seeing your own address for every
page load and your proxy's for the bytes. Rotation is bandwidth cover, not identity
cover — if you need the extraction half proxied too, that is not something this file can
do for you today.

The practical symptom: put unreachable proxies in the list and pages still open, while
downloads fail. That is the design working, not the proxy system failing.

## Output files

| File | Contents |
|:--|:--|
| `moontech_*.log` | human-readable performance report |
| `moontech_*.json` | per-file metrics |
| `moontech_cli_*.log` / `.json` | the same, from the CLI |
| `output_links.txt` | extracted direct links (Links-only mode) |
| `failed_links.txt` | URLs that failed every retry |
