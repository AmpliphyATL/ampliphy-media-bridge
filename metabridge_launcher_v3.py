#!/usr/bin/env python3
"""
MetaBridge Standalone Launcher with Embedded Browser Window

Starts FastAPI/Uvicorn server in background and opens it in a native window (PyWebView).
This is a desktop application, not just a browser launcher:
- Native window with title bar, minimize/maximize/close buttons
- Runs independently without system browser requirement
- Can be pinned to taskbar
- Integrates with Windows application menu
- Runs server in background thread

Usage: python metabridge_launcher_v3.py
or run as: AmpliPhyBridge.exe (after PyInstaller compilation)
"""

import os
import sys
import threading
import time
import logging
import socket
import requests
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("metabridge.launcher")


def is_port_available(port, host="127.0.0.1"):
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def wait_for_server(url, timeout=30, retries=60):
    """Wait for the server to be ready and responding."""
    start_time = time.time()
    attempt = 0
    
    while attempt < retries and (time.time() - start_time) < timeout:
        try:
            response = requests.get(url, timeout=1)
            if response.status_code < 500:
                log.info("Server is ready and responding")
                return True
        except requests.exceptions.ConnectionError:
            pass
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            log.debug(f"Server check attempt {attempt}: {e}")
        
        attempt += 1
        time.sleep(0.5)
    
    log.warning(f"Server did not respond within {timeout} seconds")
    return False


def start_server(port=8765, host="0.0.0.0"):
    """Start Uvicorn server in background thread."""
    try:
        import uvicorn
        from app import app
        
        log.info(f"Starting Uvicorn server on http://localhost:{port}")
        
        # Run uvicorn in the current thread (thread will be daemonized)
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=True,
            use_colors=True
        )
    except OSError as e:
        if "Address already in use" in str(e) or "only one usage" in str(e):
            log.error(f"Port {port} is already in use. Is another instance running?")
            log.info("Attempting to connect to existing server...")
            return False
        else:
            log.error(f"Failed to start server: {e}")
            return False
    except Exception as e:
        log.error(f"Unexpected error starting server: {e}")
        return False


def open_dashboard(port=8765):
    """Open dashboard in PyWebView window."""
    server_url = f"http://localhost:{port}"
    
    try:
        import webview
        log.info("Opening dashboard in PyWebView window")
        
        window = webview.create_window(
            title='AmpliPhy MetaBridge',
            url=server_url,
            width=1200,
            height=800,
            resizable=True,
            fullscreen=False,
            min_size=(800, 600),
        )
        
        webview.start(debug=False, http_port=port)
        sys.exit(0)
        
    except ImportError:
        log.warning("PyWebView not available, falling back to system browser")
        open_dashboard_browser(server_url)
    except Exception as e:
        log.error(f"Error opening dashboard: {e}")
        log.info(f"Open manually at {server_url}")
        sys.exit(1)


def open_dashboard_browser(url):
    """Fallback: open dashboard in system default browser."""
    import webbrowser
    try:
        log.info(f"Opening dashboard at {url}")
        webbrowser.open(url)
        # Keep the process alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Closing launcher")
            sys.exit(0)
    except Exception as e:
        log.error(f"Failed to open browser: {e}")
        sys.exit(1)


def main():
    """Main entry point: start server and open dashboard."""
    print("=" * 70)
    print("MetaBridge — AmpliPHy Radio Metadata Resolver")
    print("=" * 70)
    print()
    
    port = 8765
    host = "0.0.0.0"
    server_url = f"http://localhost:{port}"
    
    log.info("Starting server and opening dashboard...")
    
    # Start server in background thread
    server_thread = threading.Thread(
        target=start_server,
        args=(port, host),
        daemon=True,  # Make thread daemonic so it doesn't block exit
        name="MetaBridge-Server"
    )
    server_thread.start()
    
    # Wait a moment for server to start
    log.info("Waiting for server to start...")
    time.sleep(2)
    
    # Check if server is responding
    if wait_for_server(server_url, timeout=10):
        # Server is ready, open dashboard
        open_dashboard(port)
    else:
        # Server may not have started, but try dashboard anyway
        log.warning("Server may not be ready, but attempting to open dashboard...")
        open_dashboard(port)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
