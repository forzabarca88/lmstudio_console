"""Backend tests.

Verifies server configuration, trace logging, and proxy behavior.
"""

import unittest
import os
import sys
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.config import get_host, get_port, get_lm_studio_url, get_static_dir
from backend.logger import TraceLogger, RequestTrace


class TestConfig(unittest.TestCase):
    """Verify server configuration."""

    def test_default_host(self):
        self.assertEqual(get_host(), "0.0.0.0")

    def test_default_port(self):
        self.assertEqual(get_port(), 8080)

    def test_default_lm_studio_url(self):
        self.assertEqual(get_lm_studio_url(), "http://localhost:1234")

    def test_static_dir_exists(self):
        path = get_static_dir()
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.exists(os.path.join(path, "index.html")))


class TestTraceLogger(unittest.TestCase):
    """Verify trace logging captures request details for debugging."""

    def setUp(self):
        self.logger = TraceLogger()
        self.capture = io.StringIO()
        self.logger.logger.handlers[0].stream = self.capture

    def test_request_logged(self):
        """Outgoing requests are logged with method, path, and target."""
        self.logger.log_request(
            "GET", "/api/v1/models",
            "http://localhost:1234/api/v1/models"
        )
        output = self.capture.getvalue()
        self.assertIn("GET", output)
        self.assertIn("/api/v1/models", output)

    def test_response_logged(self):
        """Responses are logged with status code and duration."""
        trace = self.logger.log_request(
            "GET", "/api/v1/models",
            "http://localhost:1234/api/v1/models"
        )
        self.logger.log_response(trace, 200, "application/json", 0.5, '{"data":[]}')
        output = self.capture.getvalue()
        self.assertIn("200", output)
        self.assertIn("OK", output)
        self.assertIn("0.500s", output)

    def test_error_logged(self):
        """Failed requests are logged with error details."""
        trace = self.logger.log_request(
            "GET", "/api/v1/models",
            "http://localhost:1234/api/v1/models"
        )
        self.logger.log_error(trace, ConnectionError("Connection refused"), 0.1)
        output = self.capture.getvalue()
        self.assertIn("ERR", output)
        self.assertIn("Connection refused", output)


class TestRequestTrace(unittest.TestCase):
    """Verify trace data structure."""

    def test_trace_fields(self):
        trace = RequestTrace("POST", "/v1/chat", "http://localhost/v1/chat", '{"messages":[]}')
        self.assertEqual(trace.method, "POST")
        self.assertEqual(trace.path, "/v1/chat")
        self.assertEqual(trace.target, "http://localhost/v1/chat")
        self.assertEqual(trace.body, '{"messages":[]}')
        self.assertIsNone(trace.status)
        self.assertEqual(trace.duration, 0.0)

    def test_status_text(self):
        trace = RequestTrace("GET", "/test", "http://localhost/test")
        self.assertEqual(trace._status_text(200), "OK")
        self.assertEqual(trace._status_text(404), "Not Found")
        self.assertEqual(trace._status_text(500), "Internal Server Error")


if __name__ == "__main__":
    unittest.main()
