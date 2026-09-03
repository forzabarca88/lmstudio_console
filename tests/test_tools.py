"""Tools module tests.

Tests tool execution directly (isolated-subprocess ``run_python_code``,
SSRF-safe capped ``open_web_page``, threaded ``web_search``) and the
agent toolset wiring.
"""

import asyncio
import os
import socket
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx

from backend.tools import open_web_page, run_python_code, web_search

# Global (publicly routable) IP used to mock DNS for hostname tests so
# validate_public_url passes without any real network access.
_GLOBAL_IP = "93.184.216.34"


def _fake_getaddrinfo(host, port=None, *args, **kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_GLOBAL_IP, 0))
    ]


class TestRunPythonCode(unittest.TestCase):
    """run_python_code executes code in a disposable isolated subprocess."""

    def _run(self, code):
        return asyncio.run(run_python_code(code))

    def test_print_output(self):
        """ARRANGE: simple print code
        ACT: Execute
        ASSERT: stdout captured"""
        result = self._run("print('hello world')")
        self.assertIsInstance(result, str)
        self.assertIn("hello world", result)

    def test_stderr_captured(self):
        """ARRANGE: code that raises an exception
        ACT: Execute
        ASSERT: traceback captured in the Stderr section"""
        result = self._run("raise ValueError('boom')")
        self.assertIsInstance(result, str)
        self.assertIn("Stderr:", result)
        self.assertIn("boom", result)

    def test_import_allowed(self):
        """ARRANGE: code importing a stdlib module
        ACT: Execute
        ASSERT: succeeds (no blocklist)"""
        result = self._run("import os; print(os.name)")
        self.assertIsInstance(result, str)
        self.assertNotIn("Error", result)
        self.assertIn(os.name, result)

    def test_timeout(self):
        """ARRANGE: code that sleeps past the 10s timeout
        ACT: Execute, measuring wall clock
        ASSERT: timeout error reported in under 13s"""
        start = time.monotonic()
        result = self._run("import time; time.sleep(15)")
        elapsed = time.monotonic() - start
        self.assertIn("Error", result)
        self.assertIn("timeout", result)
        self.assertLess(elapsed, 13)

    def test_too_large(self):
        """ARRANGE: code over the 10KB limit
        ACT: Execute
        ASSERT: error mentions the size limit"""
        result = self._run("x = '" + "a" * 11_000 + "'")
        self.assertIn("exceeds", result)

    def test_output_truncated_at_cap(self):
        """ARRANGE: code that prints without bound (infinite)
        ACT: Execute, measuring wall clock
        ASSERT: truncation marker present and the cap killed the process
        group well before the 10s timeout window"""
        start = time.monotonic()
        result = self._run("while True:\n    print('x' * 10_000)")
        elapsed = time.monotonic() - start
        self.assertIn("[output truncated: exceeded 10MB]", result)
        self.assertLess(elapsed, 9)


class _AsyncCM:
    """Minimal async context manager for mocking httpx context managers."""

    def __init__(self, value, enter_error=None):
        self._value = value
        self._enter_error = enter_error
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        if self._enter_error is not None:
            raise self._enter_error
        return self._value

    async def __aexit__(self, *exc):
        return False


def _mock_client(chunks, content_type="text/html; charset=utf-8", enter_error=None):
    """Build a mock ``httpx.AsyncClient``.

    ``stream()`` returns an async context manager whose response exposes
    ``raise_for_status``, ``headers.get`` and an ``aiter_bytes()``
    iterator over *chunks*.
    """
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.headers = {"content-type": content_type}

    async def _aiter_bytes():
        for c in chunks:
            yield c

    resp.aiter_bytes = _aiter_bytes

    client = MagicMock()
    client.stream = MagicMock(
        return_value=_AsyncCM(resp, enter_error=enter_error)
    )
    return _AsyncCM(client)


def _mock_redirect_client(responses):
    """Build a mock ``httpx.AsyncClient`` whose ``stream()`` returns the
    given response async context managers in call order (redirect walk).

    Returns ``(client_cm, client_mock)`` so callers can inspect
    ``client_mock.stream.call_count``.
    """
    client = MagicMock()
    client.stream = MagicMock(side_effect=[_AsyncCM(r) for r in responses])
    return _AsyncCM(client), client


