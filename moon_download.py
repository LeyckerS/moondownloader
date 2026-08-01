"""Shared download engine for the GUI engine and CLI front-end."""

from __future__ import annotations

import asyncio
import collections
import datetime
import io
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable

import aiohttp

from moon_extract import referer_for
import moon_extract as _moon_extract

VERSION = "v2.0"

DEFAULT_DL_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads", "datanodes")

RECV_CHUNK = 4 * 1024 * 1024
WRITE_BUF = 16 * 1024 * 1024
READ_BUFSZ = 1 << 19

# Stall detection -- near-disabled. datanodes.to CDN assigns lanes per session:
# re-extracting returns the same slow lane. A slow file WILL finish. Only kill
# files genuinely stuck at < 0.5 MB/s, once, then let them complete regardless.
STALL_MIN_MBS = 0.5
STALL_GRACE_S = 90
STALL_CHECK_S = 20
STALL_WIN_S = 60
STALL_MAX_KILL = 1
STALL_SAFE_PCT = 0.80
STALL_MIN_BYTES_IN_WIN = 30 * 1024 * 1024
STALL_MIN_FILE_BYTES = 50 * 1024 * 1024

# Inner connection retries -- separate from the user-facing extraction retry count.
DL_INNER_RETRIES = 4

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
    """Strip characters that are invalid in Windows filenames."""
    name = _WIN_INVALID.sub("_", name).strip(". ")
    return name or "download"


_SESSION: aiohttp.ClientSession | None = None
_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="dl_write")


def _sess() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        conn = aiohttp.TCPConnector(
            limit=0, limit_per_host=0, force_close=False,
            enable_cleanup_closed=True, ttl_dns_cache=600, keepalive_timeout=30,
        )
        _SESSION = aiohttp.ClientSession(
            connector=conn, read_bufsize=READ_BUFSZ,
            timeout=aiohttp.ClientTimeout(total=7200, connect=20, sock_read=90),
        )
    return _SESSION


async def _close_sess():
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
        _SESSION = None


# The degraded no-curl_cffi fuckingfast path, and the lane pool's per-context user
# agent, both reuse this module's own HTTP/UA plumbing.
_moon_extract._sess = _sess
_moon_extract.USER_AGENTS = USER_AGENTS


class ProxyPool:
    def __init__(self):
        self.proxies: list[dict] = []
        self._idx = 0
        self._lock = threading.Lock()
        self._sessions: dict[str, aiohttp.ClientSession] = {}

    def load(self, path: str, is_default: bool = False) -> tuple[int, int]:
        if not os.path.exists(path):
            if not is_default:
                print(f"WARNING: proxy file not found at {path}")
            return 0, 0
        loaded = []
        skipped = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    if line.startswith(("http://", "https://", "socks")):
                        loaded.append({"url": line, "auth": None})
                    else:
                        parts = line.split(":")
                        if len(parts) == 4:
                            if re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
                                ip, port, user, passwd = parts
                            else:
                                user, passwd, ip, port = parts
                            loaded.append({
                                "url": f"http://{ip}:{port}",
                                "auth": aiohttp.BasicAuth(user, passwd),
                            })
                        elif len(parts) == 2:
                            ip, port = parts
                            loaded.append({"url": f"http://{ip}:{port}", "auth": None})
                        else:
                            skipped += 1
                except Exception:
                    # Skip line if proxy parsing or auth formatting fails
                    skipped += 1
                    continue
        self.proxies = loaded
        if not loaded:
            print(f"WARNING: proxy file {path} yielded 0 proxies")
        return len(loaded), skipped

    def next(self) -> dict | None:
        """Round-robin proxy selection."""
        if not self.proxies:
            return None
        with self._lock:
            p = self.proxies[self._idx % len(self.proxies)]
            self._idx += 1
        return p

    def get_session(self, proxy: dict) -> aiohttp.ClientSession:
        """Get or create a dedicated aiohttp session for this proxy."""
        key = proxy["url"]
        if key not in self._sessions or self._sessions[key].closed:
            conn = aiohttp.TCPConnector(
                limit=0, limit_per_host=0, force_close=True,
                enable_cleanup_closed=True, ttl_dns_cache=300,
            )
            self._sessions[key] = aiohttp.ClientSession(
                connector=conn, read_bufsize=READ_BUFSZ,
                timeout=aiohttp.ClientTimeout(total=7200, connect=30, sock_read=120),
            )
        return self._sessions[key]

    async def close_all(self):
        for s in self._sessions.values():
            if not s.closed:
                try:
                    await s.close()
                except Exception:
                    # Best-effort cleanup during shutdown; ignore session closure errors
                    pass
        self._sessions.clear()


