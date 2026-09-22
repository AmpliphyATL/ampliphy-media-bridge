"""Standalone launcher for AmpliPhy MetaBridge with native PyWebView window.

Starts the FastAPI/Uvicorn server in a background daemon thread and opens
the dashboard in a native desktop window (PyWebView).

Usage:
    python metabridge_launcher.py

The dashboard will open at http://localhost:8765 in a native window with:
- Proper window chrome (title bar, minimize/maximize buttons, taskbar integration)
- No terminal window visible
- Automatic server startup and health checking
- Graceful shutdown when window closes
"""

import logging
import os
import socket
import sys
import threading
import time
import webbrowser

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("metabridge_launcher")


def is_port_available(port, host="127.0.0.1"):
    """Check if port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def wait_for_server(url, timeout=30, retries=60):
    """Poll server with exponential backoff until it's ready."""
    start = time.time()
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                log.info(f"Server is ready (attempt {attempt + 1})")
                return True
        except Exception:
            pass

        if time.time() - start > timeout:
            log.error(f"Server did not respond within {timeout} seconds")
            return False

        time.sleep(0.5)

    return False


def start_server(port=8765, host="0.0.0.0"):
    """Start Uvicorn server in current thread (blocking)."""
    try:
        if not is_port_available(port, "127.0.0.1"):
            log.error(f"Port {port} is already in use. Is another instance running?")
            return False

        import uvicorn

        # Import the app - this will auto-load config
        from app_enhanced import app

        log.info(f"Starting Uvicorn server on {host}:{port}")

        # Run with minimal logging
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False
        )
        return True

    except OSError as e:
        if "Address already in use" in str(e):
            log.error(f"Port {port} is already in use")
        else:
            log.error(f"Failed to start server: {e}")
        return False
    except Exception as e:
        log.error(f"Unexpected error starting server: {e}")
        return False


def open_dashboard(port=8765):
    """Open dashboard in native window or browser."""
    url = f"http://127.0.0.1:{port}"

    try:
        import webview

        log.info("Opening dashboard in native PyWebView window...")

        webview.create_window(
            title="AmpliPhy MetaBridge",
            url=url,
            width=1200,
            height=800,
            min_size=(800, 600)
        )
        webview.start()

    except ImportError:
        log.warning("PyWebView not available, opening in default browser...")
        webbrowser.open(url)


def main():
    """Main entry point."""
    port = 8765

    log.info("=" * 60)
    log.info("AmpliPhy MetaBridge Launcher")
    log.info("=" * 60)

    # Start server in daemon thread
    log.info("Starting MetaBridge server...")
    server_thread = threading.Thread(
        target=start_server,
        args=(port,),
        daemon=True
    )
    server_thread.start()

    # Wait a moment for server to start
    time.sleep(2)

    # Check if server is ready
    url = f"http://127.0.0.1:{port}"
    if not wait_for_server(url):
        log.error("Server failed to start. Check the logs above.")
        sys.exit(1)

    log.info(f"Server is running at {url}")

    # Open dashboard
    open_dashboard(port)

    log.info("Dashboard closed.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("\nShutting down...")
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
