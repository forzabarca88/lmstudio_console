"""Backend module tests.

Tests configuration, logging, and proxy functionality.
"""

import asyncio
import time
import unittest
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx

from backend import config
from backend import proxy
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


class TestProxyStreamTimeout(unittest.TestCase):
    """Proxied upstream streams are bounded by an idle (read) timeout.

    Uses a real local HTTP server on a loopback socket (no mocks of the
    httpx timeout machinery) so the per-read timeout is genuinely enforced
    by httpx, exactly as it is in production.
    """

    async def _start_upstream(self, handler):
        """Start a local asyncio HTTP server; return (server, base_url)."""
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return server, f"http://127.0.0.1:{port}"

    def test_stalled_upstream_yields_timeout_error_line(self):
        """ARRANGE: Upstream sends response headers then never sends body
                   bytes; stream read timeout patched down to 1s
        ACT: Consume proxy_stream_iter with the same error handling that
             server.py's SSE consumer applies
        ASSERT: A single data: line naming the timeout type is emitted, and
               the stream terminates cleanly (bounded by the timeout, not
               the stall)"""

        async def stall_handler(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")  # consume request headers
                writer.write(b"HTTP/1.1 200 OK\r\n"
                             b"Content-Type: text/event-stream\r\n"
                             b"Connection: close\r\n\r\n")
                await writer.drain()
                await asyncio.sleep(3600)  # stall: never send a body byte
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass
            finally:
                writer.close()

        async def scenario():
            server, url = await self._start_upstream(stall_handler)
            try:
                with unittest.mock.patch(
                        "backend.proxy._STREAM_READ_TIMEOUT",
                        httpx.Timeout(1.0, connect=1.0)):
                    start = time.monotonic()
                    lines = []
                    try:
                        async for chunk in proxy.proxy_stream_iter(
                                "POST", "/v1/chat/completions", body={},
                                headers=None, target_url=url):
                            lines.append(
                                chunk.decode("utf-8", errors="replace"))
                    except (httpx.ConnectError, httpx.TimeoutException) as e:
                        # proxy_stream_iter re-raises; server.py's
                        # _proxy_stream_response surfaces it as an SSE line.
                        lines.append(f"data: {type(e).__name__}: {e}\n\n")
                    return lines, time.monotonic() - start
            finally:
                server.close()
                await proxy.close_client()

        lines, elapsed = asyncio.run(scenario())

        self.assertEqual(len(lines), 1, f"expected one error line: {lines}")
        self.assertTrue(lines[0].startswith("data: "), lines[0])
        self.assertIn("ReadTimeout", lines[0])
        # Terminated on the patched 1s read timeout, not on the 1h stall.
        self.assertLess(elapsed, 10.0)

    def test_fast_stream_yields_chunks_unchanged(self):
        """ARRANGE: Upstream streams three chunks 50ms apart (well under
                   the idle timeout); stream read timeout patched down to 1s
        ACT: Consume proxy_stream_iter
        ASSERT: All bytes are yielded unchanged with no error line (the
               timeout is per-idle between chunks, not a total budget)"""

        async def fast_handler(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")  # consume request headers
                writer.write(b"HTTP/1.1 200 OK\r\n"
                             b"Content-Type: text/event-stream\r\n"
                             b"Connection: close\r\n\r\n")
                await writer.drain()
                for i in range(3):
                    writer.write(f"data: chunk{i}\n\n".encode())
                    await writer.drain()
                    await asyncio.sleep(0.05)
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass
            finally:
                writer.close()

        async def scenario():
            server, url = await self._start_upstream(fast_handler)
            try:
                with unittest.mock.patch(
                        "backend.proxy._STREAM_READ_TIMEOUT",
                        httpx.Timeout(1.0, connect=1.0)):
                    chunks = []
                    async for chunk in proxy.proxy_stream_iter(
                            "POST", "/v1/chat/completions", body={},
                            headers=None, target_url=url):
                        chunks.append(chunk)
                    return chunks
            finally:
                server.close()
                await proxy.close_client()

        chunks = asyncio.run(scenario())

        self.assertEqual(
            b"".join(chunks),
            b"data: chunk0\n\ndata: chunk1\n\ndata: chunk2\n\n")


if __name__ == "__main__":
    unittest.main()
