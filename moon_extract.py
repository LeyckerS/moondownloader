"""
moon_extract.py — MoonDownloader extraction layer, rebuilt 2026-07-29.

The extraction layer, imported by moon_engine.py and moon_cli.py.
Both providers changed; the pre-14.4 code fails on every link for different reasons.

fuckingfast.co
    The landing page is Alpine + htmx now and the /dl/ URL is NOT in the HTML at
    all. It is only returned in the `hx-redirect` RESPONSE HEADER of
    POST /f/{id}/go. On top of that, Cloudflare fingerprints TLS: aiohttp's
    ClientHello scores as a bot and gets `cf-mitigated: challenge` -> 403 no matter
    which headers you send, while a Chrome-impersonating ClientHello sails through.
    Extraction therefore goes through curl_cffi; the download engine keeps aiohttp
    because dl.fuckingfast.co serves the file with full Range support.

datanodes.to
    1. The share URL 302s to /download and drops a `file_code` cookie.
    2. Step 1's form is present from the first byte but sits inside a collapsed
       `#downloadReveal` with a `disabled` submit; the Vue scan arms it at ~6s
       (site's own failsafe at 8s). Submitting at t=0 gets you nowhere.
    3. The submit button carries `name="method_free" value="Free Download >>"`.
       The server re-serves step 1 when that pair is missing from the POST body,
       and a synthetic click does not reliably register as the form's submitter.
    4. Exactly ONE POST /download is allowed. A second one re-runs SecSave server
       side and invalidates the token step 2 is holding, after which download2
       fails SecCheck and the server answers with HTML. The old extractor tripped
       this by calling form.submit() and then click-hunting "free download" text.
    5. Step 2 is a <download-countdown> Vue component with Cloudflare Turnstile
       (`:has-captcha="true"`) and adblock detection (`:detect-adblock="true"`).
       The old BLOCKED_DOMS list contains "challenges.cloudflare", so Turnstile
       could never load, and BLOCKED_RES contains "stylesheet", which collapses
       every getBoundingClientRect() to 0x0 and makes the button finder blind.

    Turnstile is the load-bearing change: it does not issue a token to a headless
    Chromium. Measured on chromium 131 (headless shell, --headless=new, and a
    persistent profile) the challenge platform answers 401 on
    /cdn-cgi/challenge-platform/h/b/pat/... every time and cf-turnstile-response
    stays empty indefinitely. datanodes therefore needs a non-headless browser —
    see dn_launch_kwargs() / prepare_datanodes_context() below.
"""

import asyncio
import os
import random
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse, unquote

DEBUG = bool(os.environ.get("MOON_DEBUG"))


def _d(*a):
    if DEBUG:
        print("   [extract]", *a, flush=True)


# ══ fuckingfast.co ════════════════════════════════════════════════════════════

FF_HOST        = "https://fuckingfast.co"
FF_ID_RE       = re.compile(r"^[A-Za-z0-9]{6,32}$")
FF_DL_RE       = re.compile(r"https://(?:dl\.)?fuckingfast\.co/dl/[A-Za-z0-9_\-]{16,}")
FF_RETRIES     = 3
FF_IMPERSONATE = "chrome"
FALLBACK_UA    = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

try:
    from curl_cffi import AsyncSession as _CurlSession
    HAVE_CURL_CFFI = True
except ImportError:                                  # pragma: no cover
    _CurlSession = None
    HAVE_CURL_CFFI = False

_FF_SESSION = None
_FF_LOCK    = asyncio.Lock()


async def ff_session():
    """Shared TLS-impersonating session; one instance keeps Cloudflare clearance warm.

    *curl_cffi binds its libcurl multi handle to the loop it is constructed on, so
    this must be built inside the running loop — never at import time.*
    """
    global _FF_SESSION
    if not HAVE_CURL_CFFI:
        return None
    async with _FF_LOCK:
        if _FF_SESSION is None:
            _FF_SESSION = _CurlSession(impersonate=FF_IMPERSONATE, timeout=30,
                                       max_clients=64)
        return _FF_SESSION


async def close_ff_session():
    """Call from the same shutdown path that runs _close_sess()."""
    global _FF_SESSION
    if _FF_SESSION is not None:
        try:
            await _FF_SESSION.close()
        except Exception:
            pass  # best-effort cleanup during shutdown
        _FF_SESSION = None


