# AGENTS.md

## Core Principles

- **DESIGN AND BUILD PRODUCTION GRADE CODE FROM THE START** - i.e. no monolithic files when they can modularised, no unnecessary duplication of code, no hardcoded values when they can be placed in a centralised config file, etc.
- Keep AGENTS.md minimal - **only** keep information which will be required every time you look at this project. Project structure should always be up to date, and include a CONCISE single sentence description of each file in the project.  Do not modify `Core Principles`, but review and remove anything else from the document which is not required.
- You **must** review your own code as you write as a *Senior Software Engineer*.
- Write the bare minimum of tests - follow **ARRANGE, ACT, ASSERT**. You must test the end result, NOT the implmentation details.
- If you start the application (e.g. for testing), you must validate that the application is stopped before marking the task complete.
- Use mocks for testing sparingly - if the code requires excessive mocking, then redesign the implementation to be easier to test.
- **You must assume that your knowledge is outdated** - always research topics and frameworks before making decisions.

## Important Notes

### Project Structure

### Pydantic AI Streaming Semantics

When working with Pydantic AI's `stream_response()` in `backend/agent.py` and the SSE parsing in `static/js/chat.js`:

- **`ThinkingPartDelta.content_delta`** — incremental delta (each chunk is a *new* piece of text). Frontend must **accumulate** with `+=`.
- **`TextPart.content`** — full accumulated text (each chunk contains *all* text so far). Frontend must **assign** with `=`.

Confusing these two will cause either duplicated content (using `+=` on TextPart) or lost content (using `=` on ThinkingPartDelta).
