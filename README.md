# LM Studio Console

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
- **Persistent Chat**: Chat messages preserved across model changes and disconnects
- **Settings**: Configurable system prompt and temperature
- **Persistence**: Settings saved to localStorage between sessions
- **Trace Logging**: Detailed request/response logging on the server console
- **Live Trace Log**: Collapsible panel showing real-time server logs streamed via SSE
- **UI Themes**: 3 toggleable themes (Cyberpunk Dark, Light Professional, Warm Minimal) with persistent preference
- **File Attachments**: Upload images, audio, documents for multimodal models
- **Agentic Tools**: Toggle tool calls (web search, open web page, run python code) for LLM-agentic behavior
- **Tool Call Feedback**: Real-time UI feedback showing tool name, arguments, and execution status
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
│   ├── server.py      # FastAPI app with proxy routes, chat endpoint (client disconnect detection, cooperative cancellation), tool endpoints, file upload, trace log SSE, and CORS middleware
│   ├── agent.py       # Pydantic AI Agent wrapper for chat with automatic tool call handling, tool_call_id-based results, thinking tokens, message format conversion, and cooperative cancellation support
│   └── tools.py       # Pydantic AI tool definitions (web_search, open_web_page, run_python_code) with OpenAI-compatible schema generation
├── static/
│   ├── css/
│   │   ├── base.css         # Structural/layout CSS (theme-agnostic)
│   │   ├── theme-cyberpunk.css  # Dark cyberpunk theme variables
│   │   ├── theme-light.css      # Light professional theme variables
│   │   └── theme-warm.css       # Warm minimal theme variables
│   ├── js/
│   │   ├── api.js     # API call utilities
│   │   ├── app.js     # Main entry point
│   │   ├── chat.js    # Chat + metrics + mermaid + attachments + thinking tokens + tool call feedback
│   │   ├── connection.js  # Connection management + heartbeat
│   │   ├── history.js # Chat session history
│   │   ├── models.js  # Model management
│   │   ├── state.js   # State & localStorage
│   │   ├── trace.js   # SSE client for live trace log streaming
│   │   └── ui.js      # UI utilities + metrics display
│   └── index.html     # Page structure
├── tests/
│   ├── __init__.py
│   ├── test_backend.py
│   ├── test_agent.py
│   ├── test_integration.py
│   ├── test_js_syntax.py
│   ├── test_js_runtime.js
│   ├── test_screenshot.py  # Playwright UI screenshot validation
│   └── test_tools.py
├── .dockerignore      # Build context exclusions for the Docker image
├── .pytest_cache/
│   └── README.md      # pytest cache directory marker
├── Dockerfile         # Minimal production Docker image (Python 3.12 + uv, non-root user)
├── pyproject.toml     # Python project configuration with dependencies
├── package.json       # Node.js project configuration for frontend dependencies
├── package-lock.json  # Node.js dependency lock file
├── run.py             # Entry point with graceful shutdown
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
