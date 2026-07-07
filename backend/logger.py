"""Trace logging for proxy requests."""

import logging
import time
from typing import Optional


class TraceLogger:
    """Logs detailed trace information for proxied API requests."""

    def __init__(self):
        self._log = logging.getLogger("lmstudio_console")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        ))
        self._log.addHandler(handler)
        self._log.setLevel(logging.DEBUG)

    @property
    def logger(self):
        """Expose the underlying logger for testing."""
        return self._log

    def _error_msg(self, error: Exception) -> str:
        """Format an error for logging, using type name when message is empty."""
        msg = str(error)
        if not msg:
            return type(error).__name__
        return msg

    def log_request(self, method: str, path: str, target_url: str, body: Optional[str] = None) -> "RequestTrace":
        """Start tracing a request. Returns a trace context."""
        trace = RequestTrace(method, path, target_url, body)
        self._log.debug(f"OUT {method:6s} {path} -> {target_url}")
        if body:
            self._log.debug(f"     body: {body[:200]}{'...' if len(body) > 200 else ''}")
        return trace

    def log_response(self, trace: "RequestTrace", status: int, content_type: str, duration: float, body: Optional[str] = None) -> None:
        """Complete tracing a request with its response."""
        trace.duration = duration
        trace.status = status
        status_label = f"{status} {trace._status_text(status)}"
        self._log.debug(f"IN  {status_label:12s} {trace.path} ({duration:.3f}s)")
        if body:
            self._log.debug(f"     body: {body[:200]}{'...' if len(body) > 200 else ''}")

    def log_error(self, trace: "RequestTrace", error: Exception, duration: float) -> None:
        """Log a failed request."""
        trace.duration = duration
        trace.error = self._error_msg(error)
        self._log.error(f"ERR {trace.method:6s} {trace.path} -> {trace.target} ({duration:.3f}s): {self._error_msg(error)}")

    def log_server_error(self, method: str, path: str, error: Exception) -> None:
        """Log a server-side error (e.g. response handling failure)."""
        self._log.error(f"ERR {method:6s} {path}: {self._error_msg(error)}")


class RequestTrace:
    """Holds trace data for a single proxied request."""

    def __init__(self, method: str, path: str, target_url: str, body: Optional[str] = None):
        self.method = method
        self.path = path
        self.target = target_url
        self.body = body
        self.status: Optional[int] = None
        self.duration: float = 0.0
        self.error: Optional[str] = None
        self.started_at: float = time.monotonic()

    def _status_text(self, status: int) -> str:
        """Return HTTP reason phrase."""
        phrases = {
            200: "OK", 201: "Created", 204: "No Content",
            301: "Moved", 302: "Found",
            400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
            422: "Unprocessable", 429: "Too Many Requests",
            500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
        }
        return phrases.get(status, "Unknown")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
