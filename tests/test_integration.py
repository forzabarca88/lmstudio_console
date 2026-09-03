"""Integration tests for LM Studio Console.

Covers SPEC minimum test cases:
1. Connect to an endpoint
2. List models from the endpoint
3. Select a model and load it
4. Send a message - verify response rendered
5. Verify metrics updated correctly
6. New chat saves previous session in history
7. Unload a loaded model
8. Change system prompt and temperature
9. Load previous session and continue chatting
10. Delete saved sessions
11. Toggle web search tool calls

Plus: static file serving, CORS, error handling, file uploads, tool endpoints.
"""

import unittest
import os
import sys
import json
import io
import base64
import asyncio
import signal
import socket
import subprocess
import threading
import time
from unittest.mock import patch, MagicMock, AsyncMock, AsyncMock as AsyncMockType

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import httpx

from backend import server


# --- Mock response builders ---

def _httpx_response(status_code=200, json_data=None, text=None, headers=None):
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
    """Static files serve correctly."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_index_served(self):
        """ARRANGE: Server running
        ACT: GET /
        ASSERT: HTML page loads with all UI elements"""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("LM Studio Console", resp.text)

    def test_js_modules_served(self):
        """ARRANGE: Server running
        ACT: GET each JS module
        ASSERT: All load with status 200"""
        for module in ["app.js", "chat.js", "connection.js", "history.js",
                        "models.js", "state.js", "api.js", "ui.js", "trace.js"]:
            resp = self.client.get(f"/static/js/{module}")
            self.assertEqual(resp.status_code, 200, f"{module} failed")
            self.assertGreater(len(resp.text), 100, f"{module} empty")

    def test_css_served(self):
        """ARRANGE: Server running
        ACT: GET base.css and theme stylesheets
        ASSERT: All load with status 200"""
        for css_file in ["base.css", "theme-cyberpunk.css", "theme-light.css", "theme-warm.css"]:
            resp = self.client.get(f"/static/css/{css_file}")
            self.assertEqual(resp.status_code, 200, f"{css_file} failed")
            self.assertGreater(len(resp.text), 10, f"{css_file} empty")

    def test_favicon_served(self):
        """ARRANGE: Server running
        ACT: GET favicon
        ASSERT: No 404"""
        resp = self.client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 200)


class TestConnectAndListModels(unittest.TestCase):
    """SPEC: Connect to endpoint, list models."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_list_openai_models(self, mock_proxy):
        """ARRANGE: OpenAI-compatible endpoint
        ACT: GET /proxy/v1/models
        ASSERT: Model list returned"""
        mock_proxy.return_value = _openai_models()
        resp = self.client.get("/proxy/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["object"], "list")
        self.assertEqual(len(data["data"]), 2)

    @patch("backend.server.proxy_request")
    def test_list_lm_studio_models(self, mock_proxy):
        """ARRANGE: LM Studio endpoint
        ACT: GET /proxy/api/v1/models
        ASSERT: Models with loaded state returned"""
        mock_proxy.return_value = _lm_models()
        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        loaded = [m for m in data["models"] if m.get("loaded_instances")]
        self.assertEqual(len(loaded), 1)

    @patch("backend.server.proxy_request")
    def test_connect_error(self, mock_proxy):
        """ARRANGE: Endpoint not running
        ACT: GET /proxy/api/v1/models
        ASSERT: 502 with error message"""
        mock_proxy.side_effect = httpx.ConnectError("Connection refused")
        resp = self.client.get("/proxy/api/v1/models")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("error", resp.json())

    @patch("backend.server.proxy_request")
    def test_valid_lan_header_used(self, mock_proxy):
        """ARRANGE: OpenAI-compatible endpoint; client supplies a LAN URL header
        ACT: GET /proxy/v1/models with X-LM-Studio-URL: http://192.168.0.5:1234
        ASSERT: 200 and the header URL is used as the proxy target"""
        mock_proxy.return_value = _openai_models()
        resp = self.client.get("/proxy/v1/models",
                               headers={"X-LM-Studio-URL": "http://192.168.0.5:1234"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_proxy.call_args.kwargs["target_url"],
                         "http://192.168.0.5:1234")

    @patch("backend.server.proxy_request")
    def test_invalid_header_falls_back(self, mock_proxy):
        """ARRANGE: OpenAI-compatible endpoint; client supplies a blocked loopback URL header
        ACT: GET /proxy/v1/models with X-LM-Studio-URL: http://127.0.0.1:9999
        ASSERT: 200 and the configured fallback URL is used"""
        mock_proxy.return_value = _openai_models()
        resp = self.client.get("/proxy/v1/models",
                               headers={"X-LM-Studio-URL": "http://127.0.0.1:9999"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_proxy.call_args.kwargs["target_url"],
                         os.getenv("LM_STUDIO_URL", "http://localhost:1234"))


class TestModelLoadUnload(unittest.TestCase):
    """SPEC: Load and unload models."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_load_model(self, mock_proxy):
        """ARRANGE: Model available
        ACT: POST /proxy/api/v1/models/load
        ASSERT: Model loaded with instance_id"""
        mock_proxy.return_value = _load_response("inst-123", 3.2)
        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "llama-3.1-8b", "echo_load_config": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["instance_id"], "inst-123")
        self.assertEqual(data["status"], "loaded")

    @patch("backend.server.proxy_request")
    def test_load_large_model_timeout(self, mock_proxy):
        """ARRANGE: Large model (5 min load)
        ACT: POST load
        ASSERT: Succeeds with long load_time"""
        mock_proxy.return_value = _load_response("inst-big", 300.0)
        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "huge-model-70b"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["load_time_seconds"], 300.0)

    @patch("backend.server.proxy_request")
    def test_unload_model(self, mock_proxy):
        """ARRANGE: Model loaded
        ACT: POST /proxy/api/v1/models/unload
        ASSERT: Model unloaded"""
        mock_proxy.return_value = _unload_response("inst-123")
        resp = self.client.post("/proxy/api/v1/models/unload",
                                 json={"instance_id": "inst-123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["instance_id"], "inst-123")

    @patch("backend.server.proxy_request")
    def test_load_nonexistent_model(self, mock_proxy):
        """ARRANGE: Unknown model
        ACT: POST load
        ASSERT: 404 error"""
        mock_proxy.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(),
            response=_httpx_response(404, {"error": "model not found"})
        )
        resp = self.client.post("/proxy/api/v1/models/load",
                                 json={"model": "nonexistent"})
        self.assertEqual(resp.status_code, 404)


class TestChatProxy(unittest.TestCase):
    """SPEC: Send message via proxy, verify response and metrics."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_send_message(self, mock_proxy):
        """ARRANGE: Model loaded, user sends message
        ACT: POST /proxy/v1/chat/completions
        ASSERT: Response returned with correct content"""
        mock_proxy.return_value = _chat_response("The answer is 42.")
        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "What is the answer?"}],
            "temperature": 0.5,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["choices"][0]["message"]["content"], "The answer is 42.")

    @patch("backend.server.proxy_request")
    def test_chat_with_system_prompt(self, mock_proxy):
        """ARRANGE: System prompt configured
        ACT: POST chat with system message
        ASSERT: System prompt sent to endpoint"""
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
        # Verify system prompt was forwarded
        call_body = mock_proxy.call_args.kwargs.get("body")
        self.assertEqual(call_body["messages"][0]["role"], "system")

    @patch("backend.server.proxy_request")
    def test_chat_metrics(self, mock_proxy):
        """ARRANGE: Chat response with usage
        ACT: POST chat
        ASSERT: Metrics (tokens) returned correctly"""
        mock_proxy.return_value = _httpx_response(200, {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        })
        resp = self.client.post("/proxy/v1/chat/completions", json={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Say hi"}],
        })
        data = resp.json()
        self.assertEqual(data["usage"]["total_tokens"], 18)
        self.assertEqual(data["usage"]["prompt_tokens"], 10)
        self.assertEqual(data["usage"]["completion_tokens"], 8)

    @patch("backend.server.proxy_stream_iter")
    @patch("backend.server.proxy_request")
    @patch("backend.server._MAX_PROXY_BODY", 1024)
    def test_proxy_body_too_large(self, mock_proxy, mock_stream):
        """ARRANGE: proxy body cap patched to 1 KiB
        ACT: POST /proxy/v1/chat/completions with a 4 KiB body
        ASSERT: 413 with error JSON and no upstream call"""
        body = b"x" * 4096
        resp = self.client.post("/proxy/v1/chat/completions",
                                 content=body,
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 413)
        self.assertIn("Request body too large", resp.json()["error"])
        mock_proxy.assert_not_called()
        mock_stream.assert_not_called()

    @patch("backend.server.proxy_stream_iter")
    def test_streaming_chat(self, mock_stream):
        """ARRANGE: Streaming request
        ACT: POST chat with stream:true
        ASSERT: SSE stream with correct headers and content"""
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
        content = resp.content.decode()
        self.assertIn("Hello", content)
        self.assertIn("world", content)


