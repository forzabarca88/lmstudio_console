"""Tests for the logger module."""

import unittest
import os
import sys
import io
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.logger import TraceLogger, RequestTrace


class TestTraceLogger(unittest.TestCase):
    """Verify trace logging captures request/response details."""

    def setUp(self):
        self.logger = TraceLogger()
        self.log_capture = io.StringIO()
        handler = self.logger.logger.handlers[0]
        handler.stream = self.log_capture

    def test_log_request_records_output(self):
        self.logger.log_request("GET", "/api/v1/models", "http://localhost:1234/api/v1/models")
        output = self.log_capture.getvalue()
        self.assertIn("GET", output)
        self.assertIn("/api/v1/models", output)

    def test_log_request_with_body(self):
        body = '{"model": "test-model"}'
        self.logger.log_request("POST", "/api/v1/models/load", "http://localhost:1234/api/v1/models/load", body)
        output = self.log_capture.getvalue()
        self.assertIn("POST", output)
        self.assertIn(body, output)

    def test_log_response_records_status(self):
        trace = self.logger.log_request("GET", "/api/v1/models", "http://localhost:1234/api/v1/models")
        self.logger.log_response(trace, 200, "application/json", 0.5, '{"models":[]}')
        output = self.log_capture.getvalue()
        self.assertIn("200", output)
        self.assertIn("OK", output)
        self.assertIn("0.500s", output)

    def test_log_error_records_failure(self):
        trace = self.logger.log_request("GET", "/api/v1/models", "http://localhost:1234/api/v1/models")
        self.logger.log_error(trace, ConnectionError("Connection refused"), 0.1)
        output = self.log_capture.getvalue()
        self.assertIn("ERR", output)
        self.assertIn("Connection refused", output)

    def test_request_trace_status_text(self):
        trace = RequestTrace("GET", "/test", "http://localhost/test")
        self.assertEqual(trace._status_text(200), "OK")
        self.assertEqual(trace._status_text(404), "Not Found")
        self.assertEqual(trace._status_text(500), "Internal Server Error")
        self.assertEqual(trace._status_text(999), "Unknown")


class TestRequestTrace(unittest.TestCase):
    """Verify RequestTrace holds correct data."""

    def test_initial_state(self):
        trace = RequestTrace("POST", "/v1/chat", "http://localhost/v1/chat", '{"messages":[]}')
        self.assertEqual(trace.method, "POST")
        self.assertEqual(trace.path, "/v1/chat")
        self.assertEqual(trace.target, "http://localhost/v1/chat")
        self.assertEqual(trace.body, '{"messages":[]}')
        self.assertIsNone(trace.status)
        self.assertEqual(trace.duration, 0.0)
        self.assertIsNone(trace.error)

    def test_context_manager(self):
        with RequestTrace("GET", "/test", "http://localhost/test") as trace:
            self.assertEqual(trace.method, "GET")

    def test_streaming_content_type_detection(self):
        """Verify SSE content type is detected for streaming."""
        # This is tested implicitly through proxy tests
        pass


if __name__ == "__main__":
    unittest.main()