_PROXY_POOL = ProxyPool()


@dataclass
class FileRecord:
    url: str
    filename: str
    worker_id: int = -1
    stall_kills: int = 0
    queued_at: float = 0.0
    extract_s: float = 0.0
    dl_start: float = 0.0
    dl_s: float = 0.0
    file_bytes: int = 0
    status: str = "pending"
    error: str = ""
    avg_mbs: float = 0.0
    queue_wait_s: float = 0.0
    done_bytes: int = 0
    live_mbs: float = 0.0
    notes: list = field(default_factory=list)


class Telemetry:
    def __init__(self, cfg: dict, flavor: str = "engine"):
        self.cfg = cfg
        self.flavor = flavor
        self.t0 = time.monotonic()
        self.t_end = 0.0
        self.files: dict[str, FileRecord] = {}
        self.snapshots: list[dict] = []
        self.stall_events: list[dict] = []
        self._lock = threading.Lock()

    def reg(self, url: str, filename: str) -> FileRecord:
        rec = FileRecord(url=url, filename=filename, queued_at=time.monotonic())
        with self._lock:
            self.files[url] = rec
        return rec

    def snap(self, *args):
        if len(args) == 5:
            browsers, dls, qsize, ok, fail = args
            self.snapshots.append({
                "ts": round(time.monotonic() - self.t0, 1),
                "browsers": browsers, "downloads": dls,
                "queue": qsize, "ok": ok, "fail": fail,
            })
        else:
            dls, qsize, ok, fail = args
            self.snapshots.append({
                "ts": round(time.monotonic() - self.t0, 1),
                "downloads": dls, "queue": qsize, "ok": ok, "fail": fail,
            })

    def stall(self, filename, speed, done_bytes, action):
        self.stall_events.append({
            "ts": round(time.monotonic() - self.t0, 1),
            "file": filename,
            "speed_mbs": round(speed, 2),
            "done_mb": round(done_bytes / 1e6, 1),
            "action": action,
        })

    def finish(self):
        self.t_end = time.monotonic()

    def save(self, out_dir: str) -> tuple[str, str]:
        if self.flavor == "cli":
            return self._save_cli(out_dir)
        return self._save_engine(out_dir)

    def _save_cli(self, out_dir: str) -> tuple[str, str]:
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        lp = os.path.join(out_dir, f"moontech_cli_{ts}.log")
        jp = os.path.join(out_dir, f"moontech_cli_{ts}.json")
        el = self.t_end - self.t0
        recs = list(self.files.values())
        ok_r = [r for r in recs if r.status == "ok"]

        buf = io.StringIO()

        def W(*parts):
            buf.write(" ".join(str(p) for p in parts) + "\n")

        W("=" * 72)
        W(f"MOONTECH CLI {VERSION}  --  PERFORMANCE LOG")
        W("=" * 72)
        W(f"Duration : {int(el//60)}m {int(el%60)}s")
        if ok_r:
            tb = sum(r.file_bytes for r in ok_r)
            W(f"Total    : {tb/1e9:.2f} GB  @  {tb/el/1e6:.1f} MB/s")
        W(f"OK: {len(ok_r)}  /  Fail: {len(recs)-len(ok_r)}")
        W()
        W(f"{'#':<4} {'Filename':<48} {'DL':>7} {'Speed':>10} {'Status'}")
        W("-" * 80)
        for i, r in enumerate(recs, 1):
            spd = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "--"
            W(f"{i:<4} {r.filename[:48]:<48} {r.dl_s:>7.1f} {spd:>10} {r.status}")
        W("=" * 72)

        with open(lp, "w", encoding="utf-8") as f:
            f.write(buf.getvalue())
        cli_fields = (
            "url", "filename", "worker_id", "stall_kills", "queued_at", "extract_s",
            "dl_start", "dl_s", "file_bytes", "status", "error", "avg_mbs",
            "queue_wait_s", "notes",
        )
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({
                "version": VERSION,
                "duration_s": round(el, 2),
                "ok": len(ok_r),
                "fail": len(recs) - len(ok_r),
                "files": [{k: getattr(r, k) for k in cli_fields} for r in recs],
            }, f, indent=2)
        return lp, jp

    def _save_engine(self, out_dir: str) -> tuple[str, str]:
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        lp = os.path.join(out_dir, f"moontech_{ts}.log")
        jp = os.path.join(out_dir, f"moontech_{ts}.json")
        el = self.t_end - self.t0
        recs = list(self.files.values())
        ok_r = [r for r in recs if r.status == "ok"]
        dt = sorted(r.dl_s for r in ok_r if r.dl_s > 0)
        med = dt[len(dt)//2] if dt else 0.0
        slow_ids = {id(r) for r in ok_r if r.dl_s > med * 2}

        buf = io.StringIO()

        def W(*parts):
            buf.write(" ".join(str(p) for p in parts) + "\n")

        W("=" * 72)
        W(f"MOONTECH {VERSION}  —  PERFORMANCE LOG")
        W("=" * 72)
        W(f"Session  : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        W(f"Duration : {int(el//60)}m {int(el%60)}s  ({el:.1f}s)")
        W()
        W("── CONFIG ──────────────────────────────────────────────────────────")
        for k, v in self.cfg.items():
            W(f"  {k:<28} {v}")
        W()
        W("── SUMMARY ─────────────────────────────────────────────────────────")
        W(f"  Total links    : {len(recs)}")
        W(f"  Completed OK   : {len(ok_r)}")
        W(f"  Failed         : {len(recs)-len(ok_r)}")
        W(f"  Stall kills    : {sum(r.stall_kills for r in recs)}")
        if ok_r:
            tb = sum(r.file_bytes for r in ok_r)
            W(f"  Total data     : {tb/1e9:.2f} GB")
            W(f"  Session speed  : {tb/el/1e6:.1f} MB/s")
        if dt:
            W(f"  Median DL time : {med:.1f}s")
            W(f"  Slowest file   : {max(dt):.1f}s")
            W(f"  Fastest file   : {min(dt):.1f}s")
        W(f"  Slow (>2x median): {len(slow_ids)}")
        W()
        W("── STALL EVENTS ────────────────────────────────────────────────────")
        if self.stall_events:
            for e in self.stall_events:
                W(f"  t={e['ts']:>6.1f}s  {e['file'][:44]:<44}  "
                  f"{e['speed_mbs']:.2f} MB/s  {e['done_mb']:.0f}MB  → {e['action']}")
        else:
            W("  None.")
        W()
        W("── PER-FILE TIMING ─────────────────────────────────────────────────")
        W(f"  {'#':<4} {'Filename':<48} {'Wkr':>3} {'Kll':>3} "
          f"{'QWait':>6} {'Extr':>6} {'DL':>7} {'Speed':>10} {'Status'}")
        W("  " + "-"*4 + " " + "-"*48 + " " + "-"*3 + " " + "-"*3 + " "
          + "-"*6 + " " + "-"*6 + " " + "-"*7 + " " + "-"*10 + " " + "-"*8)
        for i, r in enumerate(recs, 1):
            spd = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "—"
            flag = " ⚠SLOW" if id(r) in slow_ids else ""
            W(f"  {i:<4} {r.filename[:48]:<48} {r.worker_id:>3} {r.stall_kills:>3} "
              f"{r.queue_wait_s:>6.1f} {r.extract_s:>6.1f} {r.dl_s:>7.1f} {spd:>10} {r.status}{flag}")
            for n in r.notes:
                W(f"       → {n}")
        W()
        W("── LAST 10 FILES ───────────────────────────────────────────────────")
        for r in recs[-10:]:
            spd = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "—"
            W(f"  {r.filename[:52]:<52}  DL={r.dl_s:.1f}s  {spd}"
              f"{'  ← SLOW' if id(r) in slow_ids else ''}")
        W()
        W("── CONCURRENCY ─────────────────────────────────────────────────────")
        W(f"  {'Time':>7}  {'Browsers':>8}  {'Downloads':>9}  {'Queue':>5}  {'OK':>5}  {'Fail':>5}")
        step = max(1, len(self.snapshots)//45)
        for s in self.snapshots[::step]:
            W(f"  {s['ts']:>6.1f}s  {s['browsers']:>8}  {s['downloads']:>9}  "
              f"{s['queue']:>5}  {s['ok']:>5}  {s['fail']:>5}")
        W()
        W("── ERRORS ──────────────────────────────────────────────────────────")
        errs = [r for r in recs if r.error]
        if errs:
            for r in errs:
                W(f"  {r.filename[:52]:<52}  {r.error}")
        else:
            W("  None.")
        W("=" * 72)

        with open(lp, "w", encoding="utf-8") as f:
            f.write(buf.getvalue())
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({
                "session": {
                    "version": VERSION,
                    "start": datetime.datetime.now().isoformat(),
                    "duration_s": round(el, 2),
                    "config": self.cfg,
                    "total": len(recs),
                    "ok": len(ok_r),
                    "fail": len(recs) - len(ok_r),
                    "stall_kills": sum(r.stall_kills for r in recs),
                    "median_dl_s": round(med, 2),
                },
                "files": [
                    {k: round(v, 3) if isinstance(v, float) else v
                     for k, v in asdict(r).items()}
                    for r in recs
                ],
                "stall_events": self.stall_events,
                "concurrency": self.snapshots,
            }, f, indent=2)
        return lp, jp


class _StallKill(Exception):
    pass


async def download_file(
    proxy_url: str,
    cookies: str,
    dest: str,
    rec: FileRecord,
    bytes_acc: collections.deque,
    kill_evt: asyncio.Event,
    kills_so_far: int,
    telem: Telemetry | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> tuple[bool, str, int]:
    """Download a single file with resume support, stall detection, and proxy rotation."""
    tmp = dest + ".tmp"
    loop = asyncio.get_running_loop()

    def note(msg: str, tag: str = "warn") -> None:
        """Record a mid-transfer event in the report and surface it live if a front-end is listening."""
        rec.notes.append(msg)
        if on_event:
            on_event(msg, tag)
    detect = kills_so_far < STALL_MAX_KILL

    def _write(f, data: bytes):
        f.write(data)

    for att in range(DL_INNER_RETRIES):
        resume = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        ref = referer_for(proxy_url)
        hdrs = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": ref,
            "Connection": "keep-alive",
        }
        if cookies:
            hdrs["Cookie"] = cookies
        if resume > 0:
            hdrs["Range"] = f"bytes={resume}-"

        proxy_cfg = _PROXY_POOL.next()
        dl_session = _PROXY_POOL.get_session(proxy_cfg) if proxy_cfg else _sess()
        dl_proxy = proxy_cfg["url"] if proxy_cfg else None
        dl_proxy_auth = proxy_cfg["auth"] if proxy_cfg else None

        try:
            dl_t0 = time.monotonic()
            downloaded = resume
            req_kwargs = dict(headers=hdrs)
            if dl_proxy:
                req_kwargs["proxy"] = dl_proxy
                req_kwargs["proxy_auth"] = dl_proxy_auth

            async with dl_session.get(proxy_url, **req_kwargs) as r:
                if r.status == 416:
                    if os.path.exists(tmp):
                        os.replace(tmp, dest)
                    rec.file_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
                    return True, "ok", 0
                if r.status not in (200, 206):
                    return False, f"HTTP {r.status}", resume
                if r.status == 200 and resume > 0:
                    resume = 0
                    note(f"{rec.filename}: server ignored the resume request (HTTP 200) — restarting from zero")

                file_size = int(r.headers.get("Content-Length", 0)) + resume
                if file_size > 0:
                    rec.file_bytes = file_size

                effective_detect = detect and (file_size == 0 or file_size >= STALL_MIN_FILE_BYTES)
                f = open(tmp, "ab" if resume > 0 else "wb")
                speed_win: collections.deque = collections.deque(maxlen=8000)
                pub_win: collections.deque = collections.deque(maxlen=600)
                last_pub = dl_t0
                downloaded = resume
                last_check = dl_t0

                try:
                    buf: list[bytes] = []
                    bufsz = 0
                    async for chunk in r.content.iter_chunked(RECV_CHUNK):
                        if not chunk:
                            break
                        if kill_evt.is_set():
                            raise _StallKill()
                        now = time.monotonic()
                        downloaded += len(chunk)
                        speed_win.append((now, len(chunk)))
                        pub_win.append((now, len(chunk)))
                        bytes_acc.append((now, len(chunk)))
                        buf.append(chunk)
                        bufsz += len(chunk)
                        if bufsz >= WRITE_BUF:
                            data = b"".join(buf)
                            buf = []
                            bufsz = 0
                            await loop.run_in_executor(_POOL, _write, f, data)

                        elapsed = now - dl_t0

                        if now - last_pub >= 0.25:
                            last_pub = now
                            pub_cut = now - 3.0
                            while pub_win and pub_win[0][0] < pub_cut:
                                pub_win.popleft()
                            pub_span = max(now - pub_win[0][0], 0.25) if pub_win else 1.0
                            rec.done_bytes = downloaded
                            rec.live_mbs = sum(b for _, b in pub_win) / pub_span / 1_048_576

                        if effective_detect and (now - last_check) >= STALL_CHECK_S:
                            last_check = now
                            if elapsed >= STALL_GRACE_S:
                                pct = downloaded / file_size if file_size > 0 else 0.0
                                cutoff = now - STALL_WIN_S
                                while speed_win and speed_win[0][0] < cutoff:
                                    speed_win.popleft()
                                win_bytes = sum(b for _, b in speed_win)
                                if win_bytes >= STALL_MIN_BYTES_IN_WIN and pct < STALL_SAFE_PCT:
                                    win_s = max(now - speed_win[0][0], 1.0)
                                    spd = win_bytes / win_s / 1e6
                                    if spd < STALL_MIN_MBS:
                                        if telem is not None:
                                            telem.stall(
                                                rec.filename, spd, downloaded,
                                                f"slow ({spd:.2f} MB/s, {pct*100:.0f}%) → kill",
                                            )
                                        kill_evt.set()
                                        raise _StallKill()

                    if buf:
                        bytes_acc.append((time.monotonic(), sum(len(b) for b in buf)))
                        await loop.run_in_executor(_POOL, _write, f, b"".join(buf))
                finally:
                    f.close()

            os.replace(tmp, dest)
            dl_s = max(time.monotonic() - dl_t0, 0.001)
            net = downloaded - resume
            if net > 0:
                rec.avg_mbs = net / dl_s / 1e6
            rec.file_bytes = rec.file_bytes or downloaded
            rec.done_bytes = downloaded
            return True, "ok", 0

        except _StallKill:
            return False, "stall_killed", downloaded
        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError):
            note(f"connection dropped att {att+1}", "retry")
            if att < DL_INNER_RETRIES - 1:
                await asyncio.sleep(0.5 * (att + 1))
                continue
            return False, "connection dropped", downloaded
        except asyncio.TimeoutError:
            note(f"timeout att {att+1}", "retry")
            if att < DL_INNER_RETRIES - 1:
                await asyncio.sleep(1 + att)
                continue
            return False, "timeout", downloaded
        except Exception as e:
            err = str(e)
            note(f"error att {att+1}: {err}", "retry")
            if att < DL_INNER_RETRIES - 1 and ("ContentLengthError" in err or "not enough data" in err.lower()):
                await asyncio.sleep(0.5 * (att + 1))
                continue
            return False, err, downloaded

    return False, "max retries", 0