def ff_file_id(url: str) -> str | None:
    """Pull the file id out of any fuckingfast link shape.

    FitGirl links carry the filename as a URL FRAGMENT
    (`https://fuckingfast.co/smeekt12mped#Game.part01.rar`). urlparse drops the
    fragment; a naive rsplit on the raw string does not and yields a bogus id.
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    for part in reversed(path.split("/")):
        if FF_ID_RE.match(part):
            return part
    return None


def _ff_headers(file_id: str) -> dict[str, str]:
    page = f"{FF_HOST}/{file_id}"
    return {
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "HX-Request":      "true",
        "HX-Current-URL":  page,
        "Origin":          FF_HOST,
        "Referer":         page,
        "Sec-Fetch-Dest":  "empty",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Site":  "same-origin",
    }


async def extract_fuckingfast(url: str) -> str | None:
    """Resolve a fuckingfast.co share link to its direct dl.fuckingfast.co URL.

    No browser. Returns the direct URL, or None for an unparseable id, a dead
    file, or a challenge that survives FF_RETRIES. Measured 0.23-0.33s per link
    against live FitGirl parts; the returned URL answers 206 with a correct
    Content-Range and `Content-Disposition: attachment` under plain aiohttp.

    Depends on the host module for `_sess` and `USER_AGENTS` only in the degraded
    no-curl_cffi path.
    """
    file_id = ff_file_id(url)
    if not file_id:
        return None

    go_url = f"{FF_HOST}/f/{file_id}/go"
    hdrs   = _ff_headers(file_id)
    sess   = await ff_session()

    for attempt in range(FF_RETRIES):
        try:
            if sess is not None:
                r      = await sess.post(go_url, headers=hdrs, data=b"",
                                         allow_redirects=False)
                status = r.status_code
                target = r.headers.get("hx-redirect")
                body   = "" if target else r.text
            else:
                # Degraded: only reachable if the host ever drops the TLS challenge.
                # _sess / USER_AGENTS are provided by the host module.
                host_sess = globals().get("_sess")
                host_uas  = globals().get("USER_AGENTS") or [FALLBACK_UA]
                if host_sess is None:
                    return None
                async with host_sess().post(
                        go_url,
                        headers={**hdrs, "User-Agent": random.choice(host_uas)},
                        data=b"", allow_redirects=False) as r:
                    status = r.status
                    target = r.headers.get("hx-redirect")
                    body   = "" if target else await r.text()

            if target and "/dl/" in target:
                return target.strip()
            if status == 404 or "not found" in body[:400].lower():
                _d("ff dead file", file_id)
                return None
            m = FF_DL_RE.search(body)          # legacy shape, if they ever revert
            if m:
                return m.group()
            _d(f"ff {file_id}: status={status} no hx-redirect")
        except Exception as e:
            # network or parsing error triggers a retry
            _d(f"ff {file_id}: {type(e).__name__} {e}")

        if attempt + 1 < FF_RETRIES:
            await asyncio.sleep(0.6 * (attempt + 1))

    return None


# ══ datanodes.to ══════════════════════════════════════════════════════════════

# Only heavy media is dropped. Stylesheets MUST load: the button finder measures
# getBoundingClientRect(), and with CSS blocked every element collapses to 0x0.
# Ad hosts must load too — step 2 runs `:detect-adblock="true"`.
DN_BLOCKED_RES = {"image", "media", "font"}

DN_ALWAYS_ALLOW = (
    "challenges.cloudflare.com",   # Turnstile — step 2 cannot pass without it
    "cdn-cgi/challenge-platform",  # Cloudflare JS detections
    "datanodes.to",
)

# Pure telemetry only. Nothing ad-shaped, on purpose.
DN_BLOCKED_DOMS = {
    "google-analytics.com", "analytics.google.com", "stats.g.doubleclick.net",
    "hotjar", "clarity.ms", "facebook.com/tr",
}

DN_FILE_EXT = re.compile(
    r"\.(?:r(?:ar|\d{2})|zip|7z|tar|gz|bin|iso|exe|mkv|mp4|part\d+)(?:$|[?#])", re.I)

DN_STEP1_GATE_TIMEOUT = 22.0
DN_STEP2_TIMEOUT      = 420.0
# Auto-click budget. After this the widget is left alone so a human sitting at the
# headful window can tick it; DN_MANUAL_CAPTCHA_TIMEOUT is that grace period.
DN_CAPTCHA_AUTO_TIMEOUT   = 45.0
DN_MANUAL_CAPTCHA_TIMEOUT = float(os.environ.get("MOON_DN_CAPTCHA_WAIT", "240"))
DN_CAPTCHA_RECLICK        = 13.0

# ── datanodes official API (no captcha, no countdown, no browser) ─────────────
# One free click on https://datanodes.to/account mints a key. With MOON_DN_API_KEY
# set, extraction is a single JSON GET and the whole two-step Turnstile flow is
# skipped, which is the only sane way to pull a 40-part repack.
DN_API_KEY      = os.environ.get("MOON_DN_API_KEY", "").strip()
DN_API_ENDPOINT = "https://datanodes.to/api/file/direct_link"
DN_CODE_RE      = re.compile(r"^[A-Za-z0-9]{8,20}$")

DN_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}, loadTimes: () => {}, csi: () => {},
                                  app: {isInstalled: false}};
Object.defineProperty(navigator, 'plugins',
    {get: () => ({length: 5, 0: {}, 1: {}, 2: {}, 3: {}, 4: {}})});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
const _gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _gp.apply(this, arguments);
};
"""

DN_DEAD_JS = """() => {
    const t = (document.body?.innerText || '').toLowerCase();
    return t.includes('file not found') || t.includes('could not be found')
        || t.includes('file was deleted') || t.includes('has been removed')
        || t.includes('file expired') || t.includes('no such file');
}"""

DN_GATE_JS = """(force) => {
    const w = document.getElementById('downloadReveal');
    const b = document.getElementById('method_free');
    if (!w || !b) return {present: false, ready: false};
    const cs = getComputedStyle(w);
    let ready = !b.disabled && cs.pointerEvents !== 'none' && cs.opacity !== '0';
    if (!ready && force) {
        w.style.maxHeight = 'none'; w.style.opacity = '1';
        w.style.transform = 'none'; w.style.pointerEvents = 'auto';
        b.disabled = false;
        ready = true;
    }
    return {present: true, ready: ready};
}"""

# One POST /download, ever — a second invalidates the token step 2 holds.
DN_LATCH_JS = """() => {
    if (window.__moonLatched) return false;
    window.__moonLatched = true;
    const f = document.getElementById('downloadForm');
    if (f) {
        const native = f.submit.bind(f);
        let used = false;
        f.submit = function () { if (used) return; used = true; return native(); };
    }
    return true;
}"""

# `method_free` must be in the body or the server just re-serves step 1, and a
# synthetic click is not a reliable submitter here, so materialise the pair.
DN_SUBMIT_JS = """() => {
    const f = document.getElementById('downloadForm');
    if (!f) return false;
    if (!f.querySelector('input[type=hidden][name=method_free]')) {
        const i = document.createElement('input');
        i.type = 'hidden'; i.name = 'method_free'; i.value = 'Free Download >>';
        f.appendChild(i);
    }
    f.submit();
    return true;
}"""

