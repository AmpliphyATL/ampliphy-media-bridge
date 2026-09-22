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
# Where do persistent files live?  For a PyInstaller one-file build,
# __file__ points into a temp folder that is deleted on exit, so settings and
# the database must live next to the real .exe instead.
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = pathlib.Path(sys.executable).resolve().parent
else:
    APP_DIR = pathlib.Path(__file__).resolve().parent

DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("RESOLVER_DB", str(DATA_DIR / "resolver.sqlite"))
os.environ.setdefault("METABRIDGE_SETTINGS_FILE", str(APP_DIR / "metabridge.env.json"))

LOG_FILE = APP_DIR / "AmpliPhyBridge.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("metabridge_launcher")

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"

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

        log.info("Starting Uvicorn on 0.0.0.0:%s", PORT)
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", access_log=False, log_config=None)
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
            title="AmpliPhy MetaBridge",
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
    log.info("AmpliPhy MetaBridge Launcher  (app dir: %s, autostart=%s)", APP_DIR, AUTOSTARTED)
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
