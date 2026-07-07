"""Unit tests for the server module.

Validates all proxy endpoints the end user interacts with:
- Static file serving (index.html, CSS, JS)
- Proxy GET (list models via OpenAI compat, LM Studio native)
- Proxy POST (load model, unload model, chat)
- Proxy streaming (SSE chat completions)
- CORS headers
- Error handling (connect errors, 404, 405)
- Auth header forwarding
"""

import unittest
import os
import sys
import json
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import httpx

from backend import server


# --- Mock response builders ---

def _make_httpx_response(status_code=200, json_data=None, text=None, headers=None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = json.dumps(json_data).encode() if json_data else (text.encode() if text else b"")
    resp.text = json.dumps(json_data) if json_data else (text or "")

    resp_headers = {"Content-Type": "application/json"}
    if headers:
        resp_headers.update(headers)
    resp.headers = MagicMock()
    resp.headers.get = lambda key, default=None: resp_headers.get(key, default)
    resp.json = MagicMock(return_value=json_data if json_data else {})
    resp.request = MagicMock()
    return resp


def _openai_models_response():
    """Build a mock /v1/models (OpenAI compat) response."""
    return _make_httpx_response(200, {
        "object": "list",
        "data": [
            {"id": "llama-3.1-8b", "object": "model", "owned_by": "organization_owner"},
            {"id": "mistral-7b", "object": "model", "owned_by": "organization_owner"},
        ],
    })


def _load_model_response(instance_id="inst-test", load_time=2.5):
    """Build a mock /api/v1/models/load response."""
    return _make_httpx_response(200, {
        "instance_id": instance_id,
        "load_time_seconds": load_time,
        "status": "loaded",
        "load_config": {"model_path": "/path/to/model"},
    })


def _unload_model_response(instance_id="inst-test"):
    """Build a mock /api/v1/models/unload response."""
    return _make_httpx_response(200, {"instance_id": instance_id})


def _chat_response(content="Hello! How can I help you?"):
    """Build a mock /v1/chat/completions response."""
    return _make_httpx_response(200, {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        "system_fingerprint": "fp-test",
    })


# --- Test classes ---


class TestStaticServing(unittest.TestCase):
    """Verify static files are served correctly."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_serves_index_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("LM Studio Console", resp.text)

    def test_serves_css(self):
        resp = self.client.get("/static/css/style.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("--accent", resp.text)

    def test_serves_js(self):
        resp = self.client.get("/static/js/app.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("state", resp.text)

    def test_serves_api_js(self):
        resp = self.client.get("/static/js/api.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("apiCall", resp.text)

    def test_serves_chat_js(self):
        resp = self.client.get("/static/js/chat.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sendMessage", resp.text)
        # Verify metrics tracking is present
        self.assertIn("tokensPerSecond", resp.text)


class TestProxyGet(unittest.TestCase):
    """Verify GET proxy endpoints."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_proxy_openai_models(self, mock_proxy):
        """Proxy GET /v1/models returns OpenAI-compatible list."""
        mock_proxy.return_value = _openai_models_response()

        resp = self.client.get("/proxy/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["object"], "list")
        self.assertGreater(len(data["data"]), 0)
        self.assertEqual(data["data"][0]["object"], "model")
        self.assertEqual(data["data"][0]["owned_by"], "organization_owner")

    @patch("backend.server.proxy_request")
    def test_proxy_lm_studio_models(self, mock_proxy):
        """Proxy GET /api/v1/models returns LM Studio native format."""
        mock_proxy.return_value = _make_httpx_response(200, {
            "models": [
                {"key": "llama-3.1-8b", "type": "llm", "display_name": "Llama 3.1 8B"},
            ],
        })

        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        self.assertGreater(len(data["models"]), 0)

    @patch("backend.server.proxy_request")
    def test_proxy_404_passthrough(self, mock_proxy):
        """Proxy passes through 404 from upstream."""
        mock_proxy.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(),
            response=_make_httpx_response(404, {"error": "not found"})
        )

        resp = self.client.get("/proxy/api/v1/nonexistent")
        self.assertEqual(resp.status_code, 404)

    @patch("backend.server.proxy_request")
    def test_proxy_connect_error(self, mock_proxy):
        """Proxy returns 502 when upstream is unreachable."""
        mock_proxy.side_effect = httpx.ConnectError("Connection refused")

        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 502)
        data = resp.json()
        self.assertIn("error", data)
        self.assertIn("message", data)