DN_STEP2_JS = """() => {
    const t = (document.body?.innerText || '').toLowerCase();
    const tok = document.querySelector('[name="cf-turnstile-response"]');
    const captcha = document.querySelectorAll('.cf-turnstile').length > 0
                 || document.querySelectorAll('iframe[src*="challenges.cloudflare"]').length > 0;
    let trigger = null;
    for (const el of document.querySelectorAll('button, a, input[type=submit]')) {
        const label = (el.innerText || el.value || '').trim().toLowerCase();
        if (!label || label.length > 60 || el.disabled) continue;
        if (/start download|download now|get link|proceed to download|download file|free download/.test(label)) {
            const r = el.getBoundingClientRect();
            if (r.width * r.height > 0) { trigger = label; break; }
        }
    }
    return {
        step2:    t.includes('step 2 of 2') || t.includes('unlock your download'),
        captcha:  captcha,
        solved:   !!(tok && tok.value),
        // Turnstile's OWN error state -- distinct from "not solved yet". Waiting
        // out the normal captcha budget here just burns minutes on a widget that
        // will never recover on its own; the caller bails immediately instead.
        hardFail: t.includes('verification failed') || t.includes('challenge failed'),
        adblock:  t.includes('adblock') || t.includes('ad blocker'),
        starting: t.includes('starting download') || t.includes('preparing your download'),
        trigger:  trigger,
    };
}"""

DN_CLICK_JS = """(label) => {
    for (const el of document.querySelectorAll('button, a, input[type=submit]')) {
        const l = (el.innerText || el.value || '').trim().toLowerCase();
        if (l === label) { el.click(); return true; }
    }
    return false;
}"""

# Ad overlays hijack z-index on bare <div>s directly under body; the real UI is
# inside #app, so dropping unnamed body-level layers is safe.
DN_OVERLAY_JS = """() => {
    for (const el of document.querySelectorAll('body > div')) {
        if (el.id || el.className) continue;
        const s = el.getAttribute('style') || '';
        if (s.includes('z-index') || s.includes('position: fixed')) el.remove();
    }
    for (const el of document.querySelectorAll('body > iframe, body > ins')) el.remove();
}"""


DN_HEADLESS = False       # updated by dn_launch_kwargs(); read by the captcha step


def dn_launch_kwargs(launch_args: list[str], headless: bool | None = None) -> dict:
    """Launch kwargs for a datanodes-capable browser.

    Turnstile hands out no token to a headless Chromium — the challenge platform
    answers 401 on its /pat/ endpoint and cf-turnstile-response stays empty — so
    headless defaults to False here. Override with MOON_DN_HEADLESS=1 only if you
    have wired in a captcha solver.

    *`--disable-blink-features=AutomationControlled` alone is not enough; the flag
    hides one signal, and the init script in prepare_datanodes_context() covers
    navigator.webdriver / plugins / WebGL vendor.*
    """
    global DN_HEADLESS
    if headless is None:
        headless = os.environ.get("MOON_DN_HEADLESS") == "1"
    DN_HEADLESS = headless
    args = [a for a in launch_args if a not in ("--disable-gpu", "--no-zygote")]
    if "--disable-blink-features=AutomationControlled" not in args:
        args.append("--disable-blink-features=AutomationControlled")
    return {"headless": headless, "args": args}


async def prepare_datanodes_context(context) -> None:
    """Install the stealth init script on a context before any datanodes page loads."""
    from playwright.async_api import Error as PlaywrightError
    try:
        await context.add_init_script(DN_STEALTH_JS)
    except PlaywrightError:
        pass


async def _dn_eval(page, js, arg=None, default=None, tries: int = 4):
    """page.evaluate() that survives a navigation landing mid-call.

    *Step 1 is a form-POST navigation, so Playwright will raise "Execution context
    was destroyed" on any probe that overlaps it. Retry instead of trusting.*
    """
    from playwright.async_api import Error as PlaywrightError
    for i in range(tries):
        try:
            return await (page.evaluate(js, arg) if arg is not None else page.evaluate(js))
        except PlaywrightError as e:
            msg = str(e)
            if ("Execution context was destroyed" not in msg
                    and "Target closed" not in msg and "navigation" not in msg):
                return default
            await asyncio.sleep(0.35 * (i + 1))
    return default


def dn_is_file_url(candidate: str, landing_host: str, want_name: str | None,
                   self_urls: frozenset) -> bool:
    """True when a request looks like the final file handoff rather than page chrome.

    Deliberately not keyed on the literal string "dlproxy" — that token is what the
    old extractor waited on and it is the churn-prone part of the flow. The share
    URL ends in the filename too, so anything on the landing host needs an explicit
    handoff marker; without that guard the very first navigation matches its own
    filename, gets aborted by the route handler, and goto() dies with ERR_FAILED.
    """
    if len(candidate) < 40 or candidate in self_urls:
        return False
    low  = candidate.lower()
    p    = urlparse(candidate)
    host = p.netloc.lower()
    path = unquote(p.path)

    if path in ("", "/") or path.rstrip("/") in ("/download", "/premium", "/login"):
        return False

    base = landing_host[4:] if landing_host.startswith("www.") else landing_host
    if host in (landing_host, base, f"www.{base}"):
        return "dlproxy" in low or "/dl/" in path or path.startswith("/d/")

    if re.match(r"^(?:s\d+|dl\d*|cdn\d*|node\d*|fs\d+|files?)\.", host) and \
       (DN_FILE_EXT.search(path) or "/d/" in path or "/dl/" in path):
        return True
    if "dlproxy" in low or DN_FILE_EXT.search(path):
        return True
    if want_name and want_name.lower() in low:
        return True
    return False


def dn_file_code(url: str) -> str | None:
    """Extract the datanodes file_code from a share URL (`/{code}/{filename}`)."""
    for part in urlparse(url).path.strip("/").split("/"):
        if DN_CODE_RE.match(part):
            return part
    return None