class TestChatEndpoint(unittest.TestCase):
    """SPEC: Pydantic AI chat endpoint /api/chat.

    Behaviour tests are in test_agent.py (unit tests with proper mocks).
    This class verifies the endpoint exists and returns correct headers.
    """

    def setUp(self):
        self.client = TestClient(server.app)

    def test_chat_endpoint_exists(self):
        """ARRANGE: Server running
        ACT: POST /api/chat with valid body
        ASSERT: Endpoint exists (not 404)"""
        resp = self.client.post("/api/chat", json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        self.assertNotEqual(resp.status_code, 404)

    def test_chat_returns_sse_content_type(self):
        """ARRANGE: Server running
        ACT: POST /api/chat
        ASSERT: Returns text/event-stream content type"""
        resp = self.client.post("/api/chat", json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))

    def test_chat_accepts_all_params(self):
        """ARRANGE: Server running
        ACT: POST /api/chat with all parameters
        ASSERT: Request accepted (not 400/404)"""
        resp = self.client.post("/api/chat", json={
            "model": "llama-3.1-8b",
            "messages": [
                {"role": "system", "content": "Be concise"},
                {"role": "user", "content": "Hi"},
            ],
            "temperature": 0.5,
            "system_prompt": "You are a helpful assistant",
            "toolCallEnabled": True,
        })
        self.assertNotEqual(resp.status_code, 404)

    def test_chat_streams_content_and_usage(self):
        """ARRANGE: Mock Pydantic AI agent's stream_response
        ACT: POST /api/chat
        ASSERT: SSE stream contains content payloads and usage metadata"""
        from backend.agent import ChatAgent
        from pydantic_ai.messages import ModelResponse, TextPart

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"content": "Hello"}
                yield {"content": " world"}
                yield {"thinking_done": True}
                yield {
                    "__usage__": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    }
                }

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"content": "Hello"', content)
            self.assertIn('"content": " world"', content)
            self.assertIn('"thinking_done"', content)
            self.assertIn('"__usage__"', content)
            self.assertIn('"prompt_tokens": 10', content)

    def test_chat_streams_thinking_tokens(self):
        """ARRANGE: Mock Pydantic AI agent to produce thinking tokens
        ACT: POST /api/chat
        ASSERT: SSE stream contains thinking payloads and thinking_done marker"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"thinking": "Let me think"}
                yield {"thinking": " about this"}
                yield {"thinking_done": True}
                yield {"content": "The answer is 42."}
                yield {
                    "__usage__": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                }

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "What is 6*7?"}],
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"thinking": "Let me think"', content)
            self.assertIn('"thinking": " about this"', content)
            self.assertIn('"thinking_done": true', content)
            self.assertIn('"content": "The answer is 42."', content)
            self.assertIn('"__usage__"', content)

    def test_chat_forwards_tool_lifecycle_events(self):
        """ARRANGE: Agent emits tool call and result events
        ACT: POST /api/chat
        ASSERT: Both lifecycle payloads reach the SSE client unchanged"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {
                    "tool_call": {
                        "tool_call_id": "call_1",
                        "name": "web_search",
                        "args": {"query": "Python"},
                        "status": "executing",
                    }
                }
                yield {
                    "tool_result": {
                        "tool_call_id": "call_1",
                        "name": "web_search",
                        "status": "done",
                        "result": "Python result",
                    }
                }
                yield {"content": "Here is the answer."}
                yield {"__usage__": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Search"}],
                "toolCallEnabled": True,
            })

            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"tool_call"', content)
            self.assertIn('"tool_result"', content)
            self.assertIn('"call_1"', content)
            self.assertIn('"Python result"', content)


