"""
MoonDownloader CLI  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━
Headless version for server / multi-IP deployment.

Usage:
    python moon_cli.py --urls links.txt --output /path/to/downloads
    python moon_cli.py --urls links.txt --output ./dl --browsers 8 --streams 24 --retries 3
"""
import os, sys, asyncio, threading, argparse
import time, traceback, collections
from urllib.parse import urlparse, unquote

from moon_download import (
    LAUNCH_ARGS,
    Telemetry,
    VERSION,
    _PROXY_POOL,
    _close_sess,
    _sanitize_filename,
    download_file,
    RunFatalControl,
)

# ── EXTRACTION ────────────────────────────────────────────────────────────────
# Both host front-ends changed in 2026; the extraction layer now lives in
# moon_extract.py so the engine and the CLI share one implementation.
from moon_extract import (                       # noqa: E402
    extract_fuckingfast,
    extract_datanodes,
    BrowserGate,
    close_ff_session,
    HAVE_CURL_CFFI,
    DN_API_KEY,
    DN_LANES,
    DATANODES_HOST,
    FUCKINGFAST_HOST,
    SUPPORTED_HOSTS,
)

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

# ── CLI ORCHESTRATION ──────────────────────────────────────────────────────────
def _fmt_speed(mbs: float) -> str:
    return f"{mbs:.1f} MB/s" if mbs >= 1 else f"{mbs*1024:.0f} KB/s"

