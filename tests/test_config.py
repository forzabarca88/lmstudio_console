"""Tests for the config module."""

import unittest
import os
import sys
from unittest.mock import patch

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.config import get_host, get_port, get_lm_studio_url, get_static_dir


class TestConfig(unittest.TestCase):
    """Verify config reads environment variables with correct defaults."""

    def test_default_host(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_host(), "0.0.0.0")

    def test_custom_host(self):
        with patch.dict(os.environ, {"LM_CONSOLE_HOST": "127.0.0.1"}):
            self.assertEqual(get_host(), "127.0.0.1")

    def test_default_port(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_port(), 8080)

    def test_custom_port(self):
        with patch.dict(os.environ, {"LM_CONSOLE_PORT": "9090"}):
            self.assertEqual(get_port(), 9090)

    def test_default_lm_studio_url(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_lm_studio_url(), "http://localhost:1234")

    def test_custom_lm_studio_url(self):
        with patch.dict(os.environ, {"LM_STUDIO_URL": "http://custom:5678"}):
            self.assertEqual(get_lm_studio_url(), "http://custom:5678")

    def test_static_dir_exists(self):
        path = get_static_dir()
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.exists(os.path.join(path, "index.html")))


if __name__ == "__main__":
    unittest.main()
