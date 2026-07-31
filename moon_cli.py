"""
MoonDownloader CLI  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━
Headless version for server / multi-IP deployment.

Usage:
    python moon_cli.py --urls links.txt --output /path/to/downloads
    python moon_cli.py --urls links.txt --output ./dl --browsers 8 --streams 24 --retries 3
"""
import os, re, sys, asyncio, threading, argparse, json, datetime
import time, random, traceback, collections, io
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict

import aiohttp

# ── TUNING (identical to GUI version) ─────────────────────────────────────────
RECV_CHUNK             = 4  * 1024 * 1024
WRITE_BUF              = 16 * 1024 * 1024
READ_BUFSZ             = 1  << 19

STALL_MIN_MBS          = 0.5
STALL_GRACE_S          = 90
STALL_CHECK_S          = 20
STALL_WIN_S            = 60
STALL_MAX_KILL         = 1
STALL_SAFE_PCT         = 0.80
STALL_MIN_BYTES_IN_WIN = 30 * 1024 * 1024
STALL_MIN_FILE_BYTES   = 50 * 1024 * 1024

DL_INNER_RETRIES       = 4

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
]

LAUNCH_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-gpu", "--disable-extensions", "--disable-background-networking",
    "--disable-default-apps", "--disable-sync", "--no-first-run", "--no-zygote",
    "--mute-audio", "--hide-scrollbars", "--disable-breakpad",
    "--disable-component-update", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def _sanitize_filename(name: str) -> str:
    name = _WIN_INVALID.sub("_", name).strip(". ")
    return name or "download"

# ── SHARED RESOURCES ───────────────────────────────────────────────────────────
_SESSION : aiohttp.ClientSession | None = None
_POOL    = ThreadPoolExecutor(max_workers=12, thread_name_prefix="dl_write")

def _sess() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        conn = aiohttp.TCPConnector(limit=0, limit_per_host=0, force_close=False,
                                    enable_cleanup_closed=True, ttl_dns_cache=600,
                                    keepalive_timeout=30)
        _SESSION = aiohttp.ClientSession(
            connector=conn, read_bufsize=READ_BUFSZ,
            timeout=aiohttp.ClientTimeout(total=7200, connect=20, sock_read=90))
    return _SESSION

async def _close_sess():
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close(); _SESSION = None

# ── PROXY POOL ─────────────────────────────────────────────────────────────────
class ProxyPool:
    def __init__(self):
        self.proxies : list[dict] = []
        self._idx    = 0
        self._lock   = threading.Lock()
        self._sessions : dict[str, aiohttp.ClientSession] = {}

    def load(self, path: str) -> int:
        if not os.path.exists(path): return 0
        loaded = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                try:
                    if line.startswith("http://") or line.startswith("https://") or line.startswith("socks"):
                        loaded.append({"url": line, "auth": None})
                    else:
                        parts = line.split(":")
                        if len(parts) == 4:
                            if re.match(r'^\d+\.\d+\.\d+\.\d+$', parts[0]):
                                ip, port, user, passwd = parts
                            else:
                                user, passwd, ip, port = parts
                            loaded.append({"url": f"http://{ip}:{port}",
                                           "auth": aiohttp.BasicAuth(user, passwd)})
                        elif len(parts) == 2:
                            ip, port = parts
                            loaded.append({"url": f"http://{ip}:{port}", "auth": None})
                except Exception: continue
        self.proxies = loaded
        return len(loaded)

    def next(self) -> dict | None:
        if not self.proxies: return None
        with self._lock:
            p = self.proxies[self._idx % len(self.proxies)]
            self._idx += 1
        return p

    def get_session(self, proxy: dict) -> aiohttp.ClientSession:
        key = proxy["url"]
        if key not in self._sessions or self._sessions[key].closed:
            conn = aiohttp.TCPConnector(limit=0, limit_per_host=0, force_close=True,
                                        enable_cleanup_closed=True, ttl_dns_cache=300)
            self._sessions[key] = aiohttp.ClientSession(
                connector=conn, read_bufsize=READ_BUFSZ,
                timeout=aiohttp.ClientTimeout(total=7200, connect=30, sock_read=120))
        return self._sessions[key]

    async def close_all(self):
        for s in self._sessions.values():
            if not s.closed:
                try: await s.close()
                except Exception: pass
        self._sessions.clear()