class TestOpenWebPage(unittest.TestCase):
    """open_web_page validates the URL, caps the body, and degrades gracefully."""

    def _fetch(self, url, client_cm):
        async def _run():
            with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
                 patch("backend.tools.httpx.AsyncClient", return_value=client_cm):
                return await open_web_page(url)
        return asyncio.run(_run())

    def test_html_body(self):
        """ARRANGE: mock client returning an HTML page
        ACT: Fetch
        ASSERT: readable text extracted"""
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>".encode()
        cm = _mock_client([html])
        result = self._fetch("https://example.com", cm)
        self.assertIn("Hello", result)
        self.assertIn("World", result)

    def test_plain_text_passthrough(self):
        """ARRANGE: mock client returning text/plain
        ACT: Fetch
        ASSERT: raw body passed through"""
        body = b"just some plain text"
        cm = _mock_client([body], content_type="text/plain")
        result = self._fetch("https://example.com/plain.txt", cm)
        self.assertIn("just some plain text", result)

    def test_truncated(self):
        """ARRANGE: single 6MB chunk
        ACT: Fetch
        ASSERT: result capped and marked truncated"""
        big = b"a" * (6 * 1024 * 1024)
        cm = _mock_client([big], content_type="text/plain")
        result = self._fetch("https://example.com/big", cm)
        self.assertLess(len(result), 100_000)
        self.assertIn("truncated", result)

    def test_blocked_loopback(self):
        """ARRANGE: loopback URL
        ACT: Fetch
        ASSERT: error returned and no network client used"""
        cm = _mock_client([b""])
        result = self._fetch("http://127.0.0.1/x", cm)
        self.assertTrue(result.startswith("Error"))
        self.assertFalse(cm.entered)

    def test_blocked_metadata(self):
        """ARRANGE: cloud metadata IP URL
        ACT: Fetch
        ASSERT: error returned and no network client used"""
        cm = _mock_client([b""])
        result = self._fetch("http://169.254.169.254/latest/meta-data/", cm)
        self.assertTrue(result.startswith("Error"))
        self.assertFalse(cm.entered)

    def test_blocked_file_scheme(self):
        """ARRANGE: non-http(s) scheme
        ACT: Fetch
        ASSERT: error returned and no network client used"""
        cm = _mock_client([b""])
        result = self._fetch("file:///etc/passwd", cm)
        self.assertTrue(result.startswith("Error"))
        self.assertFalse(cm.entered)

    def test_connect_error(self):
        """ARRANGE: mock client whose stream raises httpx.ConnectError
        ACT: Fetch
        ASSERT: graceful error message"""
        cm = _mock_client([], enter_error=httpx.ConnectError("Connection refused"))
        result = self._fetch("https://bad-domain.invalid", cm)
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Error"))

    def test_redirect_to_loopback_blocked(self):
        """ARRANGE: first stream() returns a 302 to http://127.0.0.1
        ACT: Fetch
        ASSERT: error returned and the redirect was NOT followed
        (stream entered exactly once)"""
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://127.0.0.1/internal"}
        cm, client = _mock_redirect_client([redirect])
        result = self._fetch("https://example.com", cm)
        self.assertTrue(result.startswith("Error"))
        self.assertEqual(client.stream.call_count, 1)

    def test_redirect_to_global_followed(self):
        """ARRANGE: 302 to a global IP; second stream() returns a 200 body
        ACT: Fetch
        ASSERT: final body content returned after the re-validated redirect
        (stream entered twice)"""
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://93.184.216.34/"}
        final = MagicMock()
        final.status_code = 200
        final.headers = {"content-type": "text/plain"}
        final.raise_for_status = MagicMock()

        async def _aiter_bytes():
            yield b"redirected body"

        final.aiter_bytes = _aiter_bytes
        cm, client = _mock_redirect_client([redirect, final])
        result = self._fetch("https://example.com/first", cm)
        self.assertIn("redirected body", result)
        self.assertEqual(client.stream.call_count, 2)


class TestWebSearch(unittest.TestCase):
    """web_search performs a DDGS search off the event loop."""

    def test_live_search(self):
        """ARRANGE: network available
        ACT: Search for "Python"
        ASSERT: non-empty string returned"""
        result = asyncio.run(web_search("Python"))
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class TestAgentToolsetWiring(unittest.TestCase):
    """The shared _agent exposes exactly the three registered tools."""

    def test_toolset(self):
        """ARRANGE: agent module imported
        ACT: Inspect agent._agent.toolsets
        ASSERT: one toolset with exactly the three tool names"""
        from backend import agent

        toolsets = agent._agent.toolsets
        self.assertEqual(len(toolsets), 1)
        names = set(toolsets[0].tools.keys())
        self.assertEqual(names, {"web_search", "open_web_page", "run_python_code"})


if __name__ == "__main__":
    unittest.main()
