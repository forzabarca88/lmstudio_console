# PLAN.md

Step-by-step plan for aligning the codebase with [SPEC.md](SPEC.md).

## Phase 1: Missing MVP Features

### 1.1 Implement `run_python_code` tool (SPEC: MVP Required)

**What:** Add a third tool that executes Python code safely.

**Why:** SPEC requires `run_python_code` tool call with safety measures to minimise security concerns.

**Implementation:**
- Add `run_python_code(code: str) -> str` to `backend/tools.py` using `@_agent.tool_plain`
- Safety measures:
  - Use `restrictedpython` or a sandboxed `exec()` with a restricted `__builtins__` (no `import`, `open`, `eval`, `exec`, `compile`, `getattr`, `__import__`, etc.)
  - Redirect `stdout` and `stderr` to capture output
  - Enforce a timeout (e.g., 10 seconds) using `signal.alarm` on Linux or a threading-based timeout
  - Limit code length (e.g., 10KB max)
  - No network access in the sandboxed environment
- Register the tool on the shared `_agent` in `backend/tools.py` so it appears in `get_tool_schemas()`
- The tool is already used by `backend/agent.py` via `_tool_agent.toolsets` — but since tools are duplicated between `tools.py` and `agent.py`, this will be addressed in Phase 2

**Files to modify:** `backend/tools.py`

**Tests to add:**
- Unit test in `tests/test_tools.py`: verify schema has `code` parameter, verify execution returns output, verify safety restrictions (blocked builtins raise error), verify timeout enforcement
- Add to `tests/test_integration.py`: test `/api/tool-exec` endpoint with `run_python_code`

---

## Phase 2: Backend Refactoring (Remove Duplication)

### 2.1 Deduplicate tool definitions between `tools.py` and `agent.py`

**What:** Tools are defined twice — once in `backend/tools.py` (on `_agent`) and again in `backend/agent.py` (on `_tool_agent`). Both define `web_search` and `open_web_page` identically.

**Why:** Duplication is error-prone and violates the AGENTS.md principle of no unnecessary duplication. The `agent.py` file should import tools from `tools.py`.

**Implementation:**
- Remove `web_search`, `open_web_page`, `_html_to_text`, and `_tool_agent` from `backend/agent.py`
- Import the shared `_agent` from `backend/tools.py` in `backend/agent.py`
- Update `agent.py` to use `_tool_agent` imported from `tools.py` for `toolsets`
- Add `run_python_code` tool to `backend/tools.py` only (from Phase 1.1)
- Update `backend/agent.py` imports accordingly

**Files to modify:** `backend/agent.py`, `backend/tools.py`

**Tests affected:** `tests/test_agent.py` (imports from agent.py), `tests/test_tools.py` — verify both still pass

---

## Phase 3: User Feedback for Tool Calls (SPEC: User Feedback)

### 3.1 Backend: Emit tool call events in the Pydantic AI chat stream

**What:** The backend `agent.chat()` method handles tool calls internally but never emits tool call events to the frontend. The frontend has CSS for `.tool-call` but no JS code to render them.

**Why:** SPEC requires "UI must show feedback to the user for all in-progress tool calls — e.g. if searching the web, running python code, etc."

**Implementation in `backend/agent.py`:**
- In the `stream_response()` loop, detect `ToolCallPart` events
- When a `ToolCallPart` appears, yield a `{"tool_call": {"name": ..., "args": ...}}` event
- After the tool executes, yield a `{"tool_result": {"name": ..., "result": ..., "status": "done"}}` event
- For tool execution in progress, yield `{"tool_call": {"name": ..., "status": "executing"}}` before the tool runs, then `{"tool_result": ...}` after

**Implementation in `static/js/chat.js`:**
- In the SSE parsing loop, handle `tool_call` and `tool_result` events
- On `tool_call` with `status: "executing"`: create a tool-call DOM element showing the tool name, args, and an animated "executing" status
- On `tool_result`: update the existing tool-call element to show "done" status and the result text
- Reuse the `.tool-call` CSS styles already defined in `style.css`

**Files to modify:** `backend/agent.py`, `static/js/chat.js`

**Tests to add:**
- In `tests/test_agent.py`: test that tool call events are emitted in the stream
- In `tests/test_integration.py`: test `/api/chat` endpoint with `toolCallEnabled: true` and verify tool call events appear in SSE stream
- Screenshot test (Phase 5): verify tool call UI renders correctly

---

## Phase 4: Graceful Shutdown (SPEC: User Feedback)

### 4.1 Handle SIGINT (Ctrl+C) for graceful server shutdown

**What:** Pressing Ctrl+C must kill all connections and stop the server (force stop after 5 seconds).

**Why:** SPEC requires "Pressing CTRL + C for the server-side process must kill all connections and stop the server (force stop after 5 seconds)."

**Implementation in `backend/server.py`:**
- Register `signal.SIGINT` and `signal.SIGTERM` handlers in `run()`
- On first signal: set a shutdown flag, start a 5-second timer, begin closing connections
- Close the shared httpx client via `proxy.close_client()`
- On 5-second timeout: force exit with `os._exit(1)`
- Print shutdown message to console