class TestProxyPost(unittest.TestCase):
    """Verify POST proxy endpoints."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_proxy_load_model(self, mock_proxy):
        """Proxy POST /api/v1/models/load loads a model."""
        mock_proxy.return_value = _load_model_response("inst-test", 2.5)

        resp = self.client.post("/proxy/api/v1/models/load", json={"model": "llama-3.1-8b", "echo_load_config": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("instance_id", data)
        self.assertIn("load_time_seconds", data)
        self.assertEqual(data["status"], "loaded")
        self.assertIn("load_config", data)

    @patch("backend.server.proxy_request")
    def test_proxy_unload_model(self, mock_proxy):
        """Proxy POST /api/v1/models/unload unloads a model."""
        mock_proxy.return_value = _unload_model_response("inst-test")

        resp = self.client.post("/proxy/api/v1/models/unload", json={"instance_id": "inst-test"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["instance_id"], "inst-test")

    @patch("backend.server.proxy_request")
    def test_proxy_load_nonexistent(self, mock_proxy):
        """Proxy passes through 404 for non-existent model."""
        mock_proxy.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(),
            response=_make_httpx_response(404, {"error": "model not found"})
        )

        resp = self.client.post("/proxy/api/v1/models/load", json={"model": "nonexistent-xyz"})
        self.assertEqual(resp.status_code, 404)

    @patch("backend.server.proxy_request")
    def test_proxy_chat_nonstreaming(self, mock_proxy):
        """Proxy POST /v1/chat/completions without stream returns JSON."""
        mock_proxy.return_value = _chat_response("Hello! How can I help you?")

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Say hi"}],
            "temperature": 0.5,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["choices"][0]["message"]["role"], "assistant")
        self.assertIn("content", data["choices"][0]["message"])
        self.assertIn("usage", data)
        self.assertIn("system_fingerprint", data)

    @patch("backend.server.proxy_request")
    def test_proxy_chat_with_stream_false(self, mock_proxy):
        """Chat with stream: false uses buffered response."""
        mock_proxy.return_value = _chat_response("Hi there!")

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["choices"][0]["message"]["content"], "Hi there!")

    @patch("backend.server.proxy_stream_iter")
    def test_proxy_chat_streaming(self, mock_stream):
        """Chat with stream: true returns SSE streaming response."""
        from fastapi.responses import StreamingResponse

        sse_data = b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"}}]}\n'
        sse_done = b"data: [DONE]\n"

        async def mock_iter():
            yield sse_data
            yield sse_done

        mock_stream.return_value = mock_iter()

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Say hi"}],
            "stream": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))

    @patch("backend.server.proxy_request")
    def test_auth_header_forwarding(self, mock_proxy):
        """Authorization header is forwarded to upstream."""
        mock_proxy.return_value = _openai_models_response()

        resp = self.client.get("/proxy/v1/models", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(resp.status_code, 200)

        # Verify proxy_request was called with the auth header
        call_kwargs = mock_proxy.call_args
        self.assertIn("headers", call_kwargs.kwargs)
        self.assertEqual(call_kwargs.kwargs["headers"]["Authorization"], "Bearer test-token")


class TestCORS(unittest.TestCase):
    """Verify CORS headers are set correctly."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_cors_on_proxy_get(self, mock_proxy):
        mock_proxy.return_value = _openai_models_response()
        resp = self.client.get("/proxy/v1/models", headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    @patch("backend.server.proxy_request")
    def test_cors_on_proxy_post(self, mock_proxy):
        mock_proxy.return_value = _load_model_response()
        resp = self.client.post("/proxy/api/v1/models/load", json={"model": "test"},
                                 headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_cors_on_static(self):
        resp = self.client.get("/", headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    @patch("backend.server.proxy_request")
    def test_options_preflight(self, mock_proxy):
        resp = self.client.options("/proxy/v1/models",
                                    headers={"Origin": "http://example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")


class TestMethodNotAllowed(unittest.TestCase):
    """Verify requests with wrong method are rejected."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_post_to_get_only_path(self):
        """POST to a GET-only path returns 405."""
        resp = self.client.post("/", json={"test": "data"})
        self.assertEqual(resp.status_code, 405)

    def test_get_to_nonexistent_path(self):
        """GET to a non-existent path returns 404."""
        resp = self.client.get("/nonexistent")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