async def extract_datanodes_api(url: str, api_key: str | None = None) -> str | None:
    """Resolve a datanodes link through the official API — no browser, no captcha.

    GET /api/file/direct_link?file_code=..&key=.. -> {"status":200,
        "result":{"url":"https://sN.datanodes.to/d/.../file.rar","size":N}}

    Returns None when no key is configured or the API refuses, so the caller can
    fall back to the browser flow.

    *The endpoint answers HTTP 200 even for failures — the real outcome is the JSON
    `status` field, so never branch on the HTTP code here.*
    """
    key = (api_key or DN_API_KEY).strip()
    code = dn_file_code(url)
    if not key or not code:
        return None

    target = f"{DN_API_ENDPOINT}?file_code={code}&key={key}"
    try:
        sess = await ff_session()
        if sess is not None:
            r = await sess.get(target)
            payload = r.json()
        else:
            host_sess = globals().get("_sess")
            if host_sess is None:
                return None
            async with host_sess().get(target) as r:
                payload = await r.json(content_type=None)
    except Exception as e:
        # swallow network/JSON errors and fall back to scraping
        _d("dn api error:", type(e).__name__, str(e)[:90])
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("status") != 200:
        msg = payload.get("msg") or "unknown error"
        if "not allowed" in str(msg).lower():
            _d("dn api: direct_link is premium-only on this account "
               "(account/info shows premium_expire: null) — falling back to the browser")
        else:
            _d(f"dn api rejected {code}: {msg}")
        return None
    direct = (payload.get("result") or {}).get("url")
    if direct:
        _d(f"dn api ok {code} -> {direct[:70]}")
        return direct
    return None


DN_WIDGET_BOX_JS = """() => {
    const d = document.querySelector('.cf-turnstile')
          || document.querySelector('iframe[src*="challenges.cloudflare"]')?.parentElement;
    if (!d) return null;
    d.scrollIntoView({block: 'center', behavior: 'instant'});
    const r = d.getBoundingClientRect();
    if (r.width < 40 || r.height < 20) return null;
    return {x: r.x, y: r.y, w: r.width, h: r.height};
}"""

DN_TOKEN_JS = """() => {
    const i = document.querySelector('[name="cf-turnstile-response"]');
    return i && i.value ? i.value.length : 0;
}"""

DN_HARD_FAIL_JS = """() => {
    const t = (document.body?.innerText || '').toLowerCase();
    return t.includes('verification failed') || t.includes('challenge failed');
}"""


async def _dn_click_turnstile(page) -> str:
    """Tick the Turnstile checkbox with a trusted input event.

    Turnstile is served in interactive mode here — it renders a 'Verify you are
    human' checkbox that must be clicked, and it will not solve on its own.

    *A JS `element.click()` produces an untrusted event that Turnstile ignores.
    Playwright's locator click and page.mouse.* both go through CDP
    Input.dispatchMouseEvent, which the widget accepts.*
    """
    from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
    # Preferred: let Playwright reach into the cross-origin challenge frame. Guard
    # on the iframe existing first — frame_locator waits out its full timeout per
    # selector otherwise, which burns 12s an attempt for nothing.
    has_frame = await _dn_eval(
        page,
        """() => document.querySelectorAll('iframe[src*="challenges.cloudflare"]').length""",
        default=0)
    if has_frame:
        for sel in ('input[type="checkbox"]', 'label', 'body'):
            try:
                frame  = page.frame_locator('iframe[src*="challenges.cloudflare"]')
                target = frame.locator(sel).first
                await target.click(timeout=4000)
                return f"frame:{sel}"
            except (PlaywrightError, PlaywrightTimeoutError):
                continue

    # Fallback: real mouse at the widget's checkbox, with some pointer entropy
    # first — Turnstile scores mouse movement, a teleporting cursor looks synthetic.
    box = await _dn_eval(page, DN_WIDGET_BOX_JS)
    if not box:
        return ""
    cx = box["x"] + 30
    cy = box["y"] + box["h"] / 2
    try:
        await page.mouse.move(cx - 140, cy - 70, steps=14)
        await asyncio.sleep(0.2)
        await page.mouse.move(cx - 40, cy - 12, steps=10)
        await asyncio.sleep(0.15)
        await page.mouse.move(cx, cy, steps=8)
        await asyncio.sleep(0.2)
        await page.mouse.click(cx, cy, delay=95)
        return f"mouse:({cx:.0f},{cy:.0f})"
    except (PlaywrightError, PlaywrightTimeoutError) as e:
        _d("turnstile click failed:", str(e)[:70])
        return ""


async def dn_solve_turnstile(page, headless: bool) -> bool:
    """Get a Turnstile token: auto-click first, then leave it to the human.

    Returns True once cf-turnstile-response is populated. On a headful window the
    user can always tick the box themselves, so a failed auto-click degrades to a
    prompt instead of a dead link. Bails immediately (does not burn the rest of
    its budget) the moment the widget shows its own "Verification failed" state —
    that is not a click problem, clicking it more will not help.
    """
    t0          = time.monotonic()
    attempts    = 0
    last_click  = 0.0
    prompted    = False
    budget      = DN_CAPTCHA_AUTO_TIMEOUT + (0.0 if headless else DN_MANUAL_CAPTCHA_TIMEOUT)

    while time.monotonic() - t0 < budget:
        if await _dn_eval(page, DN_TOKEN_JS, default=0):
            _d(f"turnstile solved after {time.monotonic() - t0:.1f}s "
               f"({attempts} auto-click attempt(s))")
            return True
        if await _dn_eval(page, DN_HARD_FAIL_JS, default=False):
            _d(f"turnstile hard-failed after {time.monotonic() - t0:.1f}s "
               f"({attempts} auto-click attempt(s)) — stopping, not retrying clicks")
            return False

        elapsed = time.monotonic() - t0
        if elapsed < DN_CAPTCHA_AUTO_TIMEOUT:
            if time.monotonic() - last_click > DN_CAPTCHA_RECLICK:
                last_click = time.monotonic()
                attempts  += 1
                how = await _dn_click_turnstile(page)
                _d(f"turnstile click attempt {attempts}: {how or 'no target'}")
        elif not prompted:
            prompted = True
            if headless:
                _d("turnstile unsolved and browser is headless — no human to ask")
                return False
            print("\n  >>> Tick the 'Verify you are human' checkbox in the browser "
                  f"window (waiting {int(DN_MANUAL_CAPTCHA_TIMEOUT)}s) <<<\n",
                  flush=True)

        await asyncio.sleep(0.6)

    _d("turnstile never solved")
    return False


