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
│   ├── config.py      # Configuration from environment variables
│   ├── logger.py      # Trace logging for proxy requests
│   ├── proxy.py       # HTTP proxy service (httpx)
│   ├── server.py      # FastAPI application + tool/upload endpoints
│   ├── agent.py       # Pydantic AI Agent for chat with tool call feedback
│   └── tools.py       # Pydantic AI tool definitions (web search, open web page, run python code)
├── static/
│   ├── css/
│   │   └── style.css  # Styles
│   ├── js/
│   │   ├── api.js     # API call utilities
│   │   ├── app.js     # Main entry point
│   │   ├── chat.js    # Chat + metrics + mermaid + attachments + thinking tokens + tool call feedback
│   │   ├── connection.js  # Connection management + heartbeat
│   │   ├── history.js # Chat session history
│   │   ├── models.js  # Model management
│   │   ├── state.js   # State & localStorage
│   │   └── ui.js      # UI utilities + metrics display
│   └── index.html     # Page structure
├── tests/
│   ├── test_backend.py
│   ├── test_agent.py
│   ├── test_integration.py
│   ├── test_js_syntax.py
│   ├── test_js_runtime.js
│   ├── test_screenshot.py  # Playwright UI screenshot validation
│   └── test_tools.py
├── run.py             # Entry point with graceful shutdown
├── pyproject.toml
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