class TestTools(unittest.TestCase):
    """SPEC: Private tool HTTP endpoints removed (tools are internal to the chat agent)."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_tools_endpoint_removed(self):
        """ARRANGE: Server running
        ACT: GET /api/tools
        ASSERT: 404 — endpoint no longer exists"""
        resp = self.client.get("/api/tools")
        self.assertEqual(resp.status_code, 404)

    def test_tool_exec_endpoint_removed(self):
        """ARRANGE: Server running
        ACT: POST /api/tool-exec
        ASSERT: 404 — endpoint no longer exists"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "web_search",
            "arguments": {"query": "x"},
        })
        self.assertEqual(resp.status_code, 404)


class TestFileUpload(unittest.TestCase):
    """SPEC: Attach files for multimodal models."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_upload_file(self):
        """ARRANGE: Text file
        ACT: POST /api/upload
        ASSERT: Returns base64 content with metadata"""
        file_data = io.BytesIO(b"test file content")
        resp = self.client.post("/api/upload",
                                 files={"file": ("test.txt", file_data, "text/plain")})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["filename"], "test.txt")
        self.assertEqual(data["mimeType"], "text/plain")
        self.assertEqual(data["size"], 17)
        decoded = base64.b64decode(data["base64"])
        self.assertEqual(decoded, b"test file content")

    def test_upload_too_large(self):
        """ARRANGE: File exceeding the 50 MB server-side cap
        ACT: POST /api/upload
        ASSERT: 413 with 'File too large' error"""
        size = 50 * 1024 * 1024 + 1
        file_data = io.BytesIO(b"\x00" * size)
        resp = self.client.post("/api/upload",
                                 files={"file": ("big.bin", file_data, "application/octet-stream")})
        self.assertEqual(resp.status_code, 413)
        self.assertIn("File too large", resp.json()["error"])

    def test_upload_image(self):
        """ARRANGE: Image file
        ACT: POST /api/upload
        ASSERT: isImage flag set"""
        png_data = io.BytesIO(bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
            0x54, 0x08, 0xD0, 0x50, 0x52, 0x00, 0x01, 0x00,
            0x01, 0x00, 0x00, 0x0D, 0x07, 0xC1, 0x00, 0x00,
            0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42,
            0x60, 0x82,
        ]))
        resp = self.client.post("/api/upload",
                                 files={"file": ("test.png", png_data, "image/png")})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["isImage"])

    def test_upload_content_length_reject(self):
        """ARRANGE: Content-Length > 2x a small patched cap
        ACT: POST /api/upload
        ASSERT: 413 early reject (before multipart form processing)"""
        with patch("backend.server._MAX_UPLOAD_SIZE", 1024):
            body = b"x" * 4096  # > 2 * 1024
            resp = self.client.post("/api/upload",
                                     content=body,
                                     headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(resp.status_code, 413)
        self.assertIn("File too large", resp.json()["error"])

    def test_upload_no_file(self):
        """ARRANGE: No file provided
        ACT: POST /api/upload
        ASSERT: 400 error"""
        resp = self.client.post("/api/upload")
        self.assertEqual(resp.status_code, 400)


class TestCORS(unittest.TestCase):
    """CORS headers for cross-origin access."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_cors_preflight(self):
        """ARRANGE: Cross-origin request
        ACT: OPTIONS /proxy/v1/models
        ASSERT: 200 with CORS headers"""
        resp = self.client.options("/proxy/v1/models",
                                    headers={"Origin": "http://example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_cors_on_proxy(self):
        """ARRANGE: Cross-origin request to proxy
        ACT: GET /proxy/v1/models
        ASSERT: CORS headers present"""
        with patch("backend.server.proxy_request") as mock:
            mock.return_value = _openai_models()
            resp = self.client.get("/proxy/v1/models",
                                    headers={"Origin": "http://example.com"})
            self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_cors_on_api(self):
        """ARRANGE: Cross-origin request to API
        ACT: GET /
        ASSERT: CORS headers present"""
        resp = self.client.get("/",
                                headers={"Origin": "http://example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")


class TestAuthForwarding(unittest.TestCase):
    """Auth headers forwarded to upstream."""

    def setUp(self):
        self.client = TestClient(server.app)

    @patch("backend.server.proxy_request")
    def test_auth_header_forwarded(self, mock_proxy):
        """ARRANGE: Bearer token in request
        ACT: GET /proxy/v1/models
        ASSERT: Token forwarded to upstream"""
        mock_proxy.return_value = _openai_models()
        self.client.get("/proxy/v1/models",
                        headers={"Authorization": "Bearer my-token"})
        call_kwargs = mock_proxy.call_args.kwargs
        self.assertIn("headers", call_kwargs)
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer my-token")


class TestSessionManagement(unittest.TestCase):
    """SPEC: Session lifecycle backend tests.

    Full session lifecycle (save, continue, delete) is a frontend-only concept
    (localStorage) and is tested in test_js_runtime.js.

    This class verifies that the backend /api/chat endpoint correctly handles
    multi-turn message arrays — simulating restored sessions sent by the frontend.
    """

    def setUp(self):
        self.client = TestClient(server.app)

    def test_chat_accepts_multi_turn_conversation(self):
        """ARRANGE: Multi-turn message array simulating a restored session\nACT: POST /api/chat with conversation history\nASSERT: Endpoint accepts and processes the full message array"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"content": "Based on our conversation, the answer is 42."}
                yield {"thinking_done": True}
                yield {"__usage__": {"prompt_tokens": 25, "completion_tokens": 10, "total_tokens": 35}}

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the meaning of life?"},
                    {"role": "assistant", "content": "The meaning of life is a philosophical question."},
                    {"role": "user", "content": "Can you give me a numerical answer?"},
                ],
                "temperature": 0.5,
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"content": "Based on our conversation, the answer is 42."', content)

            # Verify agent received all 3 messages (system + 2 user + 1 assistant)
            call_args = mock_chat.call_args
            messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
            self.assertEqual(len(messages), 4)

    def test_chat_handles_tool_call_history(self):
        """ARRANGE: Message array with tool call history\nACT: POST /api/chat with tool call messages\nASSERT: Endpoint processes tool call messages correctly"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"content": "The search results show Python is a programming language."}
                yield {"thinking_done": True}
                yield {"__usage__": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}}

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": "What is Python?"},
                    {"role": "assistant", "content": None,
                     "tool_calls": [
                         {
                             "id": "call_abc123",
                             "type": "function",
                             "function": {
                                 "name": "web_search",
                                 "arguments": '{"query": "Python programming language"}',
                             },
                         },
                     ],
                     },
                    {"role": "tool", "content": "Python is a programming language.",
                     "tool_call_id": "call_abc123"},
                    {"role": "user", "content": "Summarize it"},
                ],
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"content": "The search results show', content)

    def test_chat_single_message_still_works(self):
        """ARRANGE: Single user message (no conversation history)\nACT: POST /api/chat with one message\nASSERT: Endpoint processes single message correctly"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"content": "Hello!"}
                yield {"thinking_done": True}
                yield {"__usage__": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"content": "Hello!"', content)

    def test_chat_with_multimodal_history(self):
        """ARRANGE: Message array with multimodal (image) content\nACT: POST /api/chat with image_url in content\nASSERT: Endpoint processes multimodal messages"""
        from backend.agent import ChatAgent

        with patch.object(ChatAgent, "chat") as mock_chat:
            async def mock_chat_generator(**kwargs):
                yield {"content": "That image shows a cat."}
                yield {"thinking_done": True}
                yield {"__usage__": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26}}

            mock_chat.return_value = mock_chat_generator()

            resp = self.client.post("/api/chat", json={
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": [
                        {"type": "text", "text": "What animal is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                    ]},
                    {"role": "assistant", "content": "That looks like a cat."},
                    {"role": "user", "content": "What color is it?"},
                ],
            })
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn('"content": "That image shows a cat."', content)


class TestChatCancellation(unittest.TestCase):
    """Backend cancellation on client disconnect.

    `request.is_disconnected()` does not fire reliably under FastAPI's
    TestClient (the ASGI `http.disconnect` message is only sent after the
    request body is fully read and the response completes). To exercise the
    server's cancellation wiring deterministically, we patch
    `starlette.requests.Request.is_disconnected` to flip to True after a
    few polling calls and then close the streaming client response
    mid-stream (exiting the `with` block). Closing the client response
    abandons the server's generator, which surfaces as `GeneratorExit`
    inside the mocked upstream generator.
    """

    def setUp(self):
        self.client = TestClient(server.app)

    def test_chat_generator_abandoned_on_client_close(self):
        """ARRANGE: Mock ChatAgent.chat to yield content then sleep; patch
                   is_disconnected to return True after a few polls
        ACT: POST /api/chat and close the streaming response mid-stream
        ASSERT: The mocked chat generator is abandoned (GeneratorExit) and
               not fully consumed.

        This verifies the GeneratorExit path when the client closes the
        response mid-stream. The cooperative-cancel path (where the agent
        observes cancel_event and emits a __cancelled__ marker) is covered
        by test_chat_cooperative_cancel_emits_marker below, and the agent's
        own cooperative cancellation is unit-tested in test_agent.py
        (test_chat_cancels_on_event)."""
        state = {"yields": 0, "fully_consumed": False, "abandoned": False}

        async def mock_chat_generator():
            try:
                for i in range(10):
                    state["yields"] += 1
                    yield {"content": f"chunk{i}"}
                    await asyncio.sleep(0.05)
                state["fully_consumed"] = True
            except GeneratorExit:
                state["abandoned"] = True
                raise

        poll_count = {"n": 0}

        async def fake_is_disconnected(self):
            poll_count["n"] += 1
            return poll_count["n"] > 1

        import starlette.requests as starlette_requests

        with patch.object(starlette_requests.Request, "is_disconnected", fake_is_disconnected), \
                patch.object(server.ChatAgent, "chat", MagicMock(return_value=mock_chat_generator())):
            with self.client.stream("POST", "/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            }) as resp:
                received = []
                for line in resp.iter_lines():
                    if line:
                        received.append(line)
                    if any("chunk1" in line for line in received):
                        break

        # The mocked chat generator was abandoned mid-stream.
        self.assertTrue(state["abandoned"],
                        "chat generator was not abandoned on disconnect")
        self.assertFalse(state["fully_consumed"],
                        "chat generator was fully consumed despite disconnect")
        # We received at least one content chunk before disconnecting.
        self.assertTrue(any("content" in line for line in received),
                        "no content received before disconnect")
        # The disconnect watcher polled is_disconnected at least once.
        self.assertGreater(poll_count["n"], 0)

    def test_chat_cooperative_cancel_emits_marker(self):
        """ARRANGE: Mock ChatAgent.chat to cooperatively cancel (emit
                   __cancelled__ and return) when the cancel_event passed by
                   the server is set; patch is_disconnected to return True
                   after a few polls so the server's watcher sets cancel_event
        ACT: POST /api/chat and read the SSE stream
        ASSERT: The SSE stream contains a __cancelled__ marker line, proving
               the cooperative-cancel path emits the marker that lets the
               frontend distinguish cancellation from a normal end or error."""
        captured = {"cancel_event": None}

        def mock_chat(**kwargs):
            # Capture the cancel_event the server passes so the generator can
            # observe it. MagicMock(side_effect=...) forwards the call kwargs.
            captured["cancel_event"] = kwargs.get("cancel_event")

            async def gen():
                for i in range(20):
                    yield {"content": f"chunk{i}"}
                    await asyncio.sleep(0.05)
                    ce = captured["cancel_event"]
                    if ce is not None and ce.is_set():
                        yield {"__cancelled__": True}
                        return
                yield {
                    "__usage__": {
                        "prompt_tokens": 5,
                        "completion_tokens": 5,
                        "total_tokens": 10,
                    }
                }

            return gen()

        poll_count = {"n": 0}

        async def fake_is_disconnected(self):
            poll_count["n"] += 1
            return poll_count["n"] > 1

        import starlette.requests as starlette_requests

        with patch.object(starlette_requests.Request, "is_disconnected", fake_is_disconnected), \
                patch.object(server.ChatAgent, "chat", MagicMock(side_effect=mock_chat)):
            with self.client.stream("POST", "/api/chat", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            }) as resp:
                received = []
                for line in resp.iter_lines():
                    if line:
                        received.append(line)
                    if any("__cancelled__" in line for line in received):
                        break

        # The SSE stream must contain a __cancelled__ marker line.
        self.assertTrue(
            any("__cancelled__" in line and "data:" in line for line in received),
            f"no __cancelled__ SSE line in stream: {received}",
        )
        # The server wired the cancel_event through to agent.chat.
        self.assertIsNotNone(captured["cancel_event"],
                        "cancel_event was not passed to agent.chat")
        self.assertTrue(captured["cancel_event"].is_set(),
                        "cancel_event was not set by the disconnect watcher")
        # The disconnect watcher actually polled is_disconnected.
        self.assertGreater(poll_count["n"], 0)

    def test_proxy_stream_disconnect_aborts(self):
        """ARRANGE: Patch proxy_stream_iter with a generator that sleeps and
                   records whether it was fully consumed; patch is_disconnected
                   to return True after a few polls
        ACT: POST a streaming proxy request and close the response mid-stream
        ASSERT: The upstream generator was abandoned (GeneratorExit) and not
               fully consumed"""
        state = {"yields": 0, "fully_consumed": False, "abandoned": False}

        async def mock_proxy_iter(*args, **kwargs):
            try:
                for i in range(10):
                    state["yields"] += 1
                    yield b'data: {"choices":[{"delta":{"content":"chunk%d"}}]}\n\n' % i
                    await asyncio.sleep(0.05)
                state["fully_consumed"] = True
            except GeneratorExit:
                state["abandoned"] = True
                raise

        poll_count = {"n": 0}

        async def fake_is_disconnected(self):
            poll_count["n"] += 1
            return poll_count["n"] > 1

        import starlette.requests as starlette_requests

        with patch.object(starlette_requests.Request, "is_disconnected", fake_is_disconnected), \
                patch("backend.server.proxy_stream_iter", mock_proxy_iter):
            with self.client.stream("POST", "/proxy/v1/chat/completions", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            }) as resp:
                received = []
                for line in resp.iter_lines():
                    if line:
                        received.append(line)
                    if any("chunk1" in line for line in received):
                        break

        # The upstream generator was abandoned mid-stream.
        self.assertTrue(state["abandoned"],
                        "proxy generator was not abandoned on disconnect")
        self.assertFalse(state["fully_consumed"],
                        "proxy generator was fully consumed despite disconnect")
        # We received at least one chunk before disconnecting.
        self.assertTrue(any("chunk0" in line for line in received),
                        "no stream content received before disconnect")
        # The disconnect check ran at least once.
        self.assertGreater(poll_count["n"], 0)


class TestErrorHandling(unittest.TestCase):
    """Error responses for invalid requests."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_wrong_method(self):
        """ARRANGE: GET-only endpoint
        ACT: POST request
        ASSERT: 405 Method Not Allowed"""
        resp = self.client.post("/", json={"test": "data"})
        self.assertEqual(resp.status_code, 405)

    def test_nonexistent_path(self):
        """ARRANGE: Unknown path
        ACT: GET /nonexistent
        ASSERT: 404 Not Found"""
        resp = self.client.get("/nonexistent")
        self.assertEqual(resp.status_code, 404)


class TestTraceLogSSE(unittest.TestCase):
    """SSE endpoint for streaming trace logs.

    Tests the LogStreamer class directly (push, subscribe, get_recent)
    since the StreamingResponse endpoint blocks indefinitely under
    FastAPI's TestClient (no real HTTP disconnect is simulated).

    The endpoint route itself is verified using httpx.AsyncClient against
    a running uvicorn server, or by checking the route registration.
    """

    def setUp(self):
        self.client = TestClient(server.app)
        from backend.log_streamer import log_streamer
        log_streamer._buffer.clear()

    def test_trace_logs_endpoint_registered(self):
        """ARRANGE: Server app configured
        ACT: Check route registry
        ASSERT: /api/trace-logs route exists with GET method"""
        from starlette.routing import Route
        routes = {r.path: list(r.methods) for r in server.app.routes
                  if isinstance(r, Route)}
        self.assertIn("/api/trace-logs", routes)
        self.assertIn("GET", routes["/api/trace-logs"])

    def test_trace_logs_response_headers(self):
        """ARRANGE: Server app configured
        ACT: Call the trace_logs handler directly
        ASSERT: Returns StreamingResponse with correct headers

        Calls the handler function directly (bypassing ASGI) to verify
        response type and headers without blocking on the SSE stream.
        """
        from backend.server import trace_logs
        from fastapi import Request
        from fastapi.responses import StreamingResponse

        # Create a mock request (scope type must be "http" for Starlette)
        mock_request = Request({
            "type": "http",
            "asgi": {"version": "3"},
            "http_version": "1.1",
            "method": "GET",
            "path": "/api/trace-logs",
            "headers": [],
        })

        # Call handler - returns StreamingResponse
        import asyncio
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(trace_logs(mock_request))
        loop.close()

        self.assertIsInstance(resp, StreamingResponse)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("content-type"),
                         "text/event-stream; charset=utf-8")
        self.assertEqual(resp.headers.get("cache-control"), "no-cache")
        self.assertEqual(resp.headers.get("connection"), "keep-alive")
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_trace_logs_cors_headers(self):
        """ARRANGE: Cross-origin request to trace logs
        ACT: Call trace_logs handler with Origin header
        ASSERT: CORS headers present"""
        from backend.server import trace_logs
        from fastapi import Request

        mock_request = Request({
            "type": "http",
            "asgi": {"version": "3"},
            "http_version": "1.1",
            "method": "GET",
            "path": "/api/trace-logs",
            "headers": [(b"origin", b"http://example.com")],
        })

        import asyncio
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(trace_logs(mock_request))
        loop.close()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_log_streamer_push_and_get_recent(self):
        """ARRANGE: Clean LogStreamer
        ACT: Push entries, then call get_recent()
        ASSERT: Returns entries in correct order with correct format"""
        from backend.log_streamer import log_streamer

        log_streamer._buffer.clear()
        log_streamer.push({"timestamp": "12:00:00", "level": "INFO",
                           "message": "Server started"})
        log_streamer.push({"timestamp": "12:00:01", "level": "DEBUG",
                           "message": "Processing request"})
        log_streamer.push({"timestamp": "12:00:02", "level": "WARNING",
                           "message": "High memory usage"})

        entries = log_streamer.get_recent(50)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["level"], "INFO")
        self.assertEqual(entries[0]["message"], "Server started")
        self.assertEqual(entries[1]["level"], "DEBUG")
        self.assertEqual(entries[1]["message"], "Processing request")
        self.assertEqual(entries[2]["level"], "WARNING")
        self.assertEqual(entries[2]["message"], "High memory usage")

        log_streamer._buffer.clear()

    def test_log_streamer_get_recent_limits_count(self):
        """ARRANGE: Push more entries than requested count
        ACT: Call get_recent(count=2)
        ASSERT: Returns only the last N entries"""
        from backend.log_streamer import log_streamer

        log_streamer._buffer.clear()
        for i in range(10):
            log_streamer.push({"timestamp": f"12:00:{i:02d}", "level": "INFO",
                               "message": f"Entry {i}"})

        entries = log_streamer.get_recent(2)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["message"], "Entry 8")
        self.assertEqual(entries[1]["message"], "Entry 9")

        log_streamer._buffer.clear()

    def test_log_streamer_empty_buffer(self):
        """ARRANGE: No entries in buffer
        ACT: Call get_recent()
        ASSERT: Returns empty list"""
        from backend.log_streamer import log_streamer

        log_streamer._buffer.clear()
        entries = log_streamer.get_recent(50)
        self.assertEqual(entries, [])

    def test_log_streamer_sse_format(self):
        """ARRANGE: Push entries
        ACT: Format as SSE lines
        ASSERT: Each line starts with 'data:' and contains valid JSON"""
        from backend.log_streamer import log_streamer
        import json

        log_streamer._buffer.clear()
        log_streamer.push({"timestamp": "12:00:00", "level": "INFO",
                           "message": "Test message"})

        entries = log_streamer.get_recent(50)
        for entry in entries:
            sse_line = f"data: {json.dumps(entry)}\n\n"
            self.assertTrue(sse_line.startswith("data:"))
            # Verify the JSON payload is parseable
            payload = sse_line[5:].strip()
            parsed = json.loads(payload)
            self.assertIn("level", parsed)
            self.assertIn("message", parsed)
            self.assertIn("timestamp", parsed)

        log_streamer._buffer.clear()


