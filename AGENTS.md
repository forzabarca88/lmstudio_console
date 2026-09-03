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
│   ├── logger.py            # Trace logging with shared logger, RequestTrace context, cancellation tracking, and SSE streamer integration
│   ├── log_streamer.py      # SSE log streamer: ring buffer (500 entries), subscriber broadcast for live trace log streaming
│   ├── proxy.py             # HTTP proxy using httpx for forwarding requests to LM Studio/OpenAI endpoints with streaming and graceful cancellation support
│   ├── server.py            # FastAPI app with proxy routes, chat endpoint (client disconnect detection, cooperative cancellation), file upload (50MB cap), trace log SSE, CORS middleware, SSRF-safe X-LM-Studio-URL handling, and graceful shutdown (5s force-exit)
│   ├── agent.py             # Pydantic AI Agent wrapper for chat with automatic tool call handling (Pydantic AI internal tool loop), tool_call/tool_result SSE events, thinking tokens, message format conversion, and cooperative cancellation support
│   ├── tools.py             # Pydantic AI tool definitions (web_search, open_web_page, run_python_code): run_python_code in a disposable isolated subprocess, web_search off the event loop, open_web_page via SSRF-validated streaming fetch with a size cap
│   └── url_security.py      # SSRF-safe URL validation for the client-supplied proxy target (LAN ranges allowed) and outbound tool fetches (global-only), with metadata-hostname block and DNS resolution checks
├── static/
│   ├── css/
│   │   ├── base.css         # Structural/layout rules (theme-agnostic): reset, layout, sidebar, chat, forms, buttons, components
│   │   ├── theme-cyberpunk.css  # Cyberpunk Dark: deep slate + electric violet palette; neon glow effects
│   │   ├── theme-light.css      # Light Professional: sharp/corporate — tight spacing, 3px radius, flat, no glow
│   │   └── theme-warm.css       # Warm Minimal: rounded/editorial — 16px+ radius, dashed borders, cream palette
│   ├── favicon.svg          # Browser tab icon
│   ├── js/
│   │   ├── api.js           # API call utilities for proxy requests, streaming, and chat endpoint with optional AbortSignal forwarding
│   │   ├── app.js           # Main entry point wiring all modules together; send button toggles between send and stop/cancel; collapsible sidebar toggle; trace log panel wiring
│   │   ├── chat.js          # Chat: send messages, streaming responses, thinking blocks, tool call display (executing + done/error via SSE events), metrics, file attachments (abortable), copy button, stop/cancel button with partial-content preservation
│   │   ├── connection.js    # Connection management: connect/disconnect (aborts active requests), status updates, heartbeat monitoring
│   │   ├── history.js       # Chat session history: render, continue (aborts active requests), delete sessions (aborts if current)
│   │   ├── models.js        # Model management: list, refresh, load, unload models with LM Studio native API
│   │   ├── state.js         # State management: localStorage persistence for settings/session history; runtime abort controller and reason tracking; theme management with applyTheme
│   │   ├── trace.js         # Live trace log panel: SSE streaming from /api/trace-logs, auto-scroll, pause/resume, exponential backoff reconnect
│   │   └── ui.js            # UI utilities: toast notifications, formatting, auto-resize, scroll, metrics display
│   ├── vendor/              # Pinned vendored frontend libraries (marked 15.0.7, DOMPurify 3.2.4) served at /static/vendor/ so markdown + sanitization work offline
│   └── index.html          # Main HTML page with sidebar (Connection, Models, History, Settings, Trace Log), chat area, and toast container
├── tests/
│   ├── __init__.py          # Empty test package init
│   ├── test_agent.py        # Unit tests for ChatAgent message conversion (incl. multimodal image/audio/file parts), streaming (text, thinking, tool calls), tool_call_id-based result matching, system prompt deduplication, and cooperative cancellation (incl. cancellation during model silence)
│   ├── test_backend.py      # Unit tests for config, logging, and proxy functionality (incl. stream read timeout)
│   ├── test_integration.py  # Integration tests: connect, list/load/unload models, chat, metrics, session management (multi-turn, tool history, multimodal), removed tool endpoints (404), file upload (incl. 50MB cap), CORS, auth, errors, X-LM-Studio-URL SSRF validation, graceful shutdown (SIGINT with 5s force-exit), and request cancellation (chat disconnect, proxy disconnect, cooperative cancel)
│   ├── test_tools.py        # Unit tests for tool execution: run_python_code subprocess (output, stderr, timeout, size cap), open_web_page (fetch, truncation, SSRF rejection, errors), live web_search, and agent toolset wiring
│   ├── test_js_runtime.js   # Node.js runtime tests: state management (defaults, persist, restore, session save with cap incl. audio/file data-URI sanitization, abortActiveRequest), UI utilities, session lifecycle (continue, delete, error handling)
│   ├── test_js_syntax.py    # Syntax validation tests for all JavaScript modules (Node.js --check and reserved word scanning)
│   ├── test_screenshot.py   # Playwright tests: visual rendering (page layout, panels, elements, screenshot pixel validation) and interactive behavioral tests (connect, send message, new chat, settings toggle, model load/unload, copy button, stop button, new chat cancels request, sidebar collapse desktop+mobile, trace log overflow stability), XSS sanitization, and live viewport resize (desktop/tablet/mobile)
│   └── test_url_security.py # Unit tests for the SSRF-safe URL validators (DNS resolution mocked)
├── .dockerignore            # Build context exclusions for the Docker image
├── AGENTS.md                # Project guidelines and documentation
├── Dockerfile               # Minimal production Docker image (Python 3.12 + uv, non-root user)
├── README.md                # Project overview, features, usage, and configuration
├── SPEC.md                  # Technical requirements and minimum test cases
├── pyproject.toml           # Python project configuration with dependencies
├── package.json             # Node.js project configuration for frontend dependencies
├── package-lock.json        # Node.js dependency lock file
└── run.py                   # Entry point; delegates to backend.server.run (Ctrl+C graceful shutdown with 5s force-exit)
```

### Server Restart
`pkill -f uvicorn` is unreliable — always use `kill -9 <pid>` (from `ps aux | grep uvicorn`) to forcefully terminate before restarting.

### Theme Architecture
CSS is split into a theme-agnostic `base.css` (layout, reset, components) and swappable theme files (`theme-cyberpunk.css`, `theme-light.css`, `theme-warm.css`) that define CSS variables for colors, fonts, and accents. The frontend loads `base.css` always and swaps the active theme stylesheet at runtime via `applyTheme()` in `state.js`. Theme preference persists in `localStorage`.

### Pydantic AI Streaming Semantics

`backend/agent.py` consumes Pydantic AI's `run_stream_events()` API and translates event-level deltas to SSE; `static/js/chat.js` accumulates incremental `thinking` and `content` payloads.

- `PartStartEvent(ThinkingPart)` may contain the first thinking text once.
- `PartDeltaEvent(ThinkingPartDelta)` contains incremental thinking text; emit it as `thinking` and accumulate it with frontend `+=`.
- `PartStartEvent(TextPart)` and `PartDeltaEvent(TextPartDelta)` are incremental content sources; frontend accumulates `content` with `+=`.
- Do not emit or log `PartEndEvent` full accumulated content as a new delta; it repeats prior text.

### Tool Call Streaming

`run_stream_events()` emits `FunctionToolCallEvent` and `FunctionToolResultEvent` for the internal tool loop. The backend maps these to `tool_call` (executing) and `tool_result` (done/error) SSE events, matched by `tool_call_id`. The frontend keeps one card per ID, updates arguments in place, and renders the result/status lifecycle.

### Sidebar Collapse

The sidebar is collapsible on all screen sizes. On desktop, a chevron tab on the right edge toggles collapse (sidebar narrows to 20px, content hidden). On mobile, a full-width toggle button at the bottom of the sidebar collapses content vertically. CSS transitions animate both states.

### Trace Log Readability

Trace log entries use readable font sizes: entries at 0.8rem (11.2px), level badges at 0.7rem (9.8px), and control buttons at 0.72rem (10.1px). This ensures the live trace panel is legible at normal reading distance.
