"""
MoonDownloader V2 -- the GUI host
═══════════════════════════════════════
language: Python 3.10+, file: moon_bridge.py, runtime: stdlib (pywebview optional)

    python moon_bridge.py                 # opens Edge/Chrome in app mode
    python moon_bridge.py --pywebview     # force the pywebview window
    python moon_bridge.py --browser       # open the default browser
    python moon_bridge.py --serve         # server only, prints the URL
    MOON_DEBUG=1 python moon_bridge.py    # log every request

Why pywebview is not the default
────────────────────────────────
pywebview picks its Windows backend at runtime and, when the .NET bridge to
WebView2 fails to load (no pythonnet, no Evergreen runtime), it **falls back to
MSHTML silently** -- Trident, the IE11 engine. In there CSS grid, system-ui,
clamp(), color-mix() and backdrop-filter do not exist: the GUI unrolls into one
column with native blue sliders. No message, no error, just a page from 2013.

So the default is the other way round: a local HTTP server plus Edge (or Chrome)
launched with `--app=`, which opens a window with no tabs and no address bar --
the same Chromium rendering, no native dependency, no backend to guess.

Local server security
─────────────────────
The socket listens on 127.0.0.1 only, on a free port chosen by the kernel, and
every /api/ call must carry the token minted at startup. No token: 403. The API
starts downloads and reads paths, so this is not a formality.

*The server exits by itself after IDLE_EXIT_S with no requests: the page polls every
80 ms, so "no requests" means "the window is closed" and the app exits instead of
lingering as an orphan process.*
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import secrets
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE / "web"
INDEX = WEB / "index.html"
SETTINGS = HERE / "settings.json"

IDLE_EXIT_S = 12.0
DEBUG = bool(os.environ.get("MOON_DEBUG"))

SETTINGS_KEYS = (
    "out_folder", "mode", "workers", "dl_streams", "retries",
    "dn_pages", "dn_captcha", "dn_chrome", "dn_apikey", "links_text", "lang",
)

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


# ── settings ────────────────────────────────────────────────────────────────
def load_settings() -> dict:
    """Read settings.json, tolerating every way it can be broken."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k in SETTINGS_KEYS} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(cfg: dict) -> None:
    """Atomic write: a crash mid-save must not leave a truncated settings file."""
    keep = {k: v for k, v in (cfg or {}).items() if k in SETTINGS_KEYS}
    if not keep:
        return
    fd, tmp = tempfile.mkstemp(dir=str(HERE), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── native dialogs, out of process ──────────────────────────────────────────
_DIALOG_SRC = r'''
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    # Some window managers reject -topmost; dialog still works without it.
    pass
kind = sys.argv[1]
if kind == "folder":
    out = filedialog.askdirectory(title="Destination folder")
elif kind == "chrome":
    out = filedialog.askopenfilename(title="Select chrome.exe",
        filetypes=[("chrome.exe", "chrome.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
else:
    out = filedialog.askopenfilename(title="Select the links file",
        filetypes=[("Text", "*.txt"), ("All files", "*.*")])
sys.stdout.write(out or "")
'''


def native_dialog(kind: str) -> str:
    """Open a Tk file dialog in a throwaway child process.

    *A dialog must own a mainloop on the thread that created it. The HTTP handler
    runs on a pool thread, so opening Tk there deadlocks or crashes depending on
    the platform -- a 200 ms subprocess is the boring, reliable answer.*
    """
    try:
        proc = subprocess.run([sys.executable, "-c", _DIALOG_SRC, kind],
                              capture_output=True, text=True, timeout=300)
        return proc.stdout.strip()
    except Exception:
        # Subprocess timeout/crash or Tk missing — empty path is the safe UI answer.
        return ""


# ── the API both transports expose ──────────────────────────────────────────
class Api:
    """Every method is reachable as pywebview.api.<name> or POST /api/<name>.

    Return values must be JSON-able; each entry point answers with {"error": ...}
    instead of raising, and the GUI turns that into a toast.
    """

    def __init__(self, engine, window=None, dialogs=None):
        self.engine = engine
        self.window = window
        self.dialogs = dialogs or native_dialog       # injectable for tests

    # ── handshake ──────────────────────────────────────────────────────────
    def hello(self) -> dict:
        from moon_engine import HAVE_CURL_CFFI, VERSION
        settings = load_settings()
        settings.setdefault("out_folder", self.engine._cfg["out_folder"])
        settings.setdefault("dn_chrome", self.engine._cfg["dn_chrome"])
        settings.setdefault("dn_apikey", self.engine._cfg["dn_apikey"])
        return {"version": VERSION, "have_curl": HAVE_CURL_CFFI, "settings": settings}

    # ── engine ─────────────────────────────────────────────────────────────
    def snapshot(self, cursor: int = 0) -> dict:
        try:
            return self.engine.snapshot(int(cursor or 0))
        except Exception as e:                        # never break the poll loop
            return {"error": f"snapshot: {e}"}

    def start(self, cfg: dict) -> dict:
        try:
            return self.engine.start(cfg or {})
        except Exception as e:
            return {"error": f"start: {e}"}

    def stop(self) -> dict:
        return self.engine.stop()

    def clear_files(self) -> dict:
        return self.engine.clear_files()

    # ── dialogs ────────────────────────────────────────────────────────────
    def browse_folder(self) -> dict:
        path = self.dialogs("folder")
        return {"path": path} if path else {}

    def browse_chrome(self) -> dict:
        path = self.dialogs("chrome")
        return {"path": path} if path else {}

    def load_txt(self) -> dict:
        path = self.dialogs("txt")
        if not path:
            return {}
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read().strip()
        except OSError as e:
            return {"error": f"{os.path.basename(path)}: {e}"}
        return {"text": text, "count": len([l for l in text.splitlines() if l.strip()]),
                "path": path}

    # ── settings ───────────────────────────────────────────────────────────
    def settings_save(self, cfg: dict) -> dict:
        save_settings(cfg)
        return {"ok": True}

    def settings_load(self) -> dict:
        return load_settings()


# ── local HTTP transport ────────────────────────────────────────────────────
class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    api: Api
    token: str
    last_seen: float = 0.0


class _Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    # -- plumbing -----------------------------------------------------------
    def log_message(self, fmt, *args):                # quiet unless MOON_DEBUG
        if DEBUG:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _authorised(self) -> bool:
        """Loopback + per-run token. The API can start downloads and read paths."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        token = self.headers.get("X-Moon-Token", "")
        return secrets.compare_digest(token, self.server.token)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        # The GUI is served from a private loopback origin; nothing here should
        # ever be cached or embedded elsewhere.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:
        self.server.last_seen = time.monotonic()
        if self.path.startswith("/api/"):
            self._send_json({"error": "usa POST"}, 405)
            return
        if self.path in ("/", ""):
            self.path = "/index.html"
        self.path = self.path.split("?", 1)[0]
        super().do_GET()

    def _read_body(self) -> bytes:
        """Always drain the request body, even when the answer is an error.

        *HTTP/1.1 keeps the connection open. Replying 403 without consuming the
        body leaves those bytes in the socket, and the next request on the same
        connection is parsed starting at `{"args":[0]}POST /api/... ` -- which the
        server reports as 501 Unsupported method. Read first, judge after.*
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def do_POST(self) -> None:
        self.server.last_seen = time.monotonic()
        raw = self._read_body()

        if not self.path.startswith("/api/"):
            self._send_json({"error": "not found"}, 404)
            return
        if not self._authorised():
            self._send_json({"error": "forbidden"}, 403)
            return

        name = self.path[len("/api/"):].split("?", 1)[0]
        method = getattr(self.server.api, name, None)
        if not callable(method) or name.startswith("_"):
            self._send_json({"error": f"metodo sconosciuto: {name}"}, 404)
            return

        try:
            args = json.loads(raw or b"{}").get("args", [])
            result = method(*args)
        except Exception as e:
            self._send_json({"error": f"{name}: {e}"}, 200)
            return
        self._send_json(result if isinstance(result, dict) else {"value": result})


def serve(api: Api) -> tuple[_Server, str]:
    """Bind a loopback server on a kernel-chosen port; return it plus the URL."""
    server = _Server(("127.0.0.1", 0), _Handler)
    server.api = api
    server.token = secrets.token_urlsafe(24)
    server.last_seen = time.monotonic()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/index.html?t={server.token}"


# ── window launchers ────────────────────────────────────────────────────────
def find_chromium() -> str | None:
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch_app_window(browser: str, url: str) -> subprocess.Popen:
    """Chromium in --app mode: a real window, no tabs, no address bar.

    A separate --user-data-dir keeps the app out of the user's browsing session
    (and stops a running Edge from swallowing the launch and returning instantly).
    """
    profile = pathlib.Path(tempfile.gettempdir()) / "moondownloader-ui"
    return subprocess.Popen([
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--window-size=1500,950",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,TranslateUI,OptimizationGuideModelDownloading",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_pywebview(api: Api) -> int:
    """Only when asked for explicitly -- see the module docstring for why."""
    try:
        import webview
    except ImportError:
        print("pywebview is not installed:  pip install pywebview", file=sys.stderr)
        return 2
    from moon_engine import VERSION
    window = webview.create_window(
        f"MoonDownloader {VERSION}", url=INDEX.as_uri(), js_api=api,
        width=1500, height=950, min_size=(1100, 720),
        background_color="#05070c", text_select=False)
    api.window = window
    kwargs = {"debug": DEBUG}
    if sys.platform == "win32":
        # Pinned, not guessed. Without this pywebview may fall back to MSHTML.
        kwargs["gui"] = "edgechromium"
    webview.start(**kwargs)
    return 0


# ── entry point ─────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if not INDEX.exists():
        print(f"GUI mancante: {INDEX}", file=sys.stderr)
        return 2

    from moon_engine import Engine, VERSION

    engine = Engine()
    saved = load_settings()
    if saved:
        engine.apply_cfg(saved)
    api = Api(engine)

    if "--pywebview" in argv:
        try:
            return run_pywebview(api)
        finally:
            engine.stop()

    server, url = serve(api)
    print(f"MoonDownloader {VERSION}  ·  {url}")

    mode = "browser" if "--browser" in argv else ("serve" if "--serve" in argv else "app")
    child = None
    if mode == "app":
        browser = find_chromium()
        if browser:
            child = launch_app_window(browser, url)
            print(f"window: {os.path.basename(browser)} --app")
        else:
            mode = "browser"
            print("Edge/Chrome not found: opening the default browser")
    if mode == "browser":
        webbrowser.open(url)

    if mode == "serve":
        print("Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1.0)
            if child is not None and child.poll() is not None:
                print("window closed")
                break
            idle = time.monotonic() - server.last_seen
            if mode != "serve" and idle > IDLE_EXIT_S:
                print(f"no requests for {idle:.0f}s: exiting")
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.shutdown()
        if child is not None and child.poll() is None:
            child.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
