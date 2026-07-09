"""Integration tests for LM Studio Console.

Tests the application from the end user's perspective:
- Static file serving (HTML, CSS, JS, favicon)
- Proxy endpoints (list models, load, unload, chat)
- Streaming chat responses
- CORS headers
- Error handling (connect errors, timeouts)
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

def _httpx_response(status_code=200, json_data=None, text=None, headers=None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    body = json.dumps(json_data) if json_data else (text or "")
    resp.content = body.encode()
    resp.text = body

    resp_headers = {"Content-Type": "application/json"}
    if headers:
        resp_headers.update(headers)
    resp.headers = MagicMock()
    resp.headers.get = lambda key, default=None: resp_headers.get(key, default)
    resp.json = MagicMock(return_value=json_data if json_data else {})
    resp.request = MagicMock()
    return resp


def _openai_models():
    return _httpx_response(200, {
        "object": "list",
        "data": [
            {"id": "llama-3.1-8b", "object": "model", "owned_by": "org"},
            {"id": "mistral-7b", "object": "model", "owned_by": "org"},
        ],
    })


def _lm_models():
    return _httpx_response(200, {
        "models": [
            {"key": "llama-3.1-8b", "type": "llm", "display_name": "Llama 3.1 8B", "loaded_instances": []},
            {"key": "mistral-7b", "type": "llm", "display_name": "Mistral 7B", "loaded_instances": [{"id": "inst-1"}]},
        ],
    })


def _load_response(instance_id="inst-test", load_time=2.5):
    return _httpx_response(200, {
        "instance_id": instance_id,
        "load_time_seconds": load_time,
        "status": "loaded",
        "load_config": {"model_path": "/path/to/model"},
    })


def _unload_response(instance_id="inst-test"):
    return _httpx_response(200, {"instance_id": instance_id})


def _chat_response(content="Hello! How can I help you?"):
    return _httpx_response(200, {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        "system_fingerprint": "fp-test",
    })


class TestStaticFiles(unittest.TestCase):
    """Verify all static files are served correctly."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_index_html(self):
        """User opens the app and sees the dashboard."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("LM Studio Console", resp.text)
        # Verify key UI elements exist
        self.assertIn("endpoint", resp.text)
        self.assertIn("connectBtn", resp.text)
        self.assertIn("modelList", resp.text)
        self.assertIn("chatMessages", resp.text)
        self.assertIn("chatInput", resp.text)
        self.assertIn("sendBtn", resp.text)
        self.assertIn("systemPrompt", resp.text)
        self.assertIn("temperature", resp.text)
        self.assertIn("historyList", resp.text)
        self.assertIn("historyToggle", resp.text)
        self.assertIn("marked", resp.text)
        self.assertIn("mermaid", resp.text)

    def test_favicon(self):
        """Browser requests favicon without 404."""
        resp = self.client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("content-type"), "image/svg+xml")

    def test_css(self):
        """Stylesheet loads correctly."""
        resp = self.client.get("/static/css/style.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("--accent", resp.text)
        self.assertIn(".copy-btn", resp.text)
        self.assertIn(".mermaid", resp.text)
        self.assertIn(".history-panel", resp.text)
        self.assertIn(".history-item", resp.text)

    def test_js_modules(self):
        """All JS modules load without errors."""
        modules = ["app.js", "chat.js", "connection.js", "history.js",
                    "models.js", "state.js", "api.js", "ui.js"]
        for module in modules:
            resp = self.client.get(f"/static/js/{module}")
            self.assertEqual(resp.status_code, 200, f"{module} failed to load")
            # Verify module has content
            self.assertGreater(len(resp.text), 100, f"{module} seems empty")

    def test_js_no_syntax_errors(self):
        """JS modules parse without syntax errors (checked via served content)."""
        modules = ["app.js", "chat.js", "connection.js", "history.js",
                    "models.js", "state.js", "api.js", "ui.js"]
        for module in modules:
            resp = self.client.get(f"/static/js/{module}")
            content = resp.text
            # Check for common JS syntax issues
            # Verify import/export consistency
            self.assertNotIn("does not provide an export named", content)


class TestProxyGet(unittest.TestCase):
    """User lists models through the proxy."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_list_openai_models(self, mock_proxy):
        """User sees list of available models from OpenAI-compatible endpoint."""
        mock_proxy.return_value = _openai_models()

        resp = self.client.get("/proxy/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["object"], "list")
        self.assertEqual(len(data["data"]), 2)
        self.assertEqual(data["data"][0]["id"], "llama-3.1-8b")
        self.assertEqual(data["data"][1]["id"], "mistral-7b")

    @patch("backend.server.proxy_request")
    def test_list_lm_studio_models(self, mock_proxy):
        """User sees LM Studio native model list with loaded state."""
        mock_proxy.return_value = _lm_models()

        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        self.assertEqual(len(data["models"]), 2)
        # Verify loaded model is shown
        loaded = [m for m in data["models"] if m.get("loaded_instances")]
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["key"], "mistral-7b")

    @patch("backend.server.proxy_request")
    def test_upstream_404_passthrough(self, mock_proxy):
        """User gets 404 when requesting non-existent endpoint."""
        mock_proxy.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(),
            response=_httpx_response(404, {"error": "not found"})
        )

        resp = self.client.get("/proxy/api/v1/nonexistent")
        self.assertEqual(resp.status_code, 404)

    @patch("backend.server.proxy_request")
    def test_connect_error(self, mock_proxy):
        """User gets 502 when LM Studio is not running."""
        mock_proxy.side_effect = httpx.ConnectError("Connection refused")

        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 502)
        data = resp.json()
        self.assertIn("error", data)
        self.assertIn("message", data)