async def _extract_datanodes_on_context(context, url: str,
                                        headless: bool) -> tuple[str | None, str | None]:
    """Drive the two-step browser flow on an already-open context.

    302 -> /download, ~6s scan reveal, one POST carrying method_free, then
    Turnstile + a ~15s countdown. Returns (None, None) for dead files, an
    unopened gate, or an unsolved Turnstile. Called by extract_datanodes(),
    which owns lane acquisition — this half owns only the page-level flow.
    """
    page      = await context.new_page()
    captured  = asyncio.Event()
    holder: list[str] = []

    landing_host = urlparse(url).netloc.lower()
    want_name    = unquote(urlparse(url).path).rsplit("/", 1)[-1] or None
    if want_name and not DN_FILE_EXT.search(want_name):
        want_name = None
    self_urls = frozenset({url, url.split("#")[0],
                           f"https://{landing_host}/download",
                           f"https://{landing_host}/download/"})

    def _take(candidate: str) -> bool:
        if captured.is_set() or not candidate:
            return False
        if dn_is_file_url(candidate, landing_host, want_name, self_urls):
            holder.append(candidate)
            captured.set()
            _d("captured", candidate[:110])
            return True
        return False

    async def on_route(route):
        req   = route.request
        u, rt = req.url, req.resource_type
        try:
            if _take(u):
                await route.abort()          # URL is all we need; aiohttp transfers
                return
            if any(a in u for a in DN_ALWAYS_ALLOW):
                await route.continue_()
                return
            if rt in DN_BLOCKED_RES or any(d in u for d in DN_BLOCKED_DOMS):
                await route.abort()
                return
            await route.continue_()
        except Exception:
            pass

    await page.route("**/*", on_route)
    # A same-tab navigation, a popup, or a real download event also carries the
    # URL, and none of those reliably reach the route handler.
    my_popups: list = []

    def _on_popup(pop):
        my_popups.append(pop)
        _take(pop.url)

    page.on("download", lambda d: _take(d.url))
    page.on("framenavigated", lambda f: _take(f.url))
    # page-scoped, not context-scoped: the context may be shared with other
    # workers, and context.on("page") both leaks a listener per call and would
    # hand us their tabs to close.
    page.on("popup", _on_popup)

    file_url = cookies_str = None
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not resp or resp.status >= 400:
            _d("goto failed", resp.status if resp else "no response")
            return None, None
        if await _dn_eval(page, DN_DEAD_JS, default=False):
            _d("dead link")
            return None, None

        # ── step 1: wait for the reveal gate, submit exactly once ──────────────
        gate     = {"present": False, "ready": False}
        t_gate   = time.monotonic()
        deadline = t_gate + DN_STEP1_GATE_TIMEOUT
        while time.monotonic() < deadline:
            gate = await _dn_eval(page, DN_GATE_JS, False,
                                  default={"present": False, "ready": False})
            if gate["ready"]:
                break
            if not gate["present"] and await _dn_eval(page, DN_DEAD_JS, default=False):
                return None, None
            await asyncio.sleep(0.3)
        if not gate["ready"]:
            # Same escape hatch the site ships for its own broken-bundle case.
            gate = await _dn_eval(page, DN_GATE_JS, True,
                                  default={"present": False, "ready": False})
            if not gate["ready"]:
                _d("step-1 gate never opened")
                return None, None
        _d(f"gate armed in {time.monotonic() - t_gate:.1f}s")

        await _dn_eval(page, DN_OVERLAY_JS)
        if not await _dn_eval(page, DN_LATCH_JS, default=False):
            return None, None
        if not await _dn_eval(page, DN_SUBMIT_JS, default=False):
            _d("step-1 form missing")
            return None, None
        _d("step 1 submitted (single POST, method_free present)")

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=25000)
        except Exception:
            pass
        if await _dn_eval(page, DN_DEAD_JS, default=False):
            return None, None

        # ── step 2: Turnstile + countdown, then one click on the trigger ───────
        for hard_fail_retry in range(2):        # one reload if Turnstile hard-fails
            clicked        = False
            solved_captcha = False
            last_sweep     = 0.0
            hard_failed    = False
            AD_SWEEP_EVERY = 1.0   # was 3.0s — ad popups on a shared window cost
                                    # real CPU/network while alive; close them sooner.
            deadline = time.monotonic() + DN_STEP2_TIMEOUT
            while time.monotonic() < deadline and not captured.is_set():
                now = time.monotonic()
                if now - last_sweep > AD_SWEEP_EVERY:
                    await _dn_eval(page, DN_OVERLAY_JS)
                    while my_popups:
                        ad = my_popups.pop()
                        try:
                            await ad.close()
                        except Exception:
                            pass
                    last_sweep = now

                st = await _dn_eval(page, DN_STEP2_JS)
                if st is None:
                    await asyncio.sleep(0.4)
                    continue
                if st["adblock"]:
                    _d("site reports adblock — unblock the ad hosts")
                    return None, None
                if st["hardFail"]:
                    _d("Turnstile returned 'Verification failed' — not a click "
                       "problem, the widget itself gave up")
                    hard_failed = True
                    break

                if st["captcha"] and not st["solved"]:
                    if solved_captcha:
                        # Widget reset or the token expired before the countdown ended.
                        solved_captcha = False
                    if not await dn_solve_turnstile(page, headless):
                        _d("TURNSTILE UNSOLVED — use real Chrome (MOON_CHROME_PATH) so "
                           "Cloudflare stops scoring the browser itself, then tick the box")
                        return None, None
                    solved_captcha = True
                    continue

                if st["trigger"] and not clicked:
                    await _dn_eval(page, DN_OVERLAY_JS)
                    if await _dn_eval(page, DN_CLICK_JS, st["trigger"], default=False):
                        _d("clicked step-2 trigger:", st["trigger"])
                        clicked = True
                await asyncio.sleep(0.4)

            if not hard_failed:
                break
            if hard_fail_retry == 0:
                # One reload: a fresh Turnstile widget instance sometimes clears a
                # transient hard-fail without needing a whole new context/cookie
                # jar. Second failure gives up and lets the caller's own
                # retry-with-backoff take over instead.
                _d("reloading the page once after a Turnstile hard-fail")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.0)
                except Exception:
                    return None, None
            else:
                return None, None

        if not captured.is_set():
            try:
                await asyncio.wait_for(captured.wait(), 12.0)
            except asyncio.TimeoutError:
                pass

        if holder:
            file_url    = holder[0]
            cookies_str = "; ".join(f"{c['name']}={c['value']}"
                                    for c in await context.cookies())
    except Exception as e:
        _d("datanodes:", type(e).__name__, str(e)[:120])
    finally:
        for ad in my_popups:
            try:
                await ad.close()
            except Exception:
                pass
        try:
            await page.close()
        except Exception:
            pass

    return file_url, cookies_str


