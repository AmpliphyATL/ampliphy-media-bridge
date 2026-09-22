"""Standalone launcher for AmpliPhy MetaBridge with native PyWebView window.

Starts the FastAPI/Uvicorn server in a background daemon thread and opens
the dashboard in a native desktop window (PyWebView).

Built as a --windowed one-file .exe, so nothing is printed to a console.
Everything is written to AmpliPhyBridge.log next to the .exe, and any fatal
problem is shown in a native message box so it is never silent.
"""

import logging
import os
import pathlib
import socket
import sys
import threading
import time
import webbrowser

# A --windowed PyInstaller exe has no console: sys.stdout/stderr are None,
# which makes uvicorn's default log formatter blow up. Give them a sink.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# --------------------------------------------------------------------------
# Where do persistent files live?
#
# Settings, the database and the log must SURVIVE upgrades. Every new build is
# unzipped into a new folder (AmpliPhyBridge-v43, -v44, ...), so keeping them next
# to the .exe meant every upgrade started with a blank Settings tab. They now live
# in a fixed per-user folder:
#     %LOCALAPPDATA%\AmpliPhy MetaBridge\      (Windows)
#     ~/.ampliphy-metabridge/                    (elsewhere / dev)
# On first launch of this layout, anything found next to the .exe (an older
# build's settings/database) is copied over once, so nothing is lost.
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = pathlib.Path(sys.executable).resolve().parent
else:
    APP_DIR = pathlib.Path(__file__).resolve().parent


def _user_data_dir() -> pathlib.Path:
    override = os.environ.get("METABRIDGE_HOME")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base and sys.platform.startswith("win"):
        return pathlib.Path(base) / "AmpliPhy MetaBridge"
    return pathlib.Path.home() / ".ampliphy-metabridge"


DATA_DIR = _user_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "metabridge.env.json"
DB_FILE = DATA_DIR / "resolver.sqlite"


def _migrate_from_app_dir():
    """One-time: bring settings/DB from an older build's folder (next to the exe)."""
    import shutil
    if SETTINGS_FILE.exists():
        return
    # Candidates: this exe's folder, then sibling folders of earlier builds
    # (e.g. Downloads\AmpliPhyBridge-v38 next to Downloads\AmpliPhyBridge-v44), newest first.
    candidates = [APP_DIR]
    try:
        sibs = [d for d in APP_DIR.parent.iterdir() if d.is_dir() and d != APP_DIR and (d / "metabridge.env.json").is_file()]
        sibs.sort(key=lambda d: (d / "metabridge.env.json").stat().st_mtime, reverse=True)
        candidates += sibs
    except Exception:
        pass
    for folder in candidates:
        src = folder / "metabridge.env.json"
        if not src.is_file():
            continue
        try:
            shutil.copy2(src, SETTINGS_FILE)
            old_db = folder / "data" / "resolver.sqlite"
            if old_db.is_file() and not DB_FILE.exists():
                shutil.copy2(old_db, DB_FILE)
            return
        except Exception:
            continue


_migrate_from_app_dir()

os.environ.setdefault("RESOLVER_DB", str(DB_FILE))
os.environ.setdefault("METABRIDGE_SETTINGS_FILE", str(SETTINGS_FILE))

LOG_FILE = DATA_DIR / "AmpliPhyBridge.log"
# Roll the log over at midnight and keep the last 7 days; older days are deleted
# automatically so the folder never fills up.
from logging.handlers import TimedRotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=7, encoding="utf-8")],
)
log = logging.getLogger("metabridge_launcher")

try:
    from build_info import BUILD_NUMBER
except Exception:  # running from source without a stamp
    BUILD_NUMBER = "dev"
VERSION = f"v.{BUILD_NUMBER}"

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
# Only this PC can reach the dashboard/API unless explicitly opened up:
#   set METABRIDGE_BIND=0.0.0.0 to allow other machines on the network.
BIND = os.environ.get("METABRIDGE_BIND", "127.0.0.1")
os.environ.setdefault("METABRIDGE_TCP_HOST", BIND)  # TCP intake follows the same rule

