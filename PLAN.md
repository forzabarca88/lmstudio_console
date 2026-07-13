# PLAN.md - LM Studio Console

## Architecture Overview

### Core Design Decision: Pydantic AI for Tool Calls

**Mistake in previous attempt:** I tried to handle tool calls manually in the frontend by:
1. Frontend fetches tool schemas from backend
2. Frontend sends tools to LM Studio via proxy
3. Frontend parses SSE stream to detect tool calls
4. Frontend executes tools via `/api/tool-exec`
5. Frontend re-sends chat with tool results

**Correct approach:** Use Pydantic AI on the backend to handle the entire tool call loop automatically:
1. Frontend sends chat request to backend (no tools in the request)
2. Backend creates Pydantic AI Agent connected to LM Studio endpoint
3. Agent handles: send to LLM → detect tool calls → execute tools → send results back → get final response
4. Backend streams the final response to frontend via SSE

This is simpler, more reliable, and follows the SPEC requirement to "Use Pydantic AI".

---

## System Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   Browser    │ HTTP    │  FastAPI     │ HTTP    │  LM Studio   │
│  (Frontend) ├────────►│  (Backend)  ├────────►│  (Upstream) │
│              │         │              │        │              │
│  Chat UI     │         │  Pydantic AI │ proxy   │  /v1/chat   │
│  Model Mgmt  │         │  Agent       │         │  /api/v1/*  │
│  Settings    │         │              │        │              │
└─────────────┘         └──────────────┘         └─────────────┘
```

### Backend Modules

| Module | Responsibility |
|--------|---------------|
| `config.py` | Environment variables, paths |
| `logger.py` | Trace logging for proxy requests |
| `proxy.py` | HTTP proxy to LM Studio (model mgmt only) |
| `agent.py` | Pydantic AI Agent with tools (NEW) |
| `server.py` | FastAPI routes, CORS, static files |

### Frontend Modules

| Module | Responsibility |
|--------|---------------|
| `state.js` | App state, localStorage persistence |
| `api.js` | API call utilities (fetch wrapper) |
| `ui.js` | Toast notifications, formatting, metrics display |
| `connection.js` | Connect/disconnect, heartbeat, status |
| `models.js` | Model list, load, unload |
| `chat.js` | Send messages, streaming, render markdown/mermaid |
| `history.js` | Session save/load/delete/continue |
| `app.js` | Entry point, wire DOM events |

---

## API Design

### Backend Endpoints

#### Model Management (proxied to LM Studio native API)
- `GET /proxy/api/v1/models` → List models with loaded state
- `POST /proxy/api/v1/models/load` → Load model (10min timeout)
- `POST /proxy/api/v1/models/unload` → Unload model

#### Chat (Pydantic AI powered)
- `POST /api/chat` → Chat with tool call support
  - Request: `{model, messages, temperature, stream, toolCallEnabled}`
  - Response: SSE stream of text deltas
  - Backend uses Pydantic AI Agent to handle tool calls automatically
  - Frontend receives clean text stream, no tool call handling needed

#### File Upload
- `POST /api/upload` → Upload file, returns base64 + metadata

### LM Studio Endpoints Used

| Our Endpoint | LM Studio Endpoint | Purpose |
|-------------|-------------------|---------|
| `/proxy/api/v1/models` | `/api/v1/models` | List models |
| `/proxy/api/v1/models/load` | `/api/v1/models/load` | Load model |
| `/proxy/api/v1/models/unload` | `/api/v1/models/unload` | Unload model |
| `/api/chat` | `/v1/chat/completions` | Chat (via Pydantic AI) |

**Key insight:** Model management uses LM Studio's native API. Chat uses OpenAI-compatible endpoint via Pydantic AI.

---

## Pydantic AI Agent Design

```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

class ChatAgent:
    def __init__(self, base_url: str, api_key: str = ""):
        provider = OpenAIProvider(base_url=base_url, api_key=api_key or None)
        self.agent = Agent(
            OpenAIChatModel("placeholder", provider=provider),
            system_prompt="",  # Set per-request
        )

    @agent.tool_plain
    async def web_search(query: str) -> str:
        """Search the web for information."""
        # Use ddgs for DuckDuckGo search
        ...

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
        system_prompt: str = "",
        tool_call_enabled: bool = False,
    ) -> AsyncIterable[str]:
        """Stream chat response with optional tool calls."""
        # Configure agent for this request
        self.agent.model_settings = {"temperature": temperature}
        self.agent.system_prompt = system_prompt

        # Add tool if enabled
        if tool_call_enabled:
            self.agent.tool("web_search")

        # Run agent and stream
        async with self.agent.run_stream(messages[-1]["content"]) as result:
            async for text in result.stream():
                yield text
```

---

## Frontend Design

### Chat Flow

1. User types message, clicks send
2. Frontend sends `POST /api/chat` with:
   - model, messages, temperature, stream, toolCallEnabled
3. Backend creates Pydantic AI Agent, runs it with tools if enabled
4. Backend streams text deltas via SSE
5. Frontend renders streaming text, updates metrics
6. On completion, saves session to history

**No tool call handling in frontend** - Pydantic AI handles everything on the backend.

### File Attachments

1. User clicks attach button, selects file(s)
2. Frontend shows preview chips
3. On send, uploads files to `/api/upload` → gets base64
4. Builds multimodal content array: `[{type:"text",text:"..."},{type:"image_url",...}]`
5. Sends to `/api/chat`

### Metrics

- Tokens/second: `token_count / elapsed_time`
- Time to first token: Time from send to first delta
- Total tokens: From usage in final response

---

## Implementation Plan

### Phase 1: Backend Foundation
1. `config.py` - Environment variable loading
2. `logger.py` - Trace logging with request/response tracking
3. `proxy.py` - HTTP proxy for model management endpoints
4. `agent.py` - Pydantic AI Agent with web_search tool
5. `server.py` - FastAPI app with all routes

### Phase 2: Frontend Core
1. `state.js` - State management, localStorage
2. `api.js` - Fetch wrapper with auth headers
3. `ui.js` - Toast, formatting, metrics display
4. `connection.js` - Connect/disconnect, heartbeat
5. `models.js` - Model list, load, unload
6. `chat.js` - Send, stream, render markdown/mermaid
7. `history.js` - Session management
8. `app.js` - Entry point, wire events

### Phase 3: Features
1. File attachments (upload, preview, multimodal)
2. Tool call toggle (settings panel)
3. Mermaid JS diagram rendering
4. Copy button for responses

### Phase 4: Polish
1. Responsive design
2. Accessibility
3. Error handling
4. Loading states

---

## Testing Strategy

### Backend Tests (unittest)

**Principle:** Test end results, not implementation details.

| Test | Validates |
|------|-----------|
| Config defaults | Environment variable loading |
| Proxy GET/POST | Model list, load, unload |
| Proxy error handling | 502 on connect error, 404 passthrough |
| CORS headers | Cross-origin access |
| Chat endpoint | Pydantic AI integration, tool calls |
| File upload | Base64 encoding, image detection |
| Auth forwarding | API token passed to upstream |

### Frontend Tests

**Principle:** Catch browser errors before they reach users.

| Test | Method |
|------|--------|
| JS syntax check | `node --check` on each module |
| Reserved words | Scan for `arguments`, `eval` as identifiers |
| Import/export consistency | Verify all imports have matching exports |
| Static file serving | All files served with correct MIME types |

### Integration Tests

**Principle:** Test user workflows end-to-end.

| Test Case | Covers |
|-----------|--------|
| Connect + list models | Connection, model discovery |
| Load model | Model loading with feedback |
| Send message | Chat with streaming response |
| Metrics display | Tokens/s, TTFT, total tokens |
| New chat + history save | Session management |
| Unload model | Model unloading |
| System prompt + temperature | Settings forwarded correctly |
| Continue session | History restore |
| Delete session | History cleanup |
| Toggle web search | Tool call enable/disable |

---

## Known Pitfalls to Avoid

1. **Don't handle tool calls in frontend** - Use Pydantic AI on backend
2. **Don't use `arguments` as parameter name** - Reserved in strict mode
3. **Don't concatenate objects to strings** - `content += delta` → `[object Object]`
4. **Don't check for string presence in tests** - Test actual behavior
5. **Don't use OpenAI format for model management** - Use LM Studio native API
6. **Don't forget CORS** - Required for cross-origin access
7. **Don't clear chat on model change** - Spec requires preserving chat
8. **Don't use `null` as parameter name** - Reserved word in JS

---

## Dependencies

### Backend
- `fastapi>=0.115` - Web framework
- `uvicorn>=0.32` - ASGI server
- `httpx>=0.27` - HTTP client for proxy
- `pydantic-ai>=2.0` - Agent framework with tool support
- `ddgs>=0.1` - DuckDuckGo search for web_search tool

### Frontend (CDN)
- `marked` - Markdown rendering
- `mermaid` - Diagram rendering

---

## File Structure

```
lmstudio_console/
├── backend/
│   ├── __init__.py
│   ├── config.py      # Environment variables
│   ├── logger.py      # Trace logging
│   ├── proxy.py       # HTTP proxy (model mgmt)
│   ├── agent.py       # Pydantic AI Agent (NEW)
│   └── server.py      # FastAPI app
├── static/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── app.js     # Entry point
│   │   ├── state.js   # State + localStorage
│   │   ├── api.js     # Fetch wrapper
│   │   ├── ui.js      # Toast, formatting, metrics
│   │   ├── connection.js  # Connect, heartbeat
│   │   ├── models.js  # Model management
│   │   ├── chat.js    # Chat + streaming
│   │   └── history.js # Session management
│   └── index.html
├── tests/
│   ├── test_backend.py    # Config, logger, proxy
│   ├── test_agent.py      # Pydantic AI agent
│   ├── test_integration.py # End-to-end workflows
│   └── test_js_syntax.py  # JS validation
├── run.py
├── pyproject.toml
├── SPEC.md
├── PLAN.md
└── README.md
```