class TestGracefulShutdown(unittest.TestCase):
    """SPEC: CTRL+C must kill all connections and stop the server,
    force-stopping after 5 seconds.

    Runs the real server in a subprocess (same pattern as
    test_screenshot.py) and sends SIGINT:
      * with an open SSE stream, the graceful drain is blocked, so the
        5 second force-exit timer must terminate the process with status 1;
      * with no open streams, the process exits quickly with status 1.
    """

    # Server must answer /api/health within this budget after spawn.
    READY_TIMEOUT = 15.0
    # proc.wait budget after SIGINT (force timer is 5 s; generous margin).
    WAIT_TIMEOUT = 8.0
    # 5 s force timer + margin for signal delivery and process teardown.
    FORCE_EXIT_MAX_ELAPSED = 6.5
    # No active connections: graceful drain must finish quickly.
    CLEAN_EXIT_MAX_ELAPSED = 3.0

    @classmethod
    def _venv_python(cls):
        """Locate the virtualenv interpreter (mirrors test_screenshot.py)."""
        venv_python = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".venv", "bin", "python"
        )
        return venv_python if os.path.exists(venv_python) else sys.executable

    @staticmethod
    def _free_port():
        """Ask the OS for a free TCP port on 127.0.0.1."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _start_server(self, port):
        """Start run.py in a subprocess bound to 127.0.0.1:<port>."""
        env = os.environ.copy()
        env["LM_CONSOLE_HOST"] = "127.0.0.1"
        env["LM_CONSOLE_PORT"] = str(port)
        return subprocess.Popen(
            [self._venv_python(), "run.py"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def _wait_for_ready(self, proc, port):
        """Poll GET /api/health until 200 or the readiness budget runs out."""
        url = f"http://127.0.0.1:{port}/api/health"
        deadline = time.monotonic() + self.READY_TIMEOUT
        client = httpx.Client(timeout=2.0)
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    output = proc.stdout.read().decode(errors="replace")
                    self.fail(
                        f"Server exited before becoming ready "
                        f"(code {proc.returncode}): {output}"
                    )
                try:
                    if client.get(url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass  # not listening yet
                time.sleep(0.25)
        finally:
            client.close()
        self.fail(f"Server did not become ready within {self.READY_TIMEOUT:.0f}s")

    def _cleanup_process(self, proc):
        """Safety net: never leave a server process behind."""
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def test_sigint_clean_exit_without_streams(self):
        """ARRANGE: Server running, no open connections
        ACT: Send SIGINT
        ASSERT: Process exits quickly with status 1"""
        port = self._free_port()
        proc = self._start_server(port)
        try:
            self._wait_for_ready(proc, port)
            started = time.monotonic()
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=self.WAIT_TIMEOUT)
            elapsed = time.monotonic() - started
            self.assertEqual(returncode, 1)
            self.assertLess(elapsed, self.CLEAN_EXIT_MAX_ELAPSED,
                            f"clean drain took {elapsed:.1f}s")
        except subprocess.TimeoutExpired:
            self.fail(f"server did not exit within {self.WAIT_TIMEOUT:.0f}s of SIGINT")
        finally:
            self._cleanup_process(proc)

    def test_sigint_with_open_stream_force_exits(self):
        """ARRANGE: Server running with a long-lived SSE connection open
        ACT: Send SIGINT (the open stream blocks the graceful drain)
        ASSERT: The 5s force-exit timer terminates the process with status 1
                in < 6.5s (the drain alone would hang indefinitely)"""
        port = self._free_port()
        proc = self._start_server(port)
        stop_event = threading.Event()
        stream_state = {
            "client": None, "resp": None,
            "ready": threading.Event(), "error": None,
        }

        def open_stream():
            """Open GET /api/trace-logs and hold the SSE connection open."""
            try:
                client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))
                stream_state["client"] = client
                resp = client.stream("GET", f"http://127.0.0.1:{port}/api/trace-logs")
                stream_state["resp"] = resp
                # Headers received: the response is streaming, the server-side
                # generator is running, and the connection counts as active.
                resp.__enter__()
                stream_state["ready"].set()
                stop_event.wait()  # hold the connection open until told to stop
            except BaseException as e:
                stream_state["error"] = e
                stream_state["ready"].set()

        stream_thread = threading.Thread(target=open_stream, daemon=True)
        try:
            self._wait_for_ready(proc, port)
            stream_thread.start()
            self.assertTrue(
                stream_state["ready"].wait(10),
                "SSE stream thread did not report ready",
            )
            self.assertIsNone(stream_state["error"],
                              f"failed to open SSE stream: {stream_state['error']!r}")
            time.sleep(0.5)  # let the server-side stream settle
            started = time.monotonic()
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=self.WAIT_TIMEOUT)
            elapsed = time.monotonic() - started
            self.assertEqual(returncode, 1)
            self.assertLess(elapsed, self.FORCE_EXIT_MAX_ELAPSED,
                            f"force-exit took {elapsed:.1f}s (5s timer broken?)")
        except subprocess.TimeoutExpired:
            self.fail(
                f"server did not exit within {self.WAIT_TIMEOUT:.0f}s of SIGINT: "
                "open SSE stream blocked the drain and the 5s force timer did not fire"
            )
        finally:
            stop_event.set()
            resp = stream_state.get("resp")
            if resp is not None:
                try:
                    resp.__exit__(None, None, None)
                except Exception:
                    pass
            client = stream_state.get("client")
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if stream_thread.is_alive():
                stream_thread.join(timeout=3)
            self._cleanup_process(proc)


if __name__ == "__main__":
    unittest.main()
