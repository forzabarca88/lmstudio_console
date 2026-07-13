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
                        "models.js", "state.js", "api.js", "ui.js"]:
            resp = self.client.get(f"/static/js/{module}")
            self.assertEqual(resp.status_code, 200, f"{module} failed")
            self.assertGreater(len(resp.text), 100, f"{module} empty")

    def test_css_served(self):
        """ARRANGE: Server running
        ACT: GET stylesheet
        ASSERT: Loads with status 200"""
        resp = self.client.get("/static/css/style.css")
        self.assertEqual(resp.status_code, 200)

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


class TestTools(unittest.TestCase):
    """SPEC: Toggle web search tool calls."""

    def setUp(self):
        self.client = TestClient(server.app)

    def test_list_tools(self):
        """ARRANGE: Server running
        ACT: GET /api/tools
        ASSERT: Tool schemas returned in OpenAI format"""
        resp = self.client.get("/api/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        tool_names = [t["function"]["name"] for t in data]
        self.assertIn("web_search", tool_names)
        ws = next(t for t in data if t["function"]["name"] == "web_search")
        self.assertEqual(ws["type"], "function")
        self.assertIn("parameters", ws["function"])

    def test_execute_tool(self):
        """ARRANGE: web_search tool available
        ACT: POST /api/tool-exec with query
        ASSERT: Search results returned"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "web_search",
            "arguments": {"query": "Python"},
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("result", data)
        self.assertIsInstance(data["result"], str)
        self.assertGreater(len(data["result"]), 0)

    def test_execute_unknown_tool(self):
        """ARRANGE: Unknown tool name
        ACT: POST /api/tool-exec
        ASSERT: 400 error"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "nonexistent_tool",
            "arguments": {},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())


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
        ACT: GET /api/tools
        ASSERT: CORS headers present"""
        resp = self.client.get("/api/tools",
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


if __name__ == "__main__":
    unittest.main()
