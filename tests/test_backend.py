"""Backend module tests.

Tests configuration, logging, and proxy functionality.
"""

import unittest
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend import config
from backend.logger import TraceLogger, RequestTrace


class TestConfig(unittest.TestCase):
    """Configuration loads correctly."""

    def test_default_host(self):
        """ARRANGE: No env vars set
        ACT: Get host
        ASSERT: Default host 0.0.0.0"""
        self.assertEqual(config.get_host(), "0.0.0.0")

    def test_default_port(self):
        """ARRANGE: No env vars set
        ACT: Get port
        ASSERT: Default port 8080"""
        self.assertEqual(config.get_port(), 8080)

    def test_default_lm_studio_url(self):
        """ARRANGE: No env vars set
        ACT: Get LM Studio URL
        ASSERT: Default URL"""
        self.assertEqual(config.get_lm_studio_url(), "http://localhost:1234")

    def test_static_dir_exists(self):
        """ARRANGE: Config module loaded
        ACT: Get STATIC_DIR
        ASSERT: Directory exists"""
        self.assertTrue(os.path.isdir(config.get_static_dir()))


class TestTraceLogger(unittest.TestCase):
    """Request tracing logs correctly."""

    def setUp(self):
        self.logger = TraceLogger()
        self.trace = RequestTrace(
            method="GET",
            path="/api/v1/models",
            target_url="http://localhost:1234/api/v1/models",
        )

    def test_trace_fields(self):
        """ARRANGE: RequestTrace created
        ACT: Check attributes
        ASSERT: All fields set correctly"""
        self.assertEqual(self.trace.method, "GET")
        self.assertEqual(self.trace.path, "/api/v1/models")
        self.assertEqual(self.trace.target, "http://localhost:1234/api/v1/models")
        self.assertEqual(self.trace.duration, 0.0)
        self.assertIsNone(self.trace.status)
        self.assertIsNone(self.trace.error)

    def test_status_text(self):
        """ARRANGE: RequestTrace with status codes
        ACT: Get status text
        ASSERT: Correct label"""
        self.assertEqual(self.trace._status_text(200), "OK")
        self.assertEqual(self.trace._status_text(404), "Not Found")
        self.assertEqual(self.trace._status_text(500), "Internal Server Error")

    def test_request_logged(self):
        """ARRANGE: TraceLogger created
        ACT: Log request with method, path, target_url
        ASSERT: Debug log emitted"""
        with unittest.mock.patch.object(self.logger._log, 'debug') as mock_debug:
            self.logger.log_request("GET", "/api/v1/models", "http://localhost:1234/api/v1/models")
            mock_debug.assert_called()

    def test_response_logged(self):
        """ARRANGE: TraceLogger created, trace has duration
        ACT: Log response
        ASSERT: Debug log emitted"""
        self.trace.duration = 1.5
        self.trace.status = 200
        with unittest.mock.patch.object(self.logger._log, 'debug') as mock_debug:
            self.logger.log_response(self.trace, 200, "application/json", 1.5)
            mock_debug.assert_called()

    def test_error_logged(self):
        """ARRANGE: TraceLogger created
        ACT: Log error
        ASSERT: Error log emitted"""
        self.trace.duration = 0.5
        with unittest.mock.patch.object(self.logger._log, 'error') as mock_error:
            self.logger.log_error(self.trace, ConnectionError("refused"), 0.5)
            mock_error.assert_called()


if __name__ == "__main__":
    unittest.main()
