# FAQ

## Does this work on macOS or Linux?

The engine and the CLI do — they are Python + aiohttp + curl_cffi, plus Playwright for
datanodes. `start.bat` is Windows-only, and the GUI needs a Chromium browser to host the
`--app` window, so on macOS/Linux run `python moon_bridge.py --serve` and open the URL.
datanodes also needs a real Chrome/Edge for Turnstile; `find_chrome()` already looks in
the usual macOS and Linux locations.

## What happened to the tkinter GUI?

Removed in V2. It was a second interface to maintain for the same engine, and it was
also the file `moon_engine.py` used to be generated from — which made the legacy GUI the
source of truth for the modern engine. The generator went with it; `moon_engine.py` is a
normal module you can edit.

## Is it still single-file?

No. It was until 14.4, and the extraction rewrite ended that: `moon_extract.py` holds the
extraction layer, `moon_bridge.py` hosts the GUI, `web/` **is** the GUI, and `moon_cli.py`
is still standalone-runnable, which was the actual point of the old rule.

## Why does fuckingfast need `curl_cffi`?

Cloudflare fingerprints the TLS ClientHello. aiohttp's scores as a bot and gets a 403 on
every link no matter which headers it sends; `curl_cffi` impersonates Chrome's and gets
through. Downloads stay on aiohttp — `dl.fuckingfast.co` serves the file with full Range
support and no impersonation needed.

## Why does datanodes need a *visible* Chrome?

Turnstile does not issue a token to a headless build: the challenge platform answers 401
and `cf-turnstile-response` stays empty. It also rejects Playwright's Chromium, which is
not a Google-branded build. So the app spawns a real Chrome with a persistent profile and
drives it over CDP — the profile is the point, because the clearance survives between
links.

## Does Chrome open for fuckingfast links too?

No, since V2. `moon_extract.BrowserGate` launches on the first datanodes link and never
before — not even the Playwright driver's node process. `pytest tests/ -q` asserts
it for the engine and the CLI, and checks both sources.

## What's the difference between `moon_bridge.py` and `moon_cli.py`?

Same engine, two front-ends.

- `moon_bridge.py` + `web/` + `moon_engine.py` — the GUI: loopback HTTP, Edge/Chrome
  `--app` window
- `moon_cli.py` — argparse CLI, prints to stdout, for scripting and headless boxes

## What does `--browsers` / `Extractors` actually control?

Parallel extraction workers. It has not meant "one browser each" since 14.7: datanodes
runs on **one** shared Chrome window, and `Pages` (1–8) bounds how many tabs may be open
on it at once. fuckingfast opens nothing at all.

## Are proxies required?

No. Drop a `proxies.txt` next to the scripts to enable rotation:

```
ip:port:user:pass
http://user:pass@ip:port
```

The status bar shows how many were loaded.

## Do proxies hide me from the file host?

Only for the download. The pool wraps the download session and nothing else, so link
extraction — the datanodes Chrome window that answers the Turnstile challenge, and the
fuckingfast `curl_cffi` session — connects directly from your own address every time.

That is deliberate: pushing a real Chrome through a rotating pool makes the challenge
harder to pass, not easier, and the bandwidth that proxies are actually there for is all
in the download. But it means `PROXY 20` in the footer covers half the run, and the half
it does not cover is the half that identifies you to the host.

## Can I skip the captcha entirely?

With a datanodes **premium API key**, yes — extraction becomes one JSON GET, no browser,
no Turnstile. Set it in the GUI or `MOON_DN_API_KEY`. Free keys get 403 on `direct_link`
and fall back to Chrome.

## Can I add another provider?

Yes — write the extractor in `moon_extract.py`, then dispatch on the domain in
`moon_engine.py` and `moon_cli.py`. Ask `BrowserGate.get()` for a browser inside the branch
that needs it, never before. `docs/PROVIDERS.md` has the full checklist.

## Why isn't there a Docker image?

datanodes needs a visible, real Chrome for Turnstile, which is exactly what a container
is bad at. A fuckingfast-only container would work (no browser at all) — nothing is
published yet.

## Where do I report bugs?

[Bug report template](https://github.com/LeyckerS/moondownloader/issues/new?template=bug_report.yml).
Attach `moontech_*.log` (GUI) or `moontech_cli_*.log` (CLI) from the failed session.