_PROXY_POOL = ProxyPool()

# ── TELEMETRY ──────────────────────────────────────────────────────────────────
@dataclass
class FileRecord:
    url          : str
    filename     : str
    worker_id    : int   = -1
    stall_kills  : int   = 0
    queued_at    : float = 0.0
    extract_s    : float = 0.0
    dl_start     : float = 0.0
    dl_s         : float = 0.0
    file_bytes   : int   = 0
    status       : str   = "pending"
    error        : str   = ""
    avg_mbs      : float = 0.0
    queue_wait_s : float = 0.0
    notes        : list  = field(default_factory=list)

class Telemetry:
    def __init__(self, cfg: dict):
        self.cfg       = cfg
        self.t0        = time.monotonic()
        self.t_end     = 0.0
        self.files     : dict[str, FileRecord] = {}
        self.snapshots : list[dict] = []
        self._lock     = threading.Lock()

    def reg(self, url: str, filename: str) -> FileRecord:
        rec = FileRecord(url=url, filename=filename, queued_at=time.monotonic())
        with self._lock: self.files[url] = rec
        return rec

    def snap(self, dls, qsize, ok, fail):
        self.snapshots.append({"ts": round(time.monotonic()-self.t0, 1),
            "downloads": dls, "queue": qsize, "ok": ok, "fail": fail})

    def finish(self): self.t_end = time.monotonic()

    def save(self, out_dir: str) -> tuple[str, str]:
        os.makedirs(out_dir, exist_ok=True)
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        lp   = os.path.join(out_dir, f"moontech_cli_{ts}.log")
        jp   = os.path.join(out_dir, f"moontech_cli_{ts}.json")
        el   = self.t_end - self.t0
        recs = list(self.files.values())
        ok_r = [r for r in recs if r.status == "ok"]

        buf = io.StringIO()
        def W(*parts): buf.write(" ".join(str(p) for p in parts) + "\n")
        W("="*72); W("MOONTECH CLI  --  PERFORMANCE LOG"); W("="*72)
        W(f"Duration : {int(el//60)}m {int(el%60)}s")
        if ok_r:
            tb = sum(r.file_bytes for r in ok_r)
            W(f"Total    : {tb/1e9:.2f} GB  @  {tb/el/1e6:.1f} MB/s")
        W(f"OK: {len(ok_r)}  /  Fail: {len(recs)-len(ok_r)}")
        W()
        W(f"{'#':<4} {'Filename':<48} {'DL':>7} {'Speed':>10} {'Status'}")
        W("-"*80)
        for i, r in enumerate(recs, 1):
            spd = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "--"
            W(f"{i:<4} {r.filename[:48]:<48} {r.dl_s:>7.1f} {spd:>10} {r.status}")
        W("="*72)

        with open(lp, "w", encoding="utf-8") as f: f.write(buf.getvalue())
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({"duration_s": round(el,2), "ok": len(ok_r),
                       "fail": len(recs)-len(ok_r),
                       "files": [asdict(r) for r in recs]}, f, indent=2)
        return lp, jp

# ── EXTRACTION ────────────────────────────────────────────────────────────────
# Both host front-ends changed in 2026; the extraction layer now lives in
# moon_extract.py so the engine and the CLI share one implementation.
from moon_extract import (                       # noqa: E402
    extract_fuckingfast,
    extract_datanodes,
    BrowserGate,
    close_ff_session,
    referer_for,
    HAVE_CURL_CFFI,
    DN_API_KEY,
    DN_LANES,
)
import moon_extract as _moon_extract            # noqa: E402

# The degraded no-curl_cffi fuckingfast path, and the lane pool's per-context user
# agent, both reuse this module's own HTTP/UA plumbing.
_moon_extract._sess = _sess
_moon_extract.USER_AGENTS = USER_AGENTS

