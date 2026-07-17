"""Entry point for LM Studio Console.

Usage:
    uv run python run.py
"""

import os
import signal
import sys
import threading

# Ensure the project root is on the Python path so 'backend' is importable
sys.path.insert(0, os.path.dirname(__file__))

_FORCE_TIMEOUT = 5  # seconds before force exit

_force_shutdown_timer = None


def _force_shutdown():
    """Force exit after timeout."""
    global _force_shutdown_timer
    _force_shutdown_timer = None
    print("\nForce shutdown (timeout exceeded).", flush=True)
    os._exit(1)


def _handle_graceful_shutdown(signum, frame):
    """Handle SIGINT/SIGTERM: start 5s countdown, then force exit."""
    global _force_shutdown_timer

    # Cancel any existing timer
    if _force_shutdown_timer:
        _force_shutdown_timer.cancel()
        _force_shutdown_timer = None

    print(f"\nShutdown requested (signal {signum}). Stopping server...", flush=True)

    # Force exit after FORCE_TIMEOUT seconds if graceful shutdown doesn't complete
    _force_shutdown_timer = threading.Timer(_FORCE_TIMEOUT, _force_shutdown)
    _force_shutdown_timer.daemon = True
    _force_shutdown_timer.start()


def run() -> None:
    """Start the FastAPI server using uvicorn with graceful shutdown."""
    global _force_shutdown_timer

    from backend.server import run as server_run

    # Register signal handlers before uvicorn starts
    signal.signal(signal.SIGINT, _handle_graceful_shutdown)
    signal.signal(signal.SIGTERM, _handle_graceful_shutdown)

    try:
        server_run()
    finally:
        # Clean up timer
        if _force_shutdown_timer:
            _force_shutdown_timer.cancel()
            _force_shutdown_timer = None


if __name__ == "__main__":
    run()
