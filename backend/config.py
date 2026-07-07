"""Configuration loaded from environment variables."""

import os


def get_host() -> str:
    """Server bind address."""
    return os.getenv("LM_CONSOLE_HOST", "0.0.0.0")


def get_port() -> int:
    """Server port."""
    return int(os.getenv("LM_CONSOLE_PORT", "8080"))


def get_lm_studio_url() -> str:
    """LM Studio API base URL."""
    return os.getenv("LM_STUDIO_URL", "http://localhost:1234")


def get_static_dir() -> str:
    """Path to the static files directory."""
    return os.path.join(os.path.dirname(__file__), "..", "static")
