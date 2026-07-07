# SPEC.md

## Goal

Build a remote web management dashboard/console for LM Studio via its API.  
Also include a full featured chat interface which works with all OpenAI compatible endpoints, and connects to the LM Studio endpoint by default.  
It should render messages using Markdown, and the user should be able to input multi-line messages using SHIFT + ENTER for newline.  

The interface must be modern and responsive, with striking design.  


## Technical Requirements

### Backend

- Use Python 3.12.
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
- Allow tweaking of System Prompt and temperature for chat.
- Save last used details (e.g. endpoint, model, system prompt, etc) if the user closes the browser.
- Trace logging of all requests shown on the server side console.

## Reference

- https://lmstudio.ai/docs-md/developer/rest
- https://lmstudio.ai/docs-md/developer/rest/streaming-events
- https://lmstudio.ai/docs-md/developer/openai-compat
