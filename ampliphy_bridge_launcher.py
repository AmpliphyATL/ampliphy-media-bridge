#!/usr/bin/env python
"""
AmpliPhy Media Bridge — Standalone Launcher
Metadata Resolver for Independent Artists on AmpliPhy Radio
Windows .exe - just run it, the dashboard opens automatically.
"""

import os
import sys
import subprocess
import webbrowser
import time
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    # Get the directory where this script/exe is located
    app_dir = Path(__file__).parent

    # Change to app directory so imports work
    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))

    logger.info("=" * 70)
    logger.info("AmpliPhy Media Bridge")
    logger.info("Metadata Resolver for Independent Artists")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Starting server...")
    logger.info("Dashboard will open in your browser at:")
    logger.info("  http://localhost:8765")
    logger.info("")
    logger.info("Press Ctrl+C to stop the server.")
    logger.info("-" * 70)

    # Wait a moment, then open the dashboard in the default browser
    time.sleep(2)
    try:
        webbrowser.open("http://localhost:8765")
        logger.info("Opening dashboard in browser...")
    except Exception as e:
        logger.warning(f"Could not auto-open browser: {e}")
        logger.info("Visit http://localhost:8765 manually.")

    # Start the server
    try:
        import uvicorn
        from app import app

        logger.info("")
        logger.info(f"Uvicorn version: {uvicorn.__version__}")
        logger.info(f"FastAPI app: {app.title} v{app.version}")
        logger.info("")

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8765,
            log_level="info",
            access_log=True
        )
    except KeyboardInterrupt:
        logger.info("")
        logger.info("Server stopped by user (Ctrl+C)")
        sys.exit(0)
    except ImportError as e:
        logger.error(f"Import Error: {e}")
        logger.error("")
        logger.error("Missing required module. Please install dependencies:")
        logger.error("  pip install fastapi uvicorn requests")
        logger.error("")
        input("Press Enter to close...")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        logger.error("")
        logger.error("Detailed error information:")
        import traceback
        logger.error(traceback.format_exc())
        logger.error("")
        input("Press Enter to close...")
        sys.exit(1)

if __name__ == "__main__":
    main()
