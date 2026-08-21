"""
MoonDownloader V2 -- headless engine
════════════════════════════════════════
The download engine with no GUI attached. State leaves through
Engine.snapshot(), commands come in through Engine.start()/stop(), and both are
plain JSON-able dicts -- moon_bridge.py hands them straight to the WebView.

Thread model
────────────
    * the caller's thread (the WebView bridge) only ever touches start/stop/
      snapshot/scan_tmp
    * one worker thread runs asyncio.run(self._run(...))
    * every shared counter sits behind self._lock; the log ring behind
      self._log_lock

*snapshot() is called ~12x/second, so it copies counters under the lock and does
its arithmetic outside it -- holding the lock through the ETA maths would stall
every download worker that wants to bump a byte count.*
"""

import os, sys, asyncio, threading
import time, traceback, json, collections
from urllib.parse import urlparse, unquote

from moon_download import (
    DEFAULT_DL_FOLDER,
    FileRecord,
    LAUNCH_ARGS,
    READ_BUFSZ,
    RECV_CHUNK,
    STALL_GRACE_S,
    STALL_MAX_KILL,
    STALL_MIN_BYTES_IN_WIN,
    STALL_MIN_MBS,
    STALL_SAFE_PCT,
    Telemetry,
    VERSION,
    WRITE_BUF,
    _PROXY_POOL,
    _close_sess,
    _sanitize_filename,
    download_file,
    count_usable_proxies,
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
import moon_extract as _moon_extract            # noqa: E402

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

class Engine:

    def __init__(self):
        # Settings arrive from the GUI on start(); these are the fallbacks used
        # for the first paint and for a start() that omits a field.
        self._cfg = {
            "out_folder": DEFAULT_DL_FOLDER,
            "mode":       "download",
            "workers":    16,
            "dl_streams": 48,
            "retries":    3,
            # Defaults asked for by the operator, not by the library: 8 lanes
            # and the shortest manual-captcha wait the extractor accepts.
            "dn_pages":   8,
            "dn_captcha": 30,
            "dn_chrome":  _moon_extract.CHROME_PATH or (_moon_extract.find_chrome() or ""),
            "dn_apikey":  DN_API_KEY,
        }

        self._lock       = threading.Lock()
        self._running    = False
        self._stop_flag  = False
        self._state      = "idle"
        self._url_total  = 0; self._url_done = 0
        self._dl_total   = 0; self._dl_done  = 0
        self._ok         = 0; self._fail     = 0
        self._kills      = 0; self._browsers = 0
        self._dls        = 0
        self._bytes_acc  : collections.deque = collections.deque(maxlen=200000)
        self._t0         = 0.0
        self._t_end      = 0.0
        self._proxies    = 0

        # Live FileRecord registry: the GUI reads these objects every snapshot,
        # so a row's speed and percentage come off the download loop itself
        # instead of a second copy that can go stale.
        self._tracked : dict[str, FileRecord] = {}

        # Bounded log ring + a monotonic cursor. The GUI asks for "everything
        # after N"; if it fell behind further than the ring, it gets the oldest
        # line still held instead of a gap it cannot detect.
        self._log_ring  : collections.deque = collections.deque(maxlen=6000)
        self._log_total = 0
        self._log_lock  = threading.Lock()

        self._alive  = True
        self._thread = None
        self._loop   = None
        self._gate   = None
        self._active_kill_events = set()

        self.proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.txt")
        self._proxy_mtime = 0.0
        self._proxy_status = "none_configured"
        self._last_proxy_check = 0.0

    def _inc(self, attr, delta=1):
        with self._lock: setattr(self, attr, getattr(self, attr) + delta)

    def _get(self, attr):
        with self._lock: return getattr(self, attr)

    def log(self, msg, tag=""):
        """Thread-safe: called from the asyncio worker, drained by snapshot()."""
        with self._log_lock:
            self._log_ring.append((str(msg), tag))
            self._log_total += 1

    async def _do_dl(self, proxy_url, cookies, filename, orig_url, rec,
                     kill_counts, dl_sem, dest_folder, telem, mark_done_fn,
                     failed_urls, q, fatal_control):
        async with dl_sem:
            if fatal_control.is_set():
                rec.status = "aborted"
                return
            self._inc("_dls")
            try:
                rec.dl_start = time.monotonic(); rec.status = "downloading"
                self._track(rec)
                dest = os.path.join(dest_folder, filename)

                if os.path.exists(dest):
                    self._inc("_ok")
                    self.log(f"    ✓  Exists: {filename}", "ok")
                    rec.status="ok"; rec.dl_s=0.0
                    mark_done_fn(); self._inc("_dl_done"); return

                kc       = kill_counts.get(orig_url, 0)
                kill_evt = asyncio.Event()
                with self._lock:
                    self._active_kill_events.add(kill_evt)
                try:
                    ok, msg, bytes_done = await download_file(
                        proxy_url, cookies, dest, rec, self._bytes_acc, kill_evt, kc,
                        telem=telem, on_event=self.log, fatal_control=fatal_control)
                finally:
                    with self._lock:
                        self._active_kill_events.discard(kill_evt)
                rec.dl_s = max(time.monotonic()-rec.dl_start, 0.001)

                if ok:
                    self._inc("_ok")
                    spd = f"  ({rec.avg_mbs:.1f} MB/s)" if rec.avg_mbs > 0 else ""
                    self.log(f"    ✓  Saved: {filename}{spd}", "ok")
                    rec.status="ok"; mark_done_fn(); self._inc("_dl_done")
                elif msg == "stall_killed" and self._get("_stop_flag"):
                    rec.status = "stopped"
                    self.log(f"    stop: {filename} interrupted; partial file kept", "warn")
                    mark_done_fn(); self._inc("_dl_done")
                elif msg == "aborted_disk_full":
                    rec.status = "aborted"
                elif msg == "stall_killed":
                    done_mb = bytes_done//(1<<20)
                    new_kc = kc + 1; kill_counts[orig_url] = new_kc
                    self._inc("_kills"); rec.stall_kills += 1
                    if new_kc <= STALL_MAX_KILL:
                        self.log(f"    ⚡  Kill #{new_kc}: {filename}  ({done_mb}MB) → re-extract", "kill")
                    else:
                        self.log(f"    ⚡  Kill #{new_kc}: {filename}  ({done_mb}MB) → continue", "warn")
                    rec.queued_at=time.monotonic(); rec.status="pending"
                    if not fatal_control.is_set():
                        await q.put((orig_url, 1, rec))
                else:
                    self._inc("_fail"); failed_urls.append(orig_url)
                    rec.status="fail"; rec.error=msg
                    if msg != "disk_full":
                        self.log(f"    ✗  {filename}: {msg}", "fail")
                    mark_done_fn(); self._inc("_dl_done")
            finally:
                self._inc("_dls",-1)

    async def _browser_worker(self, get_browser, wid, q, dl_sem, all_done, mark_done_fn,
                               kill_counts, all_tasks, tasks_lock,
                               output_links, failed_urls, dest_folder, mode, max_retries, telem,
                               fatal_control):
        my_tasks = []
        try:
            while not self._get("_stop_flag") and not fatal_control.is_set():
                if all_done.is_set() and q.empty(): break
                try:
                    url, attempt, rec = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError: continue
                if fatal_control.is_set():
                    rec.status = "aborted"
                    q.task_done()
                    break

                rec.worker_id    = wid
                t_start          = time.monotonic()
                rec.queue_wait_s = t_start - rec.queued_at
                rec.status       = "extracting"
                filename = rec.filename
                short    = filename[:44]+("…" if len(filename)>44 else "")
                is_re    = rec.stall_kills > 0
                is_retry = attempt > 1
                suffix   = (" [re-extract]" if is_re else "")+(" [retry]" if is_retry else "")
                self.log(f"  → {short}{suffix}", "retry" if (is_re or is_retry) else "dim")
                self._track(rec)

                success = False
                unsupported = False
                try:
                    parsed = urlparse(url)
                    if FUCKINGFAST_HOST in parsed.netloc:
                        # get_browser is passed, not called: fuckingfast only
                        # reaches for a window if /go refuses it.
                        link = await extract_fuckingfast(url, get_browser)
                        rec.extract_s = time.monotonic()-t_start
                        if fatal_control.is_set():
                            rec.status = "aborted"
                            q.task_done()
                            break
                        if not link:
                            self.log("    ✗  No link found", "fail")
                        elif mode == "links":
                            output_links.append(link); self._inc("_ok")
                            self.log(f"    ✓  {link[:70]}", "ok")
                            rec.status="ok"; success=True; mark_done_fn()
                        else:
                            self.log(f"    ↓  {filename}", "dim")
                            async def _task(pu=link, fn=filename, ou=url, r=rec):
                                await self._do_dl(pu, "", fn, ou, r, kill_counts,
                                                   dl_sem, dest_folder, telem, mark_done_fn,
                                                   failed_urls, q, fatal_control)
                            t = asyncio.create_task(_task())
                            my_tasks.append(t)
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
                        rec.extract_s = time.monotonic()-t_start
                        if fatal_control.is_set():
                            rec.status = "aborted"
                            q.task_done()
                            break
                        if not proxy_url:
                            rec.notes.append("extraction failed")
                            self.log("    ✗  No URL extracted", "fail")
                        elif mode == "links":
                            output_links.append(proxy_url); self._inc("_ok")
                            self.log(f"    ✓  {proxy_url[:70]}", "ok")
                            rec.status="ok"; success=True; mark_done_fn()
                        else:
                            self.log(f"    ↓  {filename}", "dim")
                            async def _task(pu=proxy_url, co=cookies, fn=filename, ou=url, r=rec):
                                await self._do_dl(pu, co, fn, ou, r, kill_counts,
                                                   dl_sem, dest_folder, telem, mark_done_fn,
                                                   failed_urls, q, fatal_control)
                            t = asyncio.create_task(_task())
                            my_tasks.append(t)
                            async with tasks_lock: all_tasks.append(t)
                            success = True
                    else:
                        host = parsed.hostname or parsed.netloc or "(missing host)"
                        rec.notes.append(f"unsupported host: {host}")
                        self.log(
                            f"    ✗  unsupported host: {host} — supported: "
                            f"{', '.join(SUPPORTED_HOSTS)}",
                            "fail",
                        )
                        unsupported = True
                except Exception as e:
                    # One URL's extraction/scheduling failed for any reason (network,
                    # parsing, Chrome/CDP); the worker loop must keep serving the queue.
                    rec.notes.append(f"exception: {e}")
                    self.log(f"    ✗  {e}", "fail")

                if fatal_control.is_set():
                    rec.status = "aborted"
                    q.task_done()
                    break

                if (not success and not unsupported and not is_re and attempt < max_retries
                        and not self._get("_stop_flag") and not fatal_control.is_set()):
                    backoff = min(2**(attempt-1), 6)
                    self.log(f"    ↻  retry in {backoff}s", "warn")
                    await asyncio.sleep(backoff)
                    if fatal_control.is_set():
                        rec.status = "aborted"
                        q.task_done()
                        break
                    rec.queued_at = time.monotonic()
                    await q.put((url, attempt+1, rec))
                    q.task_done(); continue

                if not success and not is_re and not fatal_control.is_set():
                    self._inc("_fail"); failed_urls.append(url)
                    rec.status="fail"; mark_done_fn()

                self._inc("_url_done"); q.task_done()

            if my_tasks:
                await asyncio.gather(*my_tasks, return_exceptions=True)
        finally:
            pass

    async def _run(self, urls, n_workers, max_dl, max_retries):
        t0           = time.monotonic()
        q            = asyncio.Queue()
        dl_sem       = asyncio.Semaphore(max_dl)
        output_links : list[str] = []
        failed_urls  : list[str] = []
        all_tasks    : list      = []
        tasks_lock   = asyncio.Lock()
        kill_counts  : dict[str,int] = {}
        fatal_control = RunFatalControl()
        dest_folder  = self._cfg["out_folder"]
        mode         = self._cfg["mode"]
        n_done       = 0
        all_done     = asyncio.Event()

        def mark_done():
            nonlocal n_done
            n_done += 1
            if n_done >= len(urls): all_done.set()

        cfg = {"browsers": n_workers, "dl_streams": max_dl, "retries": max_retries,
               "stall_min_mbs": STALL_MIN_MBS, "stall_grace_s": STALL_GRACE_S,
               "stall_max_kill": STALL_MAX_KILL, "stall_safe_pct": STALL_SAFE_PCT,
               "stall_win_guard_MB": STALL_MIN_BYTES_IN_WIN//(1<<20),
               "recv_chunk_MB": RECV_CHUNK//(1<<20), "write_buf_MB": WRITE_BUF//(1<<20),
               "socket_buf_KB": READ_BUFSZ//1024, "mode": mode, "total_links": len(urls)}
        telem = Telemetry(cfg)

        for url in urls:
            p = urlparse(url)
            raw_name = unquote(p.fragment or p.path.split("/")[-1]) or url
            filename = _sanitize_filename(raw_name)
            rec = telem.reg(url, filename)
            if rec.filename != filename:
                self.log(
                    f"  filename collision: {filename} -> {rec.filename}", "warn"
                )
            await q.put((url, 1, rec))

        snap_stop = asyncio.Event()
        async def snap_task():
            while not snap_stop.is_set():
                with self._lock:
                    b, d, ok, fail = self._browsers, self._dls, self._ok, self._fail
                telem.snap(b, d, q.qsize(), ok, fail)
                try:
                    await asyncio.wait_for(snap_stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

        # Kept in a local: a task with no reference can be collected mid-run.
        snap_t = asyncio.create_task(snap_task())  # noqa: F841

        # fuckingfast is pure HTTP: no browser, no profile, not even the Playwright
        # driver. Chrome opens on the first datanodes link and not before - a
        # fuckingfast-only batch never launches one.
        browser_started = False

        def _chrome_starting():
            nonlocal browser_started
            browser_started = True
            self._inc("_browsers")
            self.log("   datanodes: starting Chrome...", "dim")

        gate = BrowserGate(LAUNCH_ARGS, on_open=_chrome_starting)
        with self._lock:
            self._loop, self._gate = asyncio.get_running_loop(), gate

        async def _launch(wid):
            await self._browser_worker(
                gate.get, wid, q, dl_sem, all_done, mark_done,
                kill_counts, all_tasks, tasks_lock,
                output_links, failed_urls, dest_folder, mode, max_retries, telem,
                fatal_control)

        try:
            worker_results = await asyncio.gather(
                *[asyncio.create_task(_launch(i)) for i in range(n_workers)],
                return_exceptions=True,
            )
            for wid, result in enumerate(worker_results):
                if isinstance(result, BaseException):
                    self.log(f"  ✗  worker {wid} failed: {result}", "fail")
        finally:
            if browser_started:
                self._inc("_browsers", -1)
            await gate.aclose()

        async with tasks_lock:
            stragglers = [t for t in all_tasks if not t.done()]
        if stragglers:
            self.log(f"  ⚠  {len(stragglers)} straggler tasks finishing...", "warn")
            await asyncio.gather(*stragglers, return_exceptions=True)

        snap_stop.set()
        await snap_t
        await _close_sess()
        await close_ff_session()
        await _PROXY_POOL.close_all()
        telem.finish()

        base = os.path.dirname(os.path.abspath(__file__))
        try:
            lp, jp = telem.save(base)
            self.log(f"📊  {os.path.basename(lp)}", "info")
            self.log(f"📊  {os.path.basename(jp)}", "info")
        except (OSError, TypeError) as e:
            # OSError: can't open/write the report files (permissions, disk full, bad
            # path). TypeError: json.dump choking on a non-serializable field. The run's
            # downloads already completed; a report-write failure shouldn't crash finalize.
            self.log(f"⚠  Log save error: {e}", "warn")

        if output_links and mode == "links" and not fatal_control.is_set():
            with open(os.path.join(base,"output_links.txt"),"w",encoding="utf-8") as f:
                f.write("\n".join(output_links)+"\n")
            self.log("✓  Links → output_links.txt", "info")
        if failed_urls:
            try:
                with open(os.path.join(base,"failed_links.txt"),"w",encoding="utf-8") as f:
                    f.write("\n".join(failed_urls)+"\n")
                self.log(f"⚠  {len(failed_urls)} failed → failed_links.txt", "warn")
            except OSError as e:
                self.log(f"⚠  Failed links save error: {e}", "warn")

        el = time.monotonic()-t0; m, s = divmod(int(el), 60)
        with self._lock: ok, fail, kills = self._ok, self._fail, self._kills
        disk_full = fatal_control.disk_full
        if disk_full is not None:
            self.log(
                f"\nRun stopped: disk full in {disk_full.folder} after {m}m {s}s  ·  "
                f"✓ {ok}  ✗ {fail}",
                "fail",
            )
        else:
            self.log(f"\n✓  Done in {m}m {s}s  ·  ✓ {ok}  ✗ {fail}  ⚡ {kills} kills", "ok")
        self._on_done()

    def _on_done(self):
        with self._lock:
            self._running = False
            self._stop_flag = False
            self._state = "done"
            self._t_end = time.monotonic()

    def scan_tmp(self) -> int:
        folder = self._cfg["out_folder"]
        if not os.path.isdir(folder):
            return 0
        try:
            return len([f for f in os.listdir(folder) if f.endswith(".tmp")])
        except OSError:
            return 0

    # ══ GUI-facing API ═════════════════════════════════════════════════════
    _CLAMP = {
        "workers":    (2, 32),
        "dl_streams": (2, 48),
        "retries":    (0, 5),
        "dn_pages":   (1, 8),
        "dn_captcha": (30, 600),
    }
    _FILE_UI_STATE = {
        "pending":     "queue",
        "extracting":  "extract",
        "downloading": "download",
        "aborted":     "queue",
        "ok":          "ok",
        "fail":        "fail",
        # A user-initiated stop is not a failure: the partial .tmp is kept and the
        # file is resumable, so it reads as queued rather than red. Listed here
        # explicitly — it already fell through the .get() default to the same
        # value, but a status the engine can set belongs in the table.
        "stopped":     "queue",
    }
    # The GUI shows active transfers first and keeps a tail of finished ones.
    # 40 was too short to be honest on a 124-file batch: the badge read "40"
    # while 124 had gone through. 120 rows cost nothing with content-visibility.
    _ROWS_KEEP = 120

    def _track(self, rec):
        """Register a FileRecord so snapshot() can read its live fields."""
        with self._lock:
            self._tracked[rec.url] = rec

    def apply_cfg(self, cfg: dict) -> dict:
        """Validate and store settings. Returns the effective values.

        The GUI is a web page: everything it sends is untrusted input, so ints
        are coerced and clamped here rather than being fed to the semaphores raw.
        """
        cfg = cfg or {}
        for key, (lo, hi) in self._CLAMP.items():
            if key in cfg:
                try:
                    self._cfg[key] = max(lo, min(hi, int(cfg[key])))
                except (TypeError, ValueError):
                    pass
        if cfg.get("mode") in ("download", "links"):
            self._cfg["mode"] = cfg["mode"]
        for key in ("out_folder", "dn_chrome", "dn_apikey"):
            if isinstance(cfg.get(key), str):
                self._cfg[key] = cfg[key].strip() if key != "dn_apikey" else cfg[key]
        return dict(self._cfg)

    def start(self, cfg: dict) -> dict:
        if self._get("_running"):
            return {"error": "already running"}

        urls = [u.strip() for u in (cfg or {}).get("links", []) if str(u).strip()]
        if not urls:
            return {"error": "no links pasted"}
        eff = self.apply_cfg(cfg)

        try:
            os.makedirs(eff["out_folder"], exist_ok=True)
        except OSError as e:
            return {"error": f"cannot create folder: {e}"}

        with self._lock:
            self._running = True; self._stop_flag = False
            self._state = "running"
            self._url_total = len(urls); self._url_done = 0
            self._dl_total = len(urls);  self._dl_done  = 0
            self._ok = 0; self._fail = 0; self._kills = 0
            self._browsers = 0; self._dls = 0
            self._bytes_acc.clear(); self._t0 = time.monotonic()
            self._t_end = 0.0
            self._tracked.clear()
            self._active_kill_events.clear()
        with self._log_lock:
            self._log_ring.clear(); self._log_total = 0

        # What is on screen is what runs: push the per-host settings into the
        # extraction layer before any worker thread starts.
        applied = _moon_extract.configure(
            lanes=eff["dn_pages"],
            chrome_path=eff["dn_chrome"],
            api_key=eff["dn_apikey"],
            captcha_wait=eff["dn_captcha"])

        self._proxies, skipped = _PROXY_POOL.load(self.proxy_path, is_default=True)

        n, d, r = eff["workers"], eff["dl_streams"], eff["retries"]
        self.log(f"▶  {len(urls)} links  ·  {n} extractors  ·  {d} streams  ·  {r} retries  ·  {VERSION}", "info")
        self.log(f"   fuckingfast: direct HTTP"
                 f"{'' if applied['curl_cffi'] else '  ✗ curl_cffi MISSING'}"
                 f"   ·   datanodes: {applied['lanes']} pages, captcha {applied['captcha_wait']}s"
                 f"{', API key' if applied['api_key'] else ''}", "dim")
        self.log(f"   chrome: {applied['chrome']}", "dim")
        if self._proxies or skipped:
            self.log(f"   proxies: {self._proxies} loaded, {skipped} skipped — rotating per download", "info")
        self.log(f"   stall < {STALL_MIN_MBS} MB/s  ·  grace {STALL_GRACE_S}s  ·  max {STALL_MAX_KILL} kill", "dim")

        self._thread = threading.Thread(
            target=lambda: self._guarded_run(urls, n, d, r), daemon=True)
        self._thread.start()
        return {"ok": True, "proxies": self._proxies, "effective": applied}

    def _guarded_run(self, urls, n, d, r):
        """asyncio.run in a thread: an escaping exception would vanish silently."""
        try:
            asyncio.run(self._run(urls, n, d, r))
        except Exception:
            # Top-level guard for a background thread: anything that escapes _run()
            # would otherwise vanish silently instead of surfacing to the GUI/CLI.
            self.log(f"✗  engine crash: {traceback.format_exc(limit=3)}", "fail")
            self._on_done()
        finally:
            with self._lock:
                self._loop = None
                self._gate = None

    def stop(self, timeout: float = 1.5) -> dict:
        running = self._get("_running")
        kill_events = []
        if running:
            with self._lock:
                self._stop_flag = True
                self._state = "stopping"
                kill_events = list(self._active_kill_events)
            self.log("⏹  stop requested — stopping downloads in flight...", "warn")

        with self._lock:
            loop, gate, thread = self._loop, self._gate, self._thread
        deadline = time.monotonic() + max(0.0, timeout)

        if running and loop is not None and loop.is_running():
            for kill_evt in kill_events:
                loop.call_soon_threadsafe(kill_evt.set)

        if running and loop is not None and gate is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(gate.aclose(), loop)
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass

        if (running and thread is not None and thread is not threading.current_thread()
                and thread.is_alive()):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return {"ok": True}

    def _files_payload(self) -> list[dict]:
        with self._lock:
            tracked = list(self._tracked.items())

        # Retire the oldest finished entries once the list is long. Active
        # transfers are never dropped, so a 400-file batch stays bounded.
        excess = len(tracked) - self._ROWS_KEEP
        if excess > 0:
            with self._lock:
                for url, rec in tracked:
                    if excess <= 0:
                        break
                    if rec.status in ("ok", "fail"):
                        self._tracked.pop(url, None)
                        excess -= 1
                tracked = list(self._tracked.items())

        out = []
        for url, rec in tracked:
            state = self._FILE_UI_STATE.get(rec.status, "queue")
            if rec.stall_kills and rec.status in ("pending", "extracting"):
                state = "kill"
            if rec.status == "downloading" and rec.file_bytes > 0:
                pct = min(1.0, rec.done_bytes / rec.file_bytes)
            elif rec.status == "ok":
                pct = 1.0
            else:
                pct = None
            if rec.status == "downloading":
                mbs = rec.live_mbs
            elif rec.status == "ok":
                mbs = rec.avg_mbs
            else:
                mbs = 0.0
            out.append({"key": url, "name": rec.filename, "state": state,
                        "mbs": round(mbs, 3), "pct": pct})
        return out

    def snapshot(self, cursor: int = 0) -> dict:
        with self._lock:
            state    = self._state
            running  = self._running
            t0       = self._t0
            t_end    = self._t_end
            url_done = self._url_done; url_tot = self._url_total
            dl_done  = self._dl_done;  dl_tot  = self._dl_total
            ok       = self._ok; fail = self._fail
            kills    = self._kills; dls = self._dls
            snap     = list(self._bytes_acc)
            tracked_records = list(self._tracked.values())

        if not running:
            self._get_proxy_status()
        else:
            proxy_count = self._proxies
            if proxy_count > 0:
                self._proxy_status = "loaded"
            elif not os.path.exists(self.proxy_path):
                self._proxy_status = "none_configured"
            else:
                self._proxy_status = "empty_file"
        now = time.monotonic()
        # 1. Live Speed (3-second window for UI display)
        recent_live = [(t, b) for t, b in snap if t > now - 3.0]
        if len(recent_live) > 1:
            span_live = max(min(3.0, now - self._t0), 0.05)
            mbs = sum(b for _, b in recent_live) / span_live / 1_048_576
        else:
            mbs = 0.0
        # 2. Calculate smoothed eta rate
        recent_eta = [(t, b) for t, b in snap if t > now - 10.0]
        if len(recent_eta) > 1:
            span_eta = max(min(10.0, now - self._t0), 0.05)
            mbs_eta = sum(b for _, b in recent_eta) / span_eta / 1_048_576
        else:
            mbs_eta = 0.0
        total_downloaded = sum(b for _, b in snap)
        files_remaining  = dl_tot - dl_done
        if mbs_eta > 0.1 and files_remaining > 0:
            # calculate average file size
            avg_file = 0
            known_counter = 0
            for record in tracked_records:
                if record.file_bytes > 0: # if we know the amount of total bytes the download will take
                    avg_file += record.file_bytes
                    known_counter += 1
            try:
                avg_file = avg_file / known_counter
            except ZeroDivisionError:
                avg_file = 0

            dl_size_left = 0
            tracked_active_count = 0
            for record in tracked_records:
                if record.status in ("ok", "fail", "aborted", "stopped"):
                    continue

                tracked_active_count += 1
                if record.file_bytes > 0:
                    dl_size_left += (record.file_bytes - record.done_bytes)
                else:
                    dl_size_left += avg_file

            # Add remaining queued files that haven't been picked up by workers yet
            queued_files = files_remaining - tracked_active_count
            if queued_files > 0:
                dl_size_left += queued_files * avg_file

            # to make it to mb
            dl_size_left = dl_size_left / 1_048_576
            raw_eta = dl_size_left / mbs_eta
            if raw_eta >= 7200:
                eta = None
            else:
                eta = raw_eta
        else:
            eta = None
        el = (t_end - t0) if t_end else ((now - t0) if t0 else 0.0)
        # No phase sentence here on purpose: the GUI owns wording and language,
        # so the engine ships numbers and a stage name instead of prose.
        if not running and state == "idle":
            stage = "idle"
        elif not running and state == "done":
            stage = "done"
        elif url_done < url_tot:
            stage = "extracting"
        elif dl_done < dl_tot:
            stage = "downloading"
        else:
            stage = "done"

        with self._log_lock:
            dropped = self._log_total - len(self._log_ring)
            begin = max(0, min(len(self._log_ring), cursor - dropped))
            lines = [list(pair) for pair in list(self._log_ring)[begin:]]
            new_cursor = self._log_total

        return {
            "state": state,
            "metrics": {
                "speed_mbs": round(mbs, 3),
                "dl_done": dl_done, "dl_total": dl_tot,
                "ok": ok, "fail": fail, "kills": kills,
                "eta_s": round(eta, 1) if eta is not None else None,
                "bytes_total": total_downloaded,
                "extract_done": url_done, "extract_total": url_tot,
                "active": dls, "stage": stage, "elapsed_s": round(el, 1),
            },
            "files": self._files_payload(),
            "log": lines,
            "cursor": new_cursor,
            "proxies": self._proxies,
            "proxy_info": {
                "status": self._proxy_status,
                "count": self._proxies
            },
            "tmp": self.scan_tmp() if not running else None,
        }

    def clear_files(self) -> dict:
        with self._lock:
            for url in [u for u, r in self._tracked.items() if r.status in ("ok", "fail")]:
                self._tracked.pop(url, None)
        return {"ok": True}

    def _get_proxy_status(self):
        # Only do disk i/o every 2 seconds
        now = time.monotonic()
        if now - self._last_proxy_check < 2.0:
            return
        self._last_proxy_check = now

        # 1. Handle missing file
        if not os.path.exists(self.proxy_path):
            self._proxy_mtime = 0.0
            self._proxies = 0
            self._proxy_status = "none_configured"
            return
        # 2. File exists: was it modified?
        mtime = os.path.getmtime(self.proxy_path)
        if mtime > self._proxy_mtime:
            self._proxy_mtime = mtime
            try:
                usable, skipped = count_usable_proxies(self.proxy_path)
                self._proxies = usable
            except OSError:
                self._proxies = 0
        # 3. Distinguish 0 valid proxies vs N valid proxies
        if self._proxies == 0:
            self._proxy_status = "empty_file"
            
        else:
            self._proxy_status = "loaded"
        return

# ── entry point ─────────────────────────────────────────────────────────────
# There is no GUI in here. Start the app with:  python moon_bridge.py
if __name__ == "__main__":
    engine = Engine()
    print(json.dumps(engine.snapshot(0)["metrics"], indent=2))
    print(f"{VERSION}  ·  headless engine ok  ·  start the GUI with: python moon_bridge.py")