**Implementation in `run.py`:**
- Import `signal` and `os`
- Wrap `uvicorn.run()` with signal handling, or delegate to server.py's `run()`

**Files to modify:** `backend/server.py`, `run.py`

**Tests to add:**
- In `tests/test_integration.py`: test that SIGINT triggers graceful shutdown (mock signal handling)

---

## Phase 5: Frontend Screenshot Tests (SPEC: Testing)

### 5.1 Create Playwright screenshot validation tests

**What:** SPEC requires "Front-end testing must be included, and you should validate the interface using screenshots."

**Why:** Current frontend tests (`test_js_runtime.js`) only test logic in Node.js with mocked DOM. They don't validate the actual rendered UI.

**Implementation:**
- Create `tests/test_screenshot.py` using Playwright
- Fix `package.json`: change `"type": "commonjs"` to `"type": "module"` so ES module imports work in Node.js, and fix the test script
- Install Playwright browsers: `npx playwright install`
- Tests cover SPEC minimum test cases:
  1. **Connect to endpoint** — screenshot shows connected status, model list populated
  2. **List models** — screenshot shows model list with loaded/unloaded badges
  3. **Load model** — screenshot shows loading indicator, then loaded badge
  4. **Send message and receive response** — screenshot shows rendered markdown response
  5. **Metrics display** — screenshot shows tokens/s, first token time, total tokens
  6. **New chat** — screenshot shows cleared chat, previous session in history
  7. **Unload model** — screenshot shows unloading indicator, preserved chat messages
  8. **System prompt and temperature** — screenshot shows settings panel with changed values
  9. **Continue session from history** — screenshot shows restored messages
  10. **Delete session from history** — screenshot shows session removed
  11. **Toggle web search tool calls** — screenshot shows toggle on, tool call feedback in chat
  12. **Thinking tokens** — screenshot shows expandable thinking block
  13. **File attachment** — screenshot shows attachment preview and inline attachment in message
  14. **Copy button** — screenshot shows copy button on assistant messages
  15. **Mermaid diagrams** — screenshot shows rendered mermaid graph
  16. **Graceful shutdown** — verify server stops on Ctrl+C

**Implementation approach:**
- Start the server in a subprocess for each test
- Use Playwright to navigate, interact, and take screenshots
- Compare screenshots against baseline (generate baselines first run)
- Use `expect(page).to_haveScreenshot()` for visual regression
- Tests run against a mock LM Studio endpoint (mocked responses, no real server needed) or the live endpoint `http://192.168.0.5:1234`

**Files to create:** `tests/test_screenshot.py`

**Files to modify:** `package.json` (fix `"type"` and `"scripts"`), `pyproject.toml` (add playwright as dev dependency)

---

## Phase 6: Fix Existing Issues

### 6.1 Fix `package.json` type mismatch

**What:** `package.json` has `"type": "commonjs"` but all JS modules use ES module syntax (`import`/`export`).

**Why:** This breaks `node tests/test_js_runtime.js` which uses ES module imports.

**Implementation:**
- Change `"type": "commonjs"` to `"type": "module"` in `package.json`
- Update test script: `"test": "node tests/test_js_runtime.js"`

**Files to modify:** `package.json`

### 6.2 Fix `package.json` test script

**What:** Current test script is `"test": "echo \"Error: no test specified\" && exit 1"`

**Why:** Should run the actual JS runtime tests.

**Implementation:**
- Set `"test": "node tests/test_js_runtime.js"`

**Files to modify:** `package.json`

---

## Phase 7: Update AGENTS.md and README.md

### 7.1 Update AGENTS.md project structure

**What:** Update the project structure section to reflect any new files created (e.g., `tests/test_screenshot.py`).

**Files to modify:** `AGENTS.md`

### 7.2 Update README.md

**What:** Update features list to include `run_python_code` tool and screenshot testing. Update project structure section.

**Files to modify:** `README.md`

---

## Execution Order

1. **Phase 6** — Fix `package.json` (unblocks JS runtime tests)
2. **Phase 2** — Deduplicate tools (clean backend before adding new tool)
3. **Phase 1** — Add `run_python_code` tool (MVP requirement)
4. **Phase 3** — Tool call feedback in UI (User Feedback requirement)
5. **Phase 4** — Graceful shutdown (User Feedback requirement)
6. **Phase 5** — Screenshot validation tests (Testing requirement)
7. **Phase 7** — Update documentation

## Summary of Gaps Found

| SPEC Requirement | Status | Phase |
|---|---|---|
| Run python code tool | ❌ Missing | Phase 1 |
| Tool call feedback in UI | ❌ Missing | Phase 3 |
| Ctrl+C graceful shutdown | ❌ Missing | Phase 4 |
| Screenshot validation tests | ❌ Missing | Phase 5 |
| Duplicate tool definitions | ⚠️ Duplication | Phase 2 |
| package.json type mismatch | ❌ Broken | Phase 6 |
| package.json test script | ❌ Stub | Phase 6 |