async def extract_datanodes(browser, url: str,
                            headless: bool | None = None) -> tuple[str | None, str | None]:
    """Resolve a datanodes.to link; return (file_url, cookie_header).

    Takes the API fast path when MOON_DN_API_KEY is set (no browser, no captcha).
    Otherwise re-validates the shared Chrome (respawning it if it died — see
    ensure_live_browser), checks out one window from the small persistent lane
    pool (see acquire_lane), and runs the browser flow on it.

    Call this with the `browser` handle from open_browser() directly — do not
    pre-build a context yourself; lane lifetime and the crash-respawn path both
    depend on owning that step.
    """
    api_link = await extract_datanodes_api(url)
    if api_link:
        return api_link, None

    if headless is None:
        headless = DN_HEADLESS
    browser = await ensure_live_browser(browser)

    async with acquire_lane(browser) as context:
        return await _extract_datanodes_on_context(context, url, headless)


# ══ real-Chrome / CDP browser ═════════════════════════════════════════════════
# Playwright's bundled Chromium gets caught by Turnstile: the launcher adds
# automation switches, the binary is not a Google-branded build, and the profile
# is empty. The result is "Verification failed / Error 600010" in the widget.
#
# Attaching over CDP to a Chrome WE spawned ourselves avoids all three: no
# --enable-automation, a real branded build, and a persistent profile that keeps
# the cf_clearance cookie between links so later files are challenged less.

import json
import shutil
import subprocess
import sys
import urllib.request

CDP_PORT      = int(os.environ.get("MOON_CDP_PORT", "9222"))
CHROME_PATH   = os.environ.get("MOON_CHROME_PATH", "").strip()
USE_REAL_CHROME = os.environ.get("MOON_REAL_CHROME", "1") != "0"

# Bounded CONCURRENCY on the one shared, persistent browser context/profile —
# not separate windows. Two things were tried and measured against each other:
#
# v14.6 gave every lane its OWN browser.new_context() (a fresh, CDP-created,
# incognito-style identity — separate cookies from the persistent profile).
# Real-world result: WORSE. Multiple distinct browsing identities hitting
# Cloudflare from the same IP in quick succession reads as bot-farm behaviour,
# and Turnstile started returning a hard "Verification failed" (the exact
# widget-level error the very first Playwright-Chromium attempt produced) on
# top of being slower overall.
#
# v14.4/14.5's single shared context (all pages = tabs on ONE persistent
# profile, ONE cookie jar, ONE fingerprint) is what actually passed Cloudflare
# reliably. Its only real problems were (a) no bound on how many heavy tabs
# (full Turnstile iframe + ads) could be open at once, which is what crashed
# the browser after ~80 sequential extractions, and (b) no recovery if it did.
#
# So: back to ONE context, but with DN_LANES now meaning "how many pages may be
# open on it at once" — a plain concurrency cap, not a pool of distinct
# identities. Combined with the crash-respawn in ensure_live_browser(), this
# keeps the Cloudflare-friendly single-identity behaviour while still bounding
# memory/CPU regardless of the "Browsers" GUI setting.
DN_LANES = max(1, min(int(os.environ.get("MOON_DN_LANES", "3") or 3), 8))

_CHROME_PROC   : subprocess.Popen | None = None
_CDP_BROWSER   = None
_BROWSER_LOCK  = asyncio.Lock()
_PW_INSTANCE            = None    # stashed so a mid-run respawn needs no caller change
_LAST_LAUNCH_ARGS: list = []
_lanes: list            = []      # BrowserContext per lane; lane 0 is the persistent profile
_lane_queue             = None    # asyncio.Queue[int] of free lane indices
_lanes_lock             = asyncio.Lock()


def default_profile_dir() -> str:
    """A dedicated profile — never the user's live Chrome profile.

    *Chrome refuses --remote-debugging-port on a --user-data-dir that another
    Chrome process already has open, so pointing this at the daily-driver profile
    silently produces a browser you cannot attach to.*
    """
    env = os.environ.get("MOON_CHROME_PROFILE", "").strip()
    if env:
        return env
    base = (os.environ.get("LOCALAPPDATA")
            or os.path.expanduser("~/.cache"))
    return os.path.join(base, "MoonDownloader", "chrome-profile")