if DN_API_KEY:
    print("datanodes: MOON_DN_API_KEY set - trying the API first "
          "(direct_link requires a premium account; free keys get 403 "
          "and fall back to the browser)")

if not HAVE_CURL_CFFI:
    print("WARNING: curl_cffi is not installed. fuckingfast.co will fail with "
          "Cloudflare 403 on every link \u2014 run: pip install curl_cffi",
          file=sys.stderr)

print(f"datanodes: up to {DN_LANES} persistent browser window(s) "
      "(set MOON_DN_LANES to change)")

# ── DOWNLOAD ───────────────────────────────────────────────────────────────────
class _StallKill(Exception): pass

async def download_file(
    proxy_url    : str,
    cookies      : str,
    dest         : str,
    rec          : FileRecord,
    bytes_acc    : collections.deque,
    kill_evt     : asyncio.Event,
    kills_so_far : int,
) -> tuple[bool, str, int]:
    """Download a single file with resume support, stall detection, and proxy rotation."""
    tmp    = dest + ".tmp"
    loop   = asyncio.get_running_loop()
    detect = kills_so_far < STALL_MAX_KILL

    def _write(f, data: bytes): f.write(data)

    for att in range(DL_INNER_RETRIES):
        resume = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        ref = referer_for(proxy_url)
        hdrs = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer":    ref,
            "Connection": "keep-alive",
        }
        if cookies: hdrs["Cookie"] = cookies
        if resume > 0: hdrs["Range"] = f"bytes={resume}-"

        proxy_cfg     = _PROXY_POOL.next()
        dl_session    = _PROXY_POOL.get_session(proxy_cfg) if proxy_cfg else _sess()
        dl_proxy      = proxy_cfg["url"]  if proxy_cfg else None
        dl_proxy_auth = proxy_cfg["auth"] if proxy_cfg else None

        try:
            dl_t0      = time.monotonic()
            req_kwargs = dict(headers=hdrs)
            if dl_proxy:
                req_kwargs["proxy"]      = dl_proxy
                req_kwargs["proxy_auth"] = dl_proxy_auth

            async with dl_session.get(proxy_url, **req_kwargs) as r:
                if r.status == 416:
                    if os.path.exists(tmp): os.replace(tmp, dest)
                    rec.file_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
                    return True, "ok", 0
                if r.status not in (200, 206):
                    return False, f"HTTP {r.status}", resume
                if r.status == 200 and resume > 0: resume = 0

                file_size = int(r.headers.get("Content-Length", 0)) + resume
                if file_size > 0: rec.file_bytes = file_size

                effective_detect = detect and (file_size == 0 or file_size >= STALL_MIN_FILE_BYTES)
                f = open(tmp, "ab" if resume > 0 else "wb")
                speed_win  = collections.deque(maxlen=8000)
                downloaded = resume
                last_check = dl_t0

                try:
                    buf: list[bytes] = []; bufsz = 0
                    async for chunk in r.content.iter_chunked(RECV_CHUNK):
                        if not chunk: break
                        if kill_evt.is_set(): raise _StallKill()
                        now         = time.monotonic()
                        downloaded += len(chunk)
                        speed_win.append((now, len(chunk)))
                        bytes_acc.append((now, len(chunk)))
                        buf.append(chunk); bufsz += len(chunk)
                        if bufsz >= WRITE_BUF:
                            data = b"".join(buf); buf = []; bufsz = 0
                            await loop.run_in_executor(_POOL, _write, f, data)

                        if effective_detect and (now - last_check) >= STALL_CHECK_S:
                            last_check = now
                            elapsed = now - dl_t0
                            if elapsed >= STALL_GRACE_S:
                                pct    = downloaded / file_size if file_size > 0 else 0.0
                                cutoff = now - STALL_WIN_S
                                while speed_win and speed_win[0][0] < cutoff:
                                    speed_win.popleft()
                                win_bytes = sum(b for _, b in speed_win)
                                if win_bytes >= STALL_MIN_BYTES_IN_WIN and pct < STALL_SAFE_PCT:
                                    win_s = max(now - speed_win[0][0], 1.0)
                                    spd   = win_bytes / win_s / 1e6
                                    if spd < STALL_MIN_MBS:
                                        kill_evt.set(); raise _StallKill()

                    if buf:
                        bytes_acc.append((time.monotonic(), sum(len(b) for b in buf)))
                        await loop.run_in_executor(_POOL, _write, f, b"".join(buf))
                finally:
                    f.close()

            os.replace(tmp, dest)
            dl_s = max(time.monotonic() - dl_t0, 0.001)
            net  = downloaded - resume
            if net > 0: rec.avg_mbs = net / dl_s / 1e6
            rec.file_bytes = rec.file_bytes or downloaded
            return True, "ok", 0

        except _StallKill:
            return False, "stall_killed", downloaded
        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError):
            rec.notes.append(f"connection dropped att {att+1}")
            if att < DL_INNER_RETRIES - 1: await asyncio.sleep(0.5*(att+1)); continue
            return False, "connection dropped", downloaded
        except asyncio.TimeoutError:
            rec.notes.append(f"timeout att {att+1}")
            if att < DL_INNER_RETRIES - 1: await asyncio.sleep(1+att); continue
            return False, "timeout", downloaded
        except Exception as e:
            err = str(e)
            rec.notes.append(f"error att {att+1}: {err}")
            if att < DL_INNER_RETRIES - 1 and ("ContentLengthError" in err or "not enough data" in err.lower()):
                await asyncio.sleep(0.5*(att+1)); continue
            return False, err, downloaded

    return False, "max retries", 0

