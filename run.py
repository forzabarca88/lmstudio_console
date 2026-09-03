"""Entry point for LM Studio Console.

Usage:
    uv run python run.py

All server startup and signal handling (graceful shutdown with a 5 second
force-exit on Ctrl+C) lives in `backend.server.run`, so every startup path
(e.g. the Docker image CMD) behaves identically.
"""

from backend.server import run

if __name__ == "__main__":
    run()