def find_chrome() -> str | None:
    """Locate a real Chrome/Edge build. Returns None when only Chromium exists."""
    if CHROME_PATH and os.path.exists(CHROME_PATH):
        return CHROME_PATH

    candidates: list[str] = []
    if sys.platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(var)
            if not root:
                continue
            candidates += [
                os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates += ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                       "/usr/bin/microsoft-edge", "/opt/google/chrome/chrome"]

    for c in candidates:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _cdp_alive(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as r:
            return json.load(r).get("webSocketDebuggerUrl")
    except Exception:
        return None


async def _spawn_chrome(exe: str, port: int, profile: str, headless: bool) -> bool:
    """Start Chrome with a debugging port and nothing that smells of automation."""
    global _CHROME_PROC
    os.makedirs(profile, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,OptimizationHints",
        "--disable-dev-shm-usage",   # small containers OOM on /dev/shm well before RAM
        "--window-size=1440,960",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")
    if sys.platform not in ("win32", "darwin"):
        args.insert(1, "--no-sandbox")

    creationflags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
    _CHROME_PROC = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=creationflags)
    for _ in range(60):
        await asyncio.sleep(0.5)
        if _cdp_alive(port):
            return True
        if _CHROME_PROC.poll() is not None:
            return False
    return False


async def open_browser(pw, launch_args: list[str], headless: bool | None = None):
    """Return a browser for the extraction workers, preferring real Chrome over CDP.

    Every worker shares the one Chrome instance — one profile means one
    cf_clearance cookie, and Cloudflare stops re-challenging after the first
    solve instead of treating each worker as a brand-new visitor.

    Stashes `pw`/`launch_args` so a mid-run crash can be repaired from inside
    ensure_live_browser() without the caller doing anything differently — a
    worker in moon_engine.py/moon_cli.py fetches its `browser` handle once at startup
    and holds it for the whole run, so recovery has to happen underneath that
    stale reference, not by asking the caller to fetch a new one.
    """
    global _CDP_BROWSER, _PW_INSTANCE, _LAST_LAUNCH_ARGS
    _PW_INSTANCE      = pw
    _LAST_LAUNCH_ARGS = list(launch_args)
    kw = dn_launch_kwargs(launch_args, headless)

    if not USE_REAL_CHROME:
        return await pw.chromium.launch(**kw), False

    async with _BROWSER_LOCK:
        if _CDP_BROWSER is not None:
            if _CDP_BROWSER.is_connected():
                return _CDP_BROWSER, True
            _d("shared Chrome disconnected — respawning "
               "(this is what used to fail every extraction for the rest of the run)")
            await _reset_shared_locked()

        exe = find_chrome()
        if not exe:
            print("MoonDownloader: no real Chrome found, falling back to Playwright's "
                  "Chromium (Turnstile will most likely fail). Set MOON_CHROME_PATH.",
                  file=sys.stderr)
            return await pw.chromium.launch(**kw), False

        port, profile = CDP_PORT, default_profile_dir()
        if not _cdp_alive(port):
            ok = await _spawn_chrome(exe, port, profile, kw["headless"])
            if not ok:
                print(f"MoonDownloader: could not start {exe} on port {port}; "
                      "falling back to Playwright's Chromium.", file=sys.stderr)
                return await pw.chromium.launch(**kw), False
        try:
            _CDP_BROWSER = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        except Exception as e:
            print(f"MoonDownloader: CDP attach failed ({str(e)[:70]}); "
                  "falling back to Playwright's Chromium.", file=sys.stderr)
            return await pw.chromium.launch(**kw), False

        _d(f"attached to real Chrome: {exe} (port {port}, profile {profile})")
        return _CDP_BROWSER, True


async def _reset_shared_locked() -> None:
    """Tear down the dead shared browser + its lanes. Caller already holds _BROWSER_LOCK."""
    global _CDP_BROWSER
    await _drop_lanes()
    _CDP_BROWSER = None
    await shutdown_chrome()


async def ensure_live_browser(browser):
    """Re-validate the shared Chrome before use, respawning it if it died.

    A worker captures its `browser` handle once at `_launch(wid)` time and reuses
    it for every URL it processes for the rest of the run. On an 85-file live run
    the shared Chrome process died after ~80 sequential extractions — every
    worker still holding that stale handle would otherwise fail
    `browser.new_context()` with "Target page, context or browser has been
    closed" for every remaining file, forever. Calling this at the top of every
    extract_datanodes() re-derives a live handle transparently; callers never see
    the respawn.
    """
    if not USE_REAL_CHROME or not is_shared_browser(browser):
        return browser
    if browser.is_connected():
        return browser
    if _PW_INSTANCE is None:
        return browser
    fresh, _ = await open_browser(_PW_INSTANCE, _LAST_LAUNCH_ARGS or ["--no-sandbox"])
    return fresh


def is_shared_browser(browser) -> bool:
    """True when this handle is the one CDP-attached Chrome shared by all workers."""
    return _CDP_BROWSER is not None and browser is _CDP_BROWSER


async def close_browser(browser, shared: bool | None = None) -> None:
    """Close a per-worker browser; leave the shared CDP Chrome alone."""
    if shared is None:
        shared = is_shared_browser(browser)
    if shared:
        return
    try:
        await browser.close()
    except Exception:
        pass


async def shutdown_chrome() -> None:
    """Detach from and terminate the spawned Chrome. Call once, at the very end."""
    global _CDP_BROWSER, _CHROME_PROC
    await _drop_lanes()
    if _CDP_BROWSER is not None:
        try:
            await _CDP_BROWSER.close()
        except Exception:
            pass
        _CDP_BROWSER = None
    if _CHROME_PROC is not None:
        try:
            _CHROME_PROC.terminate()
            _CHROME_PROC.wait(timeout=8)
        except Exception:
            try:
                _CHROME_PROC.kill()
            except Exception:
                pass
        _CHROME_PROC = None


# ── deferred launch ───────────────────────────────────────────────────────────
async def _start_playwright():
    """Boot the Playwright driver.

    Its own function so a batch that never touches datanodes never imports
    playwright at all, and so the regression test can count driver boots.
    """
    from playwright.async_api import async_playwright
    return await async_playwright().start()


class BrowserGate:
    """The one browser every extraction worker shares, opened on first demand.

    fuckingfast.co is resolved over plain HTTPS with a Chrome TLS fingerprint
    (~0.25 s per link): no browser, no profile, not even the Playwright driver's
    node process. The front-ends used to call open_browser() once per worker at
    the top of _run(), before reading a single URL, so a batch of nothing but
    fuckingfast links still paid ~1.5 s of driver boot and put a Chrome window on
    screen - visible, because Turnstile hands no token to a headless build, so
    datanodes needs headless=False and every launch is therefore seen.

    get() is the only thing that launches, and only the datanodes branch calls
    it: no datanodes link, no browser. Concurrent first calls collapse onto one
    instance behind the lock, which is what the shared cf_clearance profile needs
    anyway.

    *`Playwright.stop()` must run on the loop that called `start()` - keep a gate
    inside a single asyncio.run()/engine run, never hand one across two.*
    """

    __slots__ = ("_args", "_headless", "_on_open", "_lock", "_pw", "_browser", "_shared")

    def __init__(self, launch_args: list[str], *, headless: bool | None = None,
                 on_open=None) -> None:
        self._args     = list(launch_args)
        self._headless = headless
        self._on_open  = on_open      # fired once, immediately before the launch
        self._lock     = asyncio.Lock()
        self._pw       = None
        self._browser  = None
        self._shared   = False

    @property
    def opened(self) -> bool:
        return self._browser is not None

    async def get(self):
        """The shared browser, launching Playwright + Chrome on the first call."""
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is None:
                if self._on_open is not None:
                    self._on_open()
                pw              = await _start_playwright()
                browser, shared = await open_browser(pw, self._args, self._headless)
                self._pw        = pw
                self._browser   = browser
                self._shared    = shared
            return self._browser

    async def aclose(self) -> None:
        """Tear down browser then driver, in that order. No-op if nothing opened."""
        async with self._lock:
            browser, pw, shared = self._browser, self._pw, self._shared
            self._browser = None
            self._pw      = None
            if browser is not None:
                if shared:
                    await shutdown_chrome()      # lanes, CDP handle, the process
                else:
                    await close_browser(browser, False)
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass


# ── persistent shared context, bounded concurrency ─────────────────────────────
# ONE context for everything (see the note above DN_LANES for why). _lanes holds
# exactly that one context; _lane_queue is a pool of DN_LANES concurrency permits
# that all point at it, so up to DN_LANES pages can be open on it at once and the
# rest simply queue — no separate identities, no separate windows.

async def _shared_context(browser):
    if browser.contexts:
        return browser.contexts[0]          # the persistent, on-disk profile
    uas = globals().get("USER_AGENTS")       # set by the host module
    ctx = await browser.new_context(
        user_agent=uas[0] if uas else None,
        viewport={"width": 1360, "height": 900}, locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
    await prepare_datanodes_context(ctx)
    return ctx


async def _ensure_lanes(browser) -> None:
    global _lane_queue
    async with _lanes_lock:
        if _lane_queue is not None:
            return
        _lanes.append(await _shared_context(browser))
        n = DN_LANES if is_shared_browser(browser) else 1
        _lane_queue = asyncio.Queue()
        for _ in range(n):
            _lane_queue.put_nowait(0)
        _d(f"datanodes: 1 shared window, up to {n} concurrent page(s)")


async def _drop_lanes() -> None:
    global _lanes, _lane_queue
    for ctx in _lanes:
        try:
            await ctx.close()
        except Exception:
            pass
    _lanes = []
    _lane_queue = None


@asynccontextmanager
async def acquire_lane(browser):
    """Check out one concurrency permit against the single shared context.

    Blocks (awaits, does not spin) once DN_LANES pages are already open — the
    caller is one of up to `Browsers` concurrent workers, and there are only
    DN_LANES permits, so excess workers queue here instead of piling more tabs
    onto the window than it can take. All permits share the SAME context/cookie
    jar on purpose: splitting into separate contexts is what caused Cloudflare
    to start hard-failing verification (see the note above DN_LANES).
    """
    await _ensure_lanes(browser)
    if not _lanes:
        # Total fallback (no context at all could be created): one-shot context,
        # not pooled — better than failing outright.
        ctx = await browser.new_context(
            viewport={"width": 1360, "height": 900}, locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
        await prepare_datanodes_context(ctx)
        try:
            yield ctx
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
        return

    await _lane_queue.get()
    try:
        yield _lanes[0]
    finally:
        _lane_queue.put_nowait(0)


def configure(*, lanes: int | None = None, chrome_path: str | None = None,
              api_key: str | None = None, captcha_wait: int | None = None,
              headless: bool | None = None) -> dict:
    """Apply UI-supplied settings at runtime, overriding the env-var defaults.

    Every knob here was previously read ONCE at import time, which meant the only
    way to change it was `setx` + restarting the process. The GUI calls this right
    before a run starts so what is on screen is what actually runs.

    Lane count takes effect on the next pool creation. That is not a caveat in
    practice: every run ends in shutdown_chrome(), which drops the pool, so the
    value on screen is live for the run you just pressed START on.

    Returns the effective settings, for logging.
    """
    global DN_LANES, CHROME_PATH, DN_API_KEY, DN_MANUAL_CAPTCHA_TIMEOUT, DN_HEADLESS

    if lanes is not None:
        DN_LANES = max(1, min(int(lanes), 8))
    if chrome_path is not None:
        CHROME_PATH = chrome_path.strip()
    if api_key is not None:
        DN_API_KEY = api_key.strip()
    if captcha_wait is not None:
        DN_MANUAL_CAPTCHA_TIMEOUT = float(max(0, int(captcha_wait)))
    if headless is not None:
        DN_HEADLESS = bool(headless)

    return {
        "lanes": DN_LANES,
        "chrome": CHROME_PATH or (find_chrome() or "not found"),
        "api_key": bool(DN_API_KEY),
        "captcha_wait": int(DN_MANUAL_CAPTCHA_TIMEOUT),
        "headless": DN_HEADLESS,
        "curl_cffi": HAVE_CURL_CFFI,
    }


# ══ download-side referer ═════════════════════════════════════════════════════

def referer_for(proxy_url: str) -> str:
    """Referer the CDN expects for a given extracted URL.

    *dl.fuckingfast.co is a distinct host from the landing page, and it wants the
    landing origin — the old `"fuckingfast" in proxy_url` substring test happens to
    still work, but keying on the parsed host is what actually holds.*
    """
    host = urlparse(proxy_url).netloc.lower()
    if "fuckingfast" in host:
        return "https://fuckingfast.co/"
    return "https://datanodes.to/"
