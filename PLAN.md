# PLAN.md — Codebase Update to Match SPEC.md

## Gap Analysis

The codebase is now **100% aligned** with SPEC.md. All gaps have been resolved.

| # | SPEC Requirement | Status | Location |
|---|---|---|---|
| 1 | "Open web page" tool call | ✅ Done | `backend/tools.py`, `backend/agent.py` |
| 2 | Thinking tokens — UI feedback + expandable realtime stream | ✅ Done | `static/js/chat.js`, `backend/agent.py`, `static/css/style.css` |
| 3 | README references `test_frontend.py` | ✅ Fixed | `README.md` |
| 4 | Test coverage: Pydantic AI chat streaming + metrics in integration tests | ✅ Done | `tests/test_integration.py`, `tests/test_agent.py` |

Everything (MVP features, user feedback, persistence, Mermaid, file attachments, tool calls, session history, trace logging, thinking tokens) is implemented.

---

## Phase 1 — Add "Open Web Page" Tool ✅ DONE

**Goal:** Implement the second required agentic tool per SPEC.

### Step 1.1 — Add `open_web_page` tool to `backend/tools.py` ✅

- Added `open_web_page(url: str) -> str` async function
- Uses `httpx` to fetch the page
- Extracts readable text from HTML using stdlib `HTMLParser`
- Enforces 30s timeout and 50KB size limit
- Returns cleaned text content
- Also added to `backend/agent.py` for tool call support in chat

### Step 1.2 — Verify tool schema exposes correctly ✅

- `get_tool_schemas()` already iterates all registered tools
- `open_web_page` appears in `GET /api/tools` response

### Step 1.3 — Add test in `tests/test_tools.py` ✅

- `test_open_web_page_schema` — verifies schema has `url` parameter
- `test_execute_open_web_page` — mocks HTTP fetch and verifies result
- `test_execute_open_web_page_error` — verifies graceful error handling

### Step 1.4 — Add integration test in `tests/test_integration.py` ✅

- `test_execute_open_web_page` — verifies `/api/tool-exec` accepts `open_web_page`
- `test_list_tools` — verifies both `web_search` and `open_web_page` appear

---

## Phase 2 — Thinking Tokens Display ✅ DONE

**Goal:** Show "LLM is thinking" feedback with expandable thinking content streamed in realtime.

### Step 2.1 — Backend: Stream thinking tokens from Pydantic AI agent ✅

**File:** `backend/agent.py`

- Switched from `stream_text()` to `stream_response()` to access all response parts
- Detects `ThinkingPartDelta` for incremental thinking content
- Detects `ThinkingPart` for complete thinking (signals thinking done)
- Yields `{"thinking": "..."}` for thinking deltas
- Yields `{"thinking_done": True}` when thinking completes
- Yields `{"content": "..."}` for text content
- Gracefully handles models that don't support thinking (emits thinking_done after text)

### Step 2.2 — Frontend: Parse thinking tokens in SSE stream ✅

**File:** `static/js/chat.js`

- Detects `{"thinking": "..."}` payloads in SSE stream
- Accumulates thinking text separately from content text
- Shows "Thinking..." indicator in streaming indicator area
- On `{"thinking_done": true}`, renders thinking content as collapsible block before assistant response

### Step 2.3 — Frontend: Render expandable thinking block ✅

**File:** `static/js/chat.js`, `static/css/style.css`

- Renders `<details>` element with summary "💭 Thinking" and thinking text inside
- Inserted before the assistant message element
- Styled with amber/coral tones to differentiate from normal responses

### Step 2.4 — Add CSS for thinking block ✅

**File:** `static/css/style.css`

- `.thinking-block` — collapsible container with subtle background
- `.thinking-content` — monospace font, muted color, max-height with scroll
- `.thinking-summary` — clickable summary with thinking icon

### Step 2.5 — Update `static/index.html` ✅

No structural changes needed — thinking blocks are appended dynamically.

### Step 2.6 — Add tests ✅

**File:** `tests/test_agent.py`
- `test_streams_thinking_tokens` — verifies thinking deltas, thinking_done, then content
- `test_thinking_done_without_thinking` — verifies thinking_done emitted when model has no thinking support

**File:** `tests/test_integration.py`
- `test_chat_streams_thinking_tokens` — verifies SSE stream contains thinking payloads

---

## Phase 3 — README Corrections ✅ DONE

**Goal:** Fix documentation to match actual file structure.

### Step 3.1 — Update `README.md` ✅

- Updated project structure tree:
  - Added `backend/agent.py` to backend section
  - Updated `tools.py` description to include "open web page"
  - Updated `chat.js` description to include "thinking tokens"
  - Changed `test_frontend.py` → `test_agent.py`, `test_js_syntax.py`, `test_js_runtime.js` in tests section
- Added "Thinking Tokens" to Features list

---

## Phase 4 — Test Coverage Gaps ✅ DONE

**Goal:** Ensure integration tests cover Pydantic AI chat flow (not just proxy flow).

### Step 4.1 — Add Pydantic AI chat integration test ✅

**File:** `tests/test_integration.py`

- `test_chat_streams_content_and_usage` — mocks agent's `chat()` to return controlled deltas
- Verifies SSE stream contains `{"content": "..."}` payloads
- Verifies `{"__usage__": {...}}` metadata is present at end of stream

### Step 4.2 — Add thinking tokens integration test ✅

**File:** `tests/test_integration.py`

- `test_chat_streams_thinking_tokens` — mocks agent to produce thinking tokens
- Verifies `{"thinking": "..."}` payloads appear in SSE stream
- Verifies `{"thinking_done": true}` marker follows thinking

---

## Execution Order

1. **Phase 1** ✅ — Add "Open web page" tool
2. **Phase 2** ✅ — Thinking tokens display
3. **Phase 3** ✅ — README corrections
4. **Phase 4** ✅ — Test coverage gaps

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Pydantic AI API changes for thinking tokens | Researched `pydantic-ai` 2.9.0 API; used `stream_response()` with `ThinkingPart`/`ThinkingPartDelta` |
| `open_web_page` fetching large/slow pages | Enforced timeout (30s) and response size limit (50KB) |
| Thinking tokens not supported by all models | Emits `thinking_done` marker regardless; frontend gracefully handles no thinking content |