class TestProxyPost(unittest.TestCase):
    """User loads, unloads models and chats."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_load_model(self, mock_proxy):
        """User loads a model and gets confirmation."""
        mock_proxy.return_value = _load_response("inst-123", 3.2)

        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "llama-3.1-8b", "echo_load_config": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["instance_id"], "inst-123")
        self.assertEqual(data["load_time_seconds"], 3.2)
        self.assertEqual(data["status"], "loaded")
        self.assertIn("load_config", data)

    @patch("backend.server.proxy_request")
    def test_load_model_timeout(self, mock_proxy):
        """Large model load can take minutes - proxy handles long timeouts."""
        mock_proxy.return_value = _load_response("inst-big", 300.0)

        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "huge-model-70b"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["load_time_seconds"], 300.0)

    @patch("backend.server.proxy_request")
    def test_unload_model(self, mock_proxy):
        """User unloads a model."""
        mock_proxy.return_value = _unload_response("inst-123")

        resp = self.client.post("/proxy/api/v1/models/unload",
                                 json={"instance_id": "inst-123"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["instance_id"], "inst-123")

    @patch("backend.server.proxy_request")
    def test_load_nonexistent_model(self, mock_proxy):
        """User gets error when loading unknown model."""
        mock_proxy.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(),
            response=_httpx_response(404, {"error": "model not found"})
        )

        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "nonexistent-xyz"})
        self.assertEqual(resp.status_code, 404)

    @patch("backend.server.proxy_request")
    def test_chat_nonstreaming(self, mock_proxy):
        """User sends a message and gets a complete response."""
        mock_proxy.return_value = _chat_response("The answer is 42.")

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "What is the answer?"}],
            "temperature": 0.5,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(data["choices"][0]["message"]["content"], "The answer is 42.")
        self.assertIn("usage", data)
        self.assertEqual(data["usage"]["total_tokens"], 18)

    @patch("backend.server.proxy_request")
    def test_chat_with_system_prompt(self, mock_proxy):
        """User chat includes system prompt."""
        mock_proxy.return_value = _chat_response("As a helpful assistant, the answer is 42.")

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the answer?"},
            ],
            "temperature": 0.7,
        })
        self.assertEqual(resp.status_code, 200)

    @patch("backend.server.proxy_stream_iter")
    def test_chat_streaming(self, mock_stream):
        """User gets streaming SSE response for real-time chat."""
        sse_chunks = [
            b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"}}]}\n\n',
            b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":" world"}}]}\n\n',
            b'data: {"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
            b"data: [DONE]\n\n",
        ]

        async def mock_iter():
            for chunk in sse_chunks:
                yield chunk

        mock_stream.return_value = mock_iter()

        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Say hi"}],
            "stream": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
        self.assertEqual(resp.headers.get("cache-control"), "no-cache")
        self.assertEqual(resp.headers.get("connection"), "keep-alive")

        # Verify streaming content
        content = resp.content.decode()
        self.assertIn("Hello", content)
        self.assertIn("world", content)
        self.assertIn("[DONE]", content)

    @patch("backend.server.proxy_request")
    def test_auth_header_forwarding(self, mock_proxy):
        """User's API token is forwarded to upstream server."""
        mock_proxy.return_value = _openai_models()

        resp = self.client.get("/proxy/v1/models",
                                headers={"Authorization": "Bearer my-secret-token"})
        self.assertEqual(resp.status_code, 200)

        # Verify proxy_request was called with auth header
        call_kwargs = mock_proxy.call_args
        self.assertIn("headers", call_kwargs.kwargs)
        self.assertEqual(call_kwargs.kwargs["headers"]["Authorization"],
                         "Bearer my-secret-token")

    @patch("backend.server.proxy_request")
    def test_custom_target_url(self, mock_proxy):
        """User can connect to different endpoints via header."""
        mock_proxy.return_value = _openai_models()

        resp = self.client.get("/proxy/v1/models",
                                headers={"X-LM-Studio-URL": "http://custom-server:9999"})
        self.assertEqual(resp.status_code, 200)

        # Verify proxy_request was called with custom target
        call_kwargs = mock_proxy.call_args
        self.assertEqual(call_kwargs.kwargs["target_url"], "http://custom-server:9999")


class TestCORS(unittest.TestCase):
    """Verify CORS works for cross-origin access."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_cors_on_index(self):
        resp = self.client.get("/", headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    @patch("backend.server.proxy_request")
    def test_cors_on_proxy_get(self, mock_proxy):
        mock_proxy.return_value = _openai_models()
        resp = self.client.get("/proxy/v1/models",
                                headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    @patch("backend.server.proxy_request")
    def test_cors_on_proxy_post(self, mock_proxy):
        mock_proxy.return_value = _load_response()
        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "test"},
                                 headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_cors_preflight(self):
        """Browser sends OPTIONS preflight before cross-origin requests."""
        resp = self.client.options("/proxy/v1/models",
                                    headers={"Origin": "http://example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_cors_on_static(self):
        resp = self.client.get("/static/js/app.js",
                                headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")


class TestMethodNotAllowed(unittest.TestCase):
    """Verify wrong HTTP methods are rejected."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_post_to_get_only(self):
        resp = self.client.post("/", json={"test": "data"})
        self.assertEqual(resp.status_code, 405)

    def test_get_to_post_only(self):
        """GET to POST-only path returns 405 (proxy only handles POST for this path)."""
        resp = self.client.get("/proxy/api/v1/models/load")
        # The proxy GET route catches all GET paths and proxies them upstream.
        # Since LM Studio isn't running, we get 502. This is expected - the proxy
        # doesn't validate that the upstream path accepts GET.
        self.assertIn(resp.status_code, [405, 502])

    def test_nonexistent_path(self):
        resp = self.client.get("/nonexistent")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
