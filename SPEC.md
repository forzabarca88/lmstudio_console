# SPEC.md

## Goal

Build a remote web management dashboard/console for LM Studio via its API.  
Also include a full featured chat interface which works with all OpenAI compatible endpoints, and connects to the LM Studio endpoint by default.  

The interface must be modern and responsive, with striking design.  


## Technical Requirements

### Backend

- Use Python 3.12.
- Use `FastAPI`.
- Use `Pydantic AI`
- Use `uv` instead of Python calls.

### Frontend

- HTML + Javascript only.

### Testing

- Use `unittest` instead of pytest.
- Validate functionality of every interface elements which the end user will interact with.

## Requirements

### Minimum Viable Product

- Implement remote API management capabilities of List Models, Load Model, Unload Model.
- Implement chat functionality with any OpenAI compatible endpoint - models should be populated based on API models list response.
- Render messages in Markdown.
- User should be able to input multi-line messages using SHIFT + ENTER for newline.
- Allow tweaking of System Prompt and Temperature for chat.
- Save last used details (e.g. endpoint, model, system prompt, etc) if the user closes the browser.
- Trace logging of all requests shown on the server side console.
- Show metrics for the current chat such as tokens per seconds, time taken for first token, and total tokens.
- Chat session history should be saved for atleast the last 10 sessions. Allow the user to `Delete` or `Continue` chat sessions saved in history.
- A `Copy` button for each chat response which allows the user to copy the message to their clipboard.
- Responses can render graphs using `Mermaid JS`.
- Allo user to attach files, this is important for multimodal models supporting vision, audio, etc.
- Add toggles to allow the user to use agentic functions such as tool calls - initially add a web search tool call so the LLM can search the web.

### User Feedback 

- UI must show feedback to the user for all in-progress states - e.g. connecting, listing model, unloading model, loading model, waiting for chat responses, while streaming chat response until completion, etc.
- Timeouts must be appropriately set - e.g. loading a model can take up to 5 minutes for large models.
- Selecting a different model in the UI or loading/unloading a model should **NOT** clear or remove the existing chat messages. User should be able to continue the existing chat even if a new model is selected.
- When displaying the list of available models, the UI should always show which (if any) models are currently loaded on the server.
- UI `Connected` status should update automatically if the endpoint becomes unavailable at any point while connected.

## Reference

Use these links if you require information about specific topics.

- https://lmstudio.ai/docs-md/developer/rest
- https://lmstudio.ai/docs-md/developer/rest/streaming-events
- https://lmstudio.ai/docs-md/developer/openai-compat
- https://github.com/mermaid-js/mermaid
- https://github.com/fastapi/fastapi
- https://github.com/pydantic/pydantic-ai
- https://developers.openai.com/api/reference/resources/chat/index.md
