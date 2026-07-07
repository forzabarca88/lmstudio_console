"""Entry point for LM Studio Console.

Usage:
    uv run python run.py
"""

import os
import sys

# Ensure the project root is on the Python path so 'backend' is importable
sys.path.insert(0, os.path.dirname(__file__))

from backend.server import run

if __name__ == "__main__":
    run()
