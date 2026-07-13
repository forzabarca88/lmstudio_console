"""Backend module tests.

Tests configuration, logging, and proxy functionality.
"""

import unittest
import os
import sys

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
            method="GET", path="/api/v1/models",
            target_url="http://localhost:1234/api/v1/models",
            start_time=1000.0,
        )

    def test_trace_fields(self):
        """ARRANGE: RequestTrace created
        ACT: Check attributes
        ASSERT: All fields set correctly"""
        self.assertEqual(self.trace.method, "GET")
        self.trace.end_time = 1500.0
        self.trace.status_code = 200
        self.assertEqual(self.trace.duration, 0.5)

    def test_status_text(self):
        """ARRANGE: RequestTrace with status codes
        ACT: Get status text
        ASSERT: Correct label"""
        self.trace.status_code = 200
        self.assertEqual(self.trace._status_text(), "OK")
        self.trace.status_code = 404
        self.assertEqual(self.trace._status_text(), "Not Found")
        self.trace.status_code = 500
        self.assertEqual(self.trace._status_text(), "Server Error")

    def test_request_logged(self):
        """ARRANGE: TraceLogger created
        ACT: Log request
        ASSERT: Debug log emitted"""
        with unittest.mock.patch.object(self.logger._logger, 'debug') as mock_debug:
            self.logger.log_request(self.trace)
            mock_debug.assert_called_once()

    def test_response_logged(self):
        """ARRANGE: TraceLogger created
        ACT: Log response
        ASSERT: Debug log emitted"""
        self.trace.end_time = 1500.0
        self.trace.status_code = 200
        with unittest.mock.patch.object(self.logger._logger, 'debug') as mock_debug:
            self.logger.log_response(self.trace)
            mock_debug.assert_called_once()

    def test_error_logged(self):
        """ARRANGE: TraceLogger created
        ACT: Log error
        ASSERT: Error log emitted"""
        self.trace.end_time = 1100.0
        self.trace.error = "Connection refused"
        with unittest.mock.patch.object(self.logger._logger, 'error') as mock_error:
            self.logger.log_error(self.trace)
            mock_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