_server_error = None  # set by the server thread if it dies


def show_error(title, message):
    """Native message box on Windows; falls back to the log elsewhere."""
    log.error("%s: %s", title, message)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10)  # MB_ICONERROR
    except Exception:
        pass


def port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def server_is_metabridge(url):
    """True if something on the port already answers like MetaBridge."""
    try:
        import requests
        r = requests.get(url, timeout=2)
        return r.status_code == 200 and "MetaBridge" in r.text
    except Exception:
        return False


def wait_for_server(url, timeout=30):
    import requests
    start = time.time()
    while time.time() - start < timeout:
        if _server_error is not None:
            return False
        try:
            if requests.get(url, timeout=2).status_code == 200:
                log.info("Server is ready")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log.error("Server did not respond within %s seconds", timeout)
    return False


def start_server():
    global _server_error
    try:
        import uvicorn
        from app_enhanced import app  # loads config on import

        log.info("Starting Uvicorn on %s:%s", BIND, PORT)
        uvicorn.run(app, host=BIND, port=PORT, log_level="info", access_log=False, log_config=None)
    except Exception as e:  # any failure must be visible
        _server_error = e
        log.exception("Server failed to start")


AUTOSTARTED = "--autostart" in sys.argv  # launched by Windows at login


def register_autostart():
    """First run: put ourselves in the Windows startup list unless the user
    turned that off in Settings. Runs quietly; never blocks startup."""
    try:
        import json
        from metabridge import autostart
        if not autostart.is_windows():
            return
        prefs = {}
        sf = pathlib.Path(os.environ["METABRIDGE_SETTINGS_FILE"])
        if sf.exists():
            try:
                prefs = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                prefs = {}
        if prefs.get("autostart", True):
            ok = autostart.enable()   # also refreshes the path if the exe moved
            log.info("Start-with-Windows registered: %s", ok)
        else:
            log.info("Start-with-Windows disabled by user")
    except Exception:
        log.exception("Could not update start-with-Windows entry")


def open_dashboard():
    try:
        import webview
        log.info("Opening native window (minimized=%s)", AUTOSTARTED)
        webview.create_window(
            title=f"AmpliPhy MetaBridge {VERSION}",
            url=URL,
            width=1200,
            height=800,
            min_size=(800, 600),
            minimized=AUTOSTARTED,
        )
        webview.start()
    except ImportError:
        log.warning("PyWebView not available, opening in default browser")
        webbrowser.open(URL)


def main():
    log.info("=" * 60)
    log.info("AmpliPhy MetaBridge %s  (app dir: %s, data dir: %s, autostart=%s)", VERSION, APP_DIR, DATA_DIR, AUTOSTARTED)
    log.info("=" * 60)
    register_autostart()

    if port_in_use(PORT):
        if server_is_metabridge(URL):
            # An older instance is still running - just show it instead of dying.
            log.warning("MetaBridge already running on port %s; opening a window to it", PORT)
            open_dashboard()
            return
        show_error(
            "AmpliPhy MetaBridge",
            f"Port {PORT} is already in use by another program.\n\n"
            f"Close whatever is using it (or an old MetaBridge still in the system tray) and try again.\n\n"
            f"Log: {LOG_FILE}",
        )
        sys.exit(1)

    threading.Thread(target=start_server, daemon=True).start()
    time.sleep(1.5)

    if not wait_for_server(URL):
        detail = f"{type(_server_error).__name__}: {_server_error}" if _server_error else "timed out"
        show_error(
            "AmpliPhy MetaBridge failed to start",
            f"The server did not start.\n\n{detail}\n\nFull details are in:\n{LOG_FILE}",
        )
        sys.exit(1)

    log.info("Server running at %s", URL)
    open_dashboard()
    log.info("Dashboard closed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        show_error("AmpliPhy MetaBridge crashed", f"{type(e).__name__}: {e}\n\nSee {LOG_FILE}")
        log.exception("Fatal error")
        sys.exit(1)