# ── CLI ORCHESTRATION ──────────────────────────────────────────────────────────
def _fmt_speed(mbs: float) -> str:
    return f"{mbs:.1f} MB/s" if mbs >= 1 else f"{mbs*1024:.0f} KB/s"

async def run(urls: list[str], output_dir: str, n_workers: int,
              max_dl: int, max_retries: int, proxy_path: str):

    os.makedirs(output_dir, exist_ok=True)

    n_proxies = _PROXY_POOL.load(proxy_path)
    if n_proxies:
        print(f"[proxies] {n_proxies} loaded")

    q             = asyncio.Queue()
    dl_sem        = asyncio.Semaphore(max_dl)
    failed_urls   : list[str]   = []
    all_tasks     : list        = []
    tasks_lock    = asyncio.Lock()
    kill_counts   : dict[str,int] = {}
    bytes_acc     = collections.deque(maxlen=200000)
    lock          = threading.Lock()
    n_done        = 0
    all_done      = asyncio.Event()
    ok_count      = 0
    fail_count    = 0
    dls_active    = 0

    cfg = {"browsers": n_workers, "dl_streams": max_dl, "retries": max_retries,
           "total_links": len(urls)}
    telem = Telemetry(cfg)

    for url in urls:
        p = urlparse(url)
        raw_name = unquote(p.fragment or p.path.split("/")[-1]) or url
        rec = telem.reg(url, _sanitize_filename(raw_name))
        await q.put((url, 1, rec))

    def mark_done():
        nonlocal n_done
        n_done += 1
        if n_done >= len(urls): all_done.set()

    t0 = time.monotonic()

    # Progress printer (runs every 2s)
    stop_progress = asyncio.Event()
    async def progress_loop():
        while not stop_progress.is_set():
            await asyncio.sleep(2.0)
            snap = list(bytes_acc)
            now  = time.monotonic()
            cut  = now - 3.0
            recent = [(t, b) for t, b in snap if t > cut]
            mbs = 0.0
            if len(recent) > 1:
                span = max(now - recent[0][0], 0.05)
                mbs  = sum(b for _, b in recent) / span / 1_048_576
            total_dl = sum(b for _, b in snap)
            el = now - t0
            with lock:
                ok, dls = ok_count, dls_active
            print(f"  [{int(el//60):02d}:{int(el%60):02d}]  "
                  f"{ok}/{len(urls)} done  |  "
                  f"{dls} active  |  "
                  f"{_fmt_speed(mbs)}  |  "
                  f"{total_dl/1e9:.2f} GB", flush=True)

    # Kept in a local: a task with no reference can be collected mid-run.
    progress_t = asyncio.create_task(progress_loop())  # noqa: F841

    async def do_dl(proxy_url, cookies, filename, orig_url, rec):
        nonlocal ok_count, fail_count, dls_active
        async with dl_sem:
            with lock: dls_active += 1
            dest = os.path.join(output_dir, filename)

            if os.path.exists(dest):
                with lock:
                    ok_count += 1; dls_active -= 1
                print(f"  [exists] {filename}")
                rec.status = "ok"; rec.dl_s = 0.0
                mark_done(); return

            kc       = kill_counts.get(orig_url, 0)
            kill_evt = asyncio.Event()
            ok, msg, bytes_done = await download_file(
                proxy_url, cookies, dest, rec, bytes_acc, kill_evt, kc)
            rec.dl_s = max(time.monotonic() - rec.dl_start, 0.001)

            if ok:
                with lock: ok_count += 1
                spd = f"  ({rec.avg_mbs:.1f} MB/s)" if rec.avg_mbs > 0 else ""
                print(f"  [ok] {filename}{spd}")
                rec.status = "ok"; mark_done()
            elif msg == "stall_killed":
                new_kc = kc + 1; kill_counts[orig_url] = new_kc
                print(f"  [kill#{new_kc}] {filename}  ({bytes_done//(1<<20)}MB) -> re-extract")
                rec.queued_at = time.monotonic(); rec.status = "pending"
                await q.put((orig_url, 1, rec))
            else:
                with lock: fail_count += 1
                failed_urls.append(orig_url)
                rec.status = "fail"; rec.error = msg
                print(f"  [fail] {filename}: {msg}")
                mark_done()

            with lock: dls_active -= 1

    async def browser_worker(get_browser, wid):
        nonlocal ok_count, fail_count
        while True:
            if all_done.is_set() and q.empty(): break
            try:
                url, attempt, rec = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError: continue

            rec.worker_id = wid
            t_start = time.monotonic()
            rec.queue_wait_s = t_start - rec.queued_at
            filename = rec.filename
            is_re    = rec.stall_kills > 0
            suffix   = " [re-extract]" if is_re else (f" [retry {attempt}]" if attempt > 1 else "")
            print(f"  -> {filename[:60]}{suffix}")

            success = False
            try:
                parsed = urlparse(url)
                if "fuckingfast.co" in parsed.netloc:
                    link = await extract_fuckingfast(url)
                    rec.extract_s = time.monotonic() - t_start
                    if not link:
                        print("  [fail] No link found")
                    else:
                        rec.dl_start = time.monotonic()
                        async def _task(pu=link, fn=filename, ou=url, r=rec):
                            await do_dl(pu, "", fn, ou, r)
                        t = asyncio.create_task(_task())
                        async with tasks_lock: all_tasks.append(t)
                        success = True
                elif "datanodes.to" in parsed.netloc:
                    # API key set -> single JSON GET, no browser, no captcha.
                    # get_browser() is where Chrome is actually launched, so a
                    # batch with no datanodes link never opens one. After that
                    # extract_datanodes() re-validates the shared Chrome
                    # (respawning it if it died) and checks out one window from
                    # the persistent lane pool internally.
                    proxy_url, cookies = await extract_datanodes(await get_browser(), url)
                    rec.extract_s = time.monotonic() - t_start
                    if not proxy_url:
                        print("  [fail] No URL extracted")
                    else:
                        rec.dl_start = time.monotonic()
                        async def _task(pu=proxy_url, co=cookies, fn=filename, ou=url, r=rec):
                            await do_dl(pu, co, fn, ou, r)
                        t = asyncio.create_task(_task())
                        async with tasks_lock: all_tasks.append(t)
                        success = True
            except Exception as e:
                print(f"  [error] {e}")

            if not success and not is_re and attempt < max_retries:
                backoff = min(2**(attempt-1), 6)
                print(f"  [retry in {backoff}s]")
                await asyncio.sleep(backoff)
                rec.queued_at = time.monotonic()
                await q.put((url, attempt+1, rec))
                q.task_done(); continue

            if not success and not is_re:
                with lock: fail_count += 1
                failed_urls.append(url)
                rec.status = "fail"; mark_done()

            q.task_done()

    print(f"\n[start] {len(urls)} links  |  {n_workers} extractors  |  "
          f"{max_dl} streams  |  {max_retries} retries\n")

    # fuckingfast is pure HTTP: no browser, no profile, not even the Playwright
    # driver. Chrome opens on the first datanodes link and not before - a
    # fuckingfast-only batch never launches one.
    gate = BrowserGate(
        LAUNCH_ARGS,
        on_open=lambda: print("  [chrome] datanodes link found - starting Chrome", flush=True))

    try:
        await asyncio.gather(*[asyncio.create_task(browser_worker(gate.get, i))
                               for i in range(n_workers)])
    finally:
        await gate.aclose()

    async with tasks_lock:
        stragglers = [t for t in all_tasks if not t.done()]
    if stragglers:
        print(f"  [wait] {len(stragglers)} downloads still finishing...")
        await asyncio.gather(*stragglers, return_exceptions=True)

    stop_progress.set()
    await _close_sess()
    await close_ff_session()
    await _PROXY_POOL.close_all()
    telem.finish()

    base = os.path.dirname(os.path.abspath(__file__))
    lp, jp = telem.save(base)

    el = time.monotonic() - t0
    total_bytes = sum(b for _, b in bytes_acc)
    print(f"\n{'='*60}")
    print(f"Done in {int(el//60)}m {int(el%60)}s  |  "
          f"ok={ok_count}  fail={fail_count}  |  "
          f"{total_bytes/1e9:.2f} GB  @  {total_bytes/el/1e6:.1f} MB/s")
    print(f"Log: {os.path.basename(lp)}")

    if failed_urls:
        fp = os.path.join(base, "failed_links.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(failed_urls) + "\n")
        print(f"Failed ({len(failed_urls)}): {fp}")

    # Exit status for scripting: 0 = all ok, 1 = partial failure (see issue #32).
    return fail_count

# ── ENTRY POINT ────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns process exit code (0 / 1 / 2). See docs/CLI.md."""
    ap = argparse.ArgumentParser(
        description="MoonDownloader CLI — headless downloader for server deployment")
    ap.add_argument("--urls",     required=True,  help="Text file with one URL per line")
    ap.add_argument("--output",   required=True,  help="Output folder for downloaded files")
    ap.add_argument("--browsers", type=int, default=8,  help="Parallel extraction workers (default: 8)")
    ap.add_argument("--streams",  type=int, default=24, help="Concurrent download streams (default: 24)")
    ap.add_argument("--retries",  type=int, default=3,  help="Max retries per link (default: 3)")
    ap.add_argument("--proxies",  default="proxies.txt", help="Proxy list file (default: proxies.txt)")
    args = ap.parse_args(argv)

    # Pre-flight only → exit 2 (run never starts).
    if not os.path.exists(args.urls):
        print(f"ERROR: urls file not found: {args.urls}")
        return 2
    try:
        with open(args.urls, encoding="utf-8", errors="replace") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError as exc:
        print(f"ERROR: cannot read urls file: {args.urls}: {exc}")
        return 2

    if not urls:
        print("ERROR: no URLs found in file")
        return 2

    print(f"Loaded {len(urls)} URLs from {args.urls}")

    try:
        fail_count = asyncio.run(
            run(urls, args.output, args.browsers, args.streams,
                args.retries, args.proxies)
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 0
    except Exception:
        crash = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_log.txt")
        with open(crash, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        traceback.print_exc()
        return 1

    # Partial failure is exit 1; clean batch is 0. Batch is never aborted early.
    return 1 if fail_count else 0

if __name__ == "__main__":
    sys.exit(main())
