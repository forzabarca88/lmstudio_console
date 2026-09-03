# LM Studio Console

> ⚠️ This was a personal project primarily implemented with generative AI. Please review code accordingly before re-use.

Remote web management dashboard and chat interface for LM Studio.

## Features

- **Model Management**: List, load, and unload LM Studio models
- **Chat Interface**: Full-featured chat with any OpenAI-compatible endpoint
- **Streaming**: Real-time SSE streaming responses
- **Markdown**: Messages rendered with full Markdown support
- **Mermaid JS**: Graph and diagram rendering in chat responses
- **Chat Metrics**: Tokens/second, time to first token, and total tokens displayed live
- **Copy Button**: One-click copy for each assistant response
- **Stop Button**: Send button toggles to stop button during streaming; cancels requests on both client and server/endpoint side
- **Session History**: Last 10 chat sessions saved; continue or delete from history (both cancel active requests)
- **Connection Monitoring**: Automatic disconnection detection via heartbeat ping
- **Collapsible Sidebar**: Collapsible on all screen sizes — chevron tab on desktop, full-width toggle on mobile
- **Persistent Chat**: Chat messages preserved across model changes and disconnects
- **Settings**: Configurable system prompt and temperature
- **Persistence**: Settings saved to localStorage between sessions
- **Trace Logging**: Detailed request/response logging on the server console
- **Live Trace Log**: Collapsible panel showing real-time server logs streamed via SSE (readable font sizes)
- **UI Themes**: 3 toggleable themes with very different design languages — Cyberpunk Dark (neon glow, electric violet), Light Professional (sharp/corporate, tight spacing, flat), Warm Minimal (rounded/editorial, dashed borders, cream palette) — with persistent preference
- **File Attachments**: Upload images, audio, documents for multimodal models
- **Agentic Tools**: Toggle tool calls (web search, open web page, run python code) for LLM-agentic behavior; Pydantic AI handles tool execution internally with SSE events streamed to frontend
- **Tool Call Display**: Real-time feedback in chat window showing tool name, arguments, and execution status (executing → done/error) with distinct visual states
- **Pydantic AI**: Backend tool definitions and execution powered by Pydantic AI
- **Thinking Tokens**: Expandable thinking blocks shown when the model produces reasoning tokens
- **Graceful Shutdown**: Ctrl+C stops server cleanly with 5-second force-stop timeout

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) for package management
- LM Studio running with API server enabled (or any OpenAI-compatible endpoint)

## Usage

```bash
# Start the server (default: http://localhost:8080)
uv run python run.py

# Custom port and LM Studio URL
LM_CONSOLE_PORT=9090 LM_STUDIO_URL=http://localhost:1234 uv run python run.py
```

Open `http://localhost:8080` in your browser.

## Docker

Build and run a minimal production image (Python 3.12 + uv):

```bash
docker build -t lmstudio-console .

# Point LM_STUDIO_URL at the host's LM Studio (use host.docker.internal on Docker Desktop)
docker run -d -p 8080:8080 \
  -e LM_STUDIO_URL=http://192.168.0.5:1234 \
  --name lmstudio-console lmstudio-console
```

The app runs as a non-root user; configure ports/URLs via the same environment variables as above.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `LM_CONSOLE_HOST` | `0.0.0.0` | Server bind address |
| `LM_CONSOLE_PORT` | `8080` | Server port |
| `LM_STUDIO_URL` | `http://localhost:1234` | LM Studio API URL |

## Project Structure

```
├── backend/
│   ├── __init__.py    # Empty package init
│   ├── config.py      # Configuration from environment variables (host, port, LM Studio URL, static dir)
│   ├── logger.py      # Trace logging with shared logger, RequestTrace context, cancellation tracking, and SSE streamer integration
│   ├── log_streamer.py # SSE broadcaster for trace log entries (ring buffer, subscriber broadcast)
│   ├── proxy.py       # HTTP proxy using httpx for forwarding requests to LM Studio/OpenAI endpoints with streaming and graceful cancellation support
│   ├── server.py      # FastAPI app with proxy routes, chat endpoint (client disconnect detection, cooperative cancellation), file upload (50MB cap), trace log SSE, CORS middleware, SSRF-safe X-LM-Studio-URL handling, and graceful shutdown (5s force-exit)
│   ├── agent.py       # Pydantic AI Agent wrapper for chat with automatic tool call handling (Pydantic AI internal tool loop), SSE tool_call/tool_result events, thinking tokens, message format conversion, and cooperative cancellation support
│   ├── tools.py       # Pydantic AI tool definitions (web_search, open_web_page, run_python_code): run_python_code in a disposable isolated subprocess, web_search off the event loop, open_web_page via SSRF-validated streaming fetch with a size cap
│   └── url_security.py # SSRF-safe URL validation for the client-supplied proxy target (LAN ranges allowed) and outbound tool fetches (global-only), with metadata-hostname block and DNS resolution checks
├── static/
│   ├── css/
│   │   ├── base.css         # Structural/layout CSS (theme-agnostic): desktop sidebar collapse, readable trace log fonts
│   │   ├── theme-cyberpunk.css  # Dark theme: neon glow, electric violet palette
│   │   ├── theme-light.css      # Light Professional: sharp/corporate — tight spacing, 3px radius, flat, no glow
│   │   └── theme-warm.css       # Warm Minimal: rounded/editorial — 16px+ radius, dashed borders, cream palette
│   ├── js/
│   │   ├── api.js     # API call utilities
│   │   ├── app.js     # Main entry point; collapsible sidebar toggle
│   │   ├── chat.js    # Chat + metrics + mermaid + attachments + thinking tokens + tool call display (executing/done/error via SSE)
│   │   ├── connection.js  # Connection management + heartbeat
│   │   ├── history.js # Chat session history
│   │   ├── models.js  # Model management
│   │   ├── state.js   # State & localStorage
│   │   ├── trace.js   # SSE client for live trace log streaming
│   │   └── ui.js      # UI utilities + metrics display
│   ├── vendor/        # Pinned vendored frontend libraries (marked 15.0.7, DOMPurify 3.2.4) served at /static/vendor/ so markdown + sanitization work offline
│   └── index.html     # Page structure with collapsible sidebar (chevron tab on desktop, toggle on mobile)
├── tests/
│   ├── __init__.py
│   ├── test_backend.py
│   ├── test_agent.py
│   ├── test_integration.py
│   ├── test_js_syntax.py
│   ├── test_js_runtime.js
│   ├── test_screenshot.py  # Playwright UI screenshot validation
│   ├── test_tools.py
│   └── test_url_security.py
├── .dockerignore      # Build context exclusions for the Docker image
├── Dockerfile         # Minimal production Docker image (Python 3.12 + uv, non-root user)
├── pyproject.toml     # Python project configuration with dependencies
├── package.json       # Node.js project configuration for frontend dependencies
├── package-lock.json  # Node.js dependency lock file
├── run.py             # Entry point; delegates to backend.server.run (Ctrl+C graceful shutdown with 5s force-exit)
└── README.md
```

## Running Tests

```bash
# Python backend tests (unit + integration)
uv run python -m unittest discover tests -v

# JavaScript runtime tests
npm test

# Screenshot validation tests (requires Playwright)
uv run python -m unittest tests.test_screenshot -v
```
