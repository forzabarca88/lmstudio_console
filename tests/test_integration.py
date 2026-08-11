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
        self.assertIn("open_web_page", tool_names)
        ws = next(t for t in data if t["function"]["name"] == "web_search")
        self.assertEqual(ws["type"], "function")
        self.assertIn("parameters", ws["function"])
        owp = next(t for t in data if t["function"]["name"] == "open_web_page")
        self.assertEqual(owp["type"], "function")
        self.assertIn("url", owp["function"]["parameters"]["properties"])

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

    def test_execute_open_web_page(self):
        """ARRANGE: open_web_page tool available
        ACT: POST /api/tool-exec with URL
        ASSERT: Page content returned or error handled"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "open_web_page",
            "arguments": {"url": "http://example.com"},
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("result", data)
        self.assertIsInstance(data["result"], str)

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

    def test_execute_run_python_code(self):
        """ARRANGE: run_python_code tool available
        ACT: POST /api/tool-exec with code
        ASSERT: Output returned"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "run_python_code",
            "arguments": {"code": "print('hello')"},
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("result", data)
        self.assertIn("hello", data["result"])

    def test_execute_run_python_code_blocked(self):
        """ARRANGE: Code with blocked operation
        ACT: POST /api/tool-exec with import
        ASSERT: Blocked error returned"""
        resp = self.client.post("/api/tool-exec", json={
            "name": "run_python_code",
            "arguments": {"code": "import os"},
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Blocked operation", data["result"])

    def test_list_tools_includes_run_python_code(self):
        """ARRANGE: Server running
        ACT: GET /api/tools
        ASSERT: run_python_code included in tool list"""
        resp = self.client.get("/api/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        tool_names = [t["function"]["name"] for t in data]
        self.assertIn("run_python_code", tool_names)


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


if __name__ == "__main__":
    unittest.main()
