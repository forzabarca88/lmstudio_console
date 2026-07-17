# AGENTS.md

## Core Principles

- **DESIGN AND BUILD PRODUCTION GRADE CODE FROM THE START** - i.e. no monolithic files when they can modularised, no unnecessary duplication of code, no hardcoded values when they can be placed in a centralised config file, etc.
- Keep AGENTS.md minimal - **only** keep information which will be required every time you look at this project. Project structure should **always** be kept up to date, and include a CONCISE single sentence description of each file in the project.  Do not modify `Core Principles`, but review and remove anything else from the document which is not required.
- Do **not** make assumptions without testing and validating first. Follow the scientific method.
- You **must** review your own code as you write as a *Senior Software Engineer*.
- Write the bare minimum of tests - follow **ARRANGE, ACT, ASSERT**. You must test the end result, NOT the implmentation details.
- If you start the application (e.g. for testing), you must validate that the application is stopped before marking the task complete.
- Use mocks for testing sparingly - if the code requires excessive mocking, then redesign the implementation to be easier to test.
- **You must assume that your knowledge is outdated** - always research topics and frameworks before making decisions.

## Important Notes

### Project Structure

```
├── backend/
│   ├── __init__.py          # Empty package init
│   ├── config.py            # Configuration from environment variables (host, port, LM Studio URL, static dir)
│   ├── logger.py            # Trace logging for proxy requests with shared logger and RequestTrace context
│   ├── proxy.py             # HTTP proxy service using httpx for forwarding requests to LM Studio/OpenAI endpoints with streaming support
│   ├── server.py            # FastAPI application with proxy routes, chat endpoint, tool endpoints, file upload, and CORS middleware
│   ├── agent.py             # Pydantic AI Agent wrapper for chat with automatic tool call handling, tool call feedback events, and message format conversion
│   └── tools.py             # Pydantic AI tool definitions (web_search, open_web_page, run_python_code) with OpenAI-compatible schema generation
├── static/
│   ├── css/
│   │   └── style.css        # Complete styling with dark theme, responsive design, animations, and component styles
│   ├── js/
│   │   ├── api.js           # API call utilities for proxy requests, streaming, and chat endpoint
│   │   ├── app.js           # Main entry point wiring all modules together with event bindings
│   │   ├── chat.js          # Chat functionality: send messages, streaming responses, message rendering, metrics, file attachments, tool call feedback
│   │   ├── connection.js    # Connection management: connect/disconnect, status updates, heartbeat monitoring
│   │   ├── history.js       # Chat session history: render, continue, delete sessions
│   │   ├── models.js        # Model management: list, refresh, load, unload models with LM Studio native API
│   │   ├── state.js         # State management and localStorage persistence for settings and session history
│   │   └── ui.js            # UI utilities: toast notifications, formatting, auto-resize, scroll, metrics display
│   └── index.html          # Main HTML page with sidebar, chat area, and all UI elements
├── tests/
│   ├── __init__.py          # Empty test package init
│   ├── test_agent.py        # Unit tests for ChatAgent message conversion, streaming, and tool call handling
│   ├── test_backend.py      # Unit tests for config, logging, and proxy functionality
│   ├── test_integration.py  # Integration tests covering SPEC requirements: connect, list models, load/unload, chat, metrics, history, tools
│   ├── test_tools.py        # Unit tests for tool schema generation and execution
│   ├── test_js_runtime.js   # Node.js runtime tests for state management, API calls, and UI utilities
│   ├── test_js_syntax.py    # Syntax validation tests for all JavaScript modules
│   └── test_screenshot.py   # Playwright screenshot validation tests for UI rendering
├── .pytest_cache/
│   └── README.md            # pytest cache directory marker
├── AGENTS.md                # Project guidelines and documentation
├── README.md                # Project overview, features, usage, and configuration
├── SPEC.md                  # Technical requirements and minimum test cases
├── pyproject.toml           # Python project configuration with dependencies
├── package.json             # Node.js project configuration for frontend dependencies
├── package-lock.json        # Node.js dependency lock file
└── run.py                   # Entry point for starting the server with graceful shutdown
```

### Server Restart
`pkill -f uvicorn` is unreliable — always use `kill -9 <pid>` (from `ps aux | grep uvicorn`) to forcefully terminate before restarting.

### Pydantic AI Streaming Semantics

When working with Pydantic AI's `stream_response()` in `backend/agent.py` and the SSE parsing in `static/js/chat.js`:

- **`ThinkingPartDelta.content_delta`** — incremental delta (each chunk is a *new* piece of text). Frontend must **accumulate** with `+=`.
- **`TextPart.content`** — full accumulated text (each chunk contains *all* text so far). Frontend must **assign** with `=`.

Confusing these two will cause either duplicated content (using `+=` on TextPart) or lost content (using `=` on ThinkingPartDelta).
