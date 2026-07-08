# SPEC.md

## Goal

Build a remote web management dashboard/console for LM Studio via its API.  
Also include a full featured chat interface which works with all OpenAI compatible endpoints, and connects to the LM Studio endpoint by default.  
It should render messages using Markdown, and the user should be able to input multi-line messages using SHIFT + ENTER for newline.  

The interface must be modern and responsive, with striking design.  


## Technical Requirements

### Backend

- Use Python 3.12.
- Use FastAPI.
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
- Allow tweaking of System Prompt and Temperature for chat.
- Save last used details (e.g. endpoint, model, system prompt, etc) if the user closes the browser.
- Trace logging of all requests shown on the server side console.
- Show metrics for the current chat such as tokens per seconds, time taken for first token, and total tokens.

### User Feedback 

- UI must show feedback to the user for all in-progress states - e.g. connecting, listing model, unloading model, loading model, waiting for chat responses, while streaming chat response until completion, etc.
- Timeouts must be appropriately set - e.g. loading a model can take up to 5 minutes for large models.
- Selecting a different model in the UI or loading/unloading a model should **NOT** clear or remove the existing chat messages. User should be able to continue the existing chat even if a new model is selected.
- Connecting to a Endpoint should indicate if any models are already loaded on that server.

## Reference

- https://lmstudio.ai/docs-md/developer/rest
- https://lmstudio.ai/docs-md/developer/rest/streaming-events
- https://lmstudio.ai/docs-md/developer/openai-compat