MetaBridge Standalone Launcher with Embedded Browser Window
Starts FastAPI/Uvicorn server in background and opens it in a native window (PyWebView).

This is a desktop application, not just a browser launcher:
- Native window with title bar, minimize/maximize/close buttons
- Runs independently without system browser requirement
- Can be pinned to taskbar
- Integrates with Windows application menu
- Runs server in background thread

Usage: python metabridge_launcher_v3.py
  or run as: AmpliPhyBridge.exe (after PyInstaller compilation)

PyInstaller command (use in build.yml):
  python -m PyInstaller --onefile --console \\
    --name "AmpliPhyBridge" \\
    --distpath "." --workpath ".build" \\
    metabridge_launcher_v3.py
"""

import os
import sys
import threading
import time
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("metabridge.launcher")


def main():
    # Get the directory where this script/exe is located
    app_dir = Path(__file__).parent
    
    # Change to app directory so imports work
    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))
    
    print("=" * 70)
    print("MetaBridge — AmpliPHy Radio Metadata Resolver")
    print("=" * 70)
    print()
    print("Starting server and opening dashboard...")
    print()
    
    # Start Uvicorn server in a background thread
    server_thread = threading.Thread(target=start_server, daemon=False)
    server_thread.start()
    
    # Wait for server to start before opening window
    time.sleep(2)
    
    # Open dashboard in native window (PyWebView)
    open_dashboard()


def start_server():
    """Run Uvicorn server on localhost:8765"""
    try:
        import uvicorn
        from app import app
        
        log.info("Starting Uvicorn server on http://localhost:8765")
        
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8765,
            log_level="info",
            access_log=True,
            use_colors=True
        )
    except KeyboardInterrupt:
        log.info("Server stopped by user")
        sys.exit(0)
    except Exception as e:
        log.exception("Error starting server: %s", e)
        print(f"\n❌ Server failed to start: {e}")
        input("Press Enter to close...")
        sys.exit(1)


def open_dashboard():
    """Open the dashboard in a native window using PyWebView"""
    try:
        import webview
        
        log.info("Opening dashboard in PyWebView window")
        
        # Create and show the window
        window = webview.create_window(
            title='AmpliPhy MetaBridge',
            url='http://localhost:8765/',
            width=1200,
            height=800,
            resizable=True,
            fullscreen=False,
            min_size=(800, 600),
        )
        
        # This blocks until the window is closed
        webview.start(debug=False, http_port=8765)
        
        # When window closes, exit the application
        log.info("Dashboard window closed, shutting down")
        sys.exit(0)
        
    except ImportError:
        log.error("PyWebView not installed. Falling back to system browser.")
        open_dashboard_browser()
    except Exception as e:
        log.error("PyWebView failed: %s. Falling back to system browser.", e)
        open_dashboard_browser()


def open_dashboard_browser():
    """Fallback: open dashboard in system default browser (original behavior)"""
    try:
        import webbrowser
        
        log.info("Opening dashboard in system default browser")
        webbrowser.open("http://localhost:8765")
        
        print("-" * 70)
        print("Dashboard opened in your default browser:")
        print("  http://localhost:8765")
        print()
        print("Server is running. Press Ctrl+C to stop.")
        print("-" * 70)
        
        # Keep the thread alive while waiting for interrupt
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Server stopped by user (Ctrl+C)")
            sys.exit(0)
            
    except Exception as e:
        log.error("Failed to open browser: %s", e)
        print(f"\n❌ Could not open browser: {e}")
        print("\nManually open your browser and visit:")
        print("  http://localhost:8765")
        input("Press Enter to close...")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Unhandled exception: %s", e)
        print(f"\n❌ Fatal error: {e}")
        input("Press Enter to close...")
        sys.exit(1)
