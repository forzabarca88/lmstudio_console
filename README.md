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
- **Session History**: Last 10 chat sessions saved; continue or delete from history
- **Connection Monitoring**: Automatic disconnection detection via heartbeat ping
- **Persistent Chat**: Chat messages preserved across model changes and disconnects
- **Settings**: Configurable system prompt and temperature
- **Persistence**: Settings saved to localStorage between sessions
- **Trace Logging**: Detailed request/response logging on the server console
- **File Attachments**: Upload images, audio, documents for multimodal models
- **Agentic Tools**: Toggle tool calls (e.g. web search) for LLM-agentic behavior
- **Pydantic AI**: Backend tool definitions and execution powered by Pydantic AI

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
│   └── tools.py       # Pydantic AI tool definitions (web search)
├── static/
│   ├── css/
│   │   └── style.css  # Styles
│   ├── js/
│   │   ├── api.js     # API call utilities
│   │   ├── app.js     # Main entry point
│   │   ├── chat.js    # Chat + metrics + mermaid + attachments + tool calls
│   │   ├── connection.js  # Connection management + heartbeat
│   │   ├── history.js # Chat session history
│   │   ├── models.js  # Model management
│   │   ├── state.js   # State & localStorage
│   │   └── ui.js      # UI utilities + metrics display
│   └── index.html     # Page structure
├── tests/
│   ├── test_backend.py
│   ├── test_frontend.py
│   ├── test_integration.py
│   └── test_tools.py
├── run.py             # Entry point
├── pyproject.toml
└── README.md
```

## Running Tests

```bash
uv run python -m unittest discover tests -v
```