async def run(urls: list[str], output_dir: str, n_workers: int,
              max_dl: int, max_retries: int, proxy_path: str, is_default_proxies: bool = False):

    os.makedirs(output_dir, exist_ok=True)

    n_proxies, skipped = _PROXY_POOL.load(proxy_path, is_default=is_default_proxies)
    if n_proxies or skipped:
        print(f"[proxies] {n_proxies} loaded, {skipped} skipped")

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
    fatal_control = RunFatalControl()

    cfg = {"browsers": n_workers, "dl_streams": max_dl, "retries": max_retries,
           "total_links": len(urls)}
    telem = Telemetry(cfg, flavor="cli")

    for url in urls:
        p = urlparse(url)
        raw_name = unquote(p.fragment or p.path.split("/")[-1]) or url
        filename = _sanitize_filename(raw_name)
        rec = telem.reg(url, filename)
        if rec.filename != filename:
            print(f"  [rename] {filename} -> {rec.filename} (filename collision)")
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
            try:
                await asyncio.wait_for(stop_progress.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            if stop_progress.is_set():
                break
            snap = list(bytes_acc)
            now  = time.monotonic()
            cut  = now - 3.0
            recent = [(t, b) for t, b in snap if t > cut]
            mbs = 0.0
            if len(recent) > 1:
                span = max(min(3.0,now - t0),0.05)
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
            if fatal_control.is_set():
                rec.status = "aborted"
                return
            with lock: dls_active += 1
            try:
                dest = os.path.join(output_dir, filename)

                if os.path.exists(dest):
                    with lock: ok_count += 1
                    print(f"  [exists] {filename}")
                    rec.status = "ok"; rec.dl_s = 0.0
                    mark_done(); return

                kc       = kill_counts.get(orig_url, 0)
                kill_evt = asyncio.Event()
                ok, msg, bytes_done = await download_file(
                    proxy_url, cookies, dest, rec, bytes_acc, kill_evt, kc,
                    on_event=lambda msg, tag: print(f"  [{tag}] {msg}", flush=True),
                    fatal_control=fatal_control)
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
                    if not fatal_control.is_set():
                        await q.put((orig_url, 1, rec))
                elif msg == "aborted_disk_full":
                    rec.status = "aborted"
                else:
                    with lock: fail_count += 1
                    failed_urls.append(orig_url)
                    rec.status = "fail"; rec.error = msg
                    if msg != "disk_full":
                        print(f"  [fail] {filename}: {msg}")
                    mark_done()
            finally:
                with lock: dls_active -= 1

    async def browser_worker(get_browser, wid):
        nonlocal ok_count, fail_count
        while not fatal_control.is_set():
            if all_done.is_set() and q.empty(): break
            try:
                url, attempt, rec = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError: continue
            if fatal_control.is_set():
                rec.status = "aborted"
                q.task_done()
                break

            rec.worker_id = wid
            t_start = time.monotonic()
            rec.queue_wait_s = t_start - rec.queued_at
            filename = rec.filename
            is_re    = rec.stall_kills > 0
            suffix   = " [re-extract]" if is_re else (f" [retry {attempt}]" if attempt > 1 else "")
            print(f"  -> {filename[:60]}{suffix}")

            success = False
            unsupported = False
            try:
                parsed = urlparse(url)
                if FUCKINGFAST_HOST in parsed.netloc:
                    # get_browser is passed, not called: fuckingfast only
                    # reaches for a window if /go refuses it.
                    link = await extract_fuckingfast(url, get_browser)
                    rec.extract_s = time.monotonic() - t_start
                    if fatal_control.is_set():
                        rec.status = "aborted"
                        q.task_done()
                        break
                    if not link:
                        print("  [fail] No link found")
                    else:
                        rec.dl_start = time.monotonic()
                        async def _task(pu=link, fn=filename, ou=url, r=rec):
                            await do_dl(pu, "", fn, ou, r)
                        t = asyncio.create_task(_task())
                        async with tasks_lock: all_tasks.append(t)
                        success = True
                elif DATANODES_HOST in parsed.netloc:
                    # API key set -> single JSON GET, no browser, no captcha.
                    # get_browser() is where Chrome is actually launched, so a
                    # batch with no datanodes link never opens one. After that
                    # extract_datanodes() re-validates the shared Chrome
                    # (respawning it if it died) and checks out one window from
                    # the persistent lane pool internally.
                    proxy_url, cookies = await extract_datanodes(await get_browser(), url)
                    rec.extract_s = time.monotonic() - t_start
                    if fatal_control.is_set():
                        rec.status = "aborted"
                        q.task_done()
                        break
                    if not proxy_url:
                        print("  [fail] No URL extracted")
                    else:
                        rec.dl_start = time.monotonic()
                        async def _task(pu=proxy_url, co=cookies, fn=filename, ou=url, r=rec):
                            await do_dl(pu, co, fn, ou, r)
                        t = asyncio.create_task(_task())
                        async with tasks_lock: all_tasks.append(t)
                        success = True
                else:
                    host = parsed.hostname or parsed.netloc or "(missing host)"
                    rec.notes.append(f"unsupported host: {host}")
                    print(
                        f"  [fail] unsupported host: {host} — supported: "
                        f"{', '.join(SUPPORTED_HOSTS)}"
                    )
                    unsupported = True
            except Exception as e:
                print(f"  [error] {e}")

            if fatal_control.is_set():
                rec.status = "aborted"
                q.task_done()
                break

            if (not success and not unsupported and not is_re and attempt < max_retries
                    and not fatal_control.is_set()):
                backoff = min(2**(attempt-1), 6)
                print(f"  [retry in {backoff}s]")
                await asyncio.sleep(backoff)
                if fatal_control.is_set():
                    rec.status = "aborted"
                    q.task_done()
                    break
                rec.queued_at = time.monotonic()
                await q.put((url, attempt+1, rec))
                q.task_done(); continue

            if not success and not is_re and not fatal_control.is_set():
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
        worker_results = await asyncio.gather(
            *[asyncio.create_task(browser_worker(gate.get, i)) for i in range(n_workers)],
            return_exceptions=True,
        )
        for wid, result in enumerate(worker_results):
            if isinstance(result, BaseException):
                print(f"  [worker {wid} error] {result}")
    finally:
        await gate.aclose()

    async with tasks_lock:
        recorded_tasks = list(all_tasks)
    stragglers = [t for t in recorded_tasks if not t.done()]
    if stragglers:
        print(f"  [wait] {len(stragglers)} downloads still finishing...")
    if recorded_tasks:
        await asyncio.gather(*recorded_tasks, return_exceptions=True)

    stop_progress.set()
    await progress_t
    await _close_sess()
    await close_ff_session()
    await _PROXY_POOL.close_all()
    telem.finish()

    base = os.path.dirname(os.path.abspath(__file__))
    try:
        lp, jp = telem.save(base)
        print(f"Log: {os.path.basename(lp)}")
    except (OSError, TypeError) as e:
        # Report persistence is independent of the fatal download state.
        print(f"[warn] Log save error: {e}")

    el = time.monotonic() - t0
    total_bytes = sum(b for _, b in bytes_acc)
    disk_full = fatal_control.disk_full
    print(f"\n{'='*60}")
    if disk_full is not None:
        print(f"Run stopped: disk full in {disk_full.folder}  |  "
              f"ok={ok_count}  fail={fail_count}")
    else:
        print(f"Done in {int(el//60)}m {int(el%60)}s  |  "
              f"ok={ok_count}  fail={fail_count}  |  "
              f"{total_bytes/1e9:.2f} GB  @  {total_bytes/el/1e6:.1f} MB/s")

    if failed_urls:
        fp = os.path.join(base, "failed_links.txt")
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write("\n".join(failed_urls) + "\n")
            print(f"Failed ({len(failed_urls)}): {fp}")
        except OSError as e:
            print(f"[warn] Failed links save error: {e}")

    return ok_count, fail_count, disk_full is not None

# ── ENTRY POINT ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="MoonDownloader CLI — headless downloader for server deployment")
    ap.add_argument("--urls",     required=True,  help="Text file with one URL per line")
    ap.add_argument("--output",   required=True,  help="Output folder for downloaded files")
    ap.add_argument("--extractors", type=int, default=None,
                    help="Parallel extraction workers (default: 8)")
    ap.add_argument("--browsers", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--streams",  type=int, default=24, help="Concurrent download streams (default: 24)")
    ap.add_argument("--retries",  type=int, default=3,  help="Max retries per link (default: 3)")
    ap.add_argument("--proxies",  default=None, help="Proxy list file (default: proxies.txt)")
    ap.add_argument("--version",  action="version", version=VERSION)
    args = ap.parse_args()

    if args.extractors is not None:
        n_extractors = args.extractors
        if args.browsers is not None:
            print("WARNING: --browsers is deprecated and ignored because --extractors was also provided")
    elif args.browsers is not None:
        n_extractors = args.browsers
        print("WARNING: --browsers is deprecated; use --extractors instead")
    else:
        n_extractors = 8

    if not os.path.exists(args.urls):
        print(f"ERROR: urls file not found: {args.urls}"); sys.exit(2)

    with open(args.urls, encoding="utf-8", errors="replace") as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    if not urls:
        print("ERROR: no URLs found in file"); sys.exit(2)

    print(f"Loaded {len(urls)} URLs from {args.urls}")

    is_default_proxies = args.proxies is None
    proxy_path = args.proxies if args.proxies is not None else "proxies.txt"

    try:
        ok, fail, aborted = asyncio.run(run(urls, args.output, n_extractors, args.streams,
                        args.retries, proxy_path, is_default_proxies))
        if aborted:
            sys.exit(1)
        elif ok == 0 and fail > 0:
            sys.exit(3)
        elif fail > 0:
            sys.exit(1)
        else:
            sys.exit(0)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception:
        # Catch unexpected top-level CLI exceptions to log crash traceback and exit cleanly
        crash = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_log.txt")
        with open(crash, "w", encoding="utf-8") as f: f.write(traceback.format_exc())
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
