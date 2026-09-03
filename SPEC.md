# SPEC.md

## Goal

Build a remote web management dashboard/console for LM Studio via its API.

Also include a full featured chat interface which works with all OpenAI compatible endpoints, and connects to the LM Studio endpoint by default.

The interface must be modern and responsive, with striking design.


## Technical Requirements

### Backend

- Use Python 3.12.
- Use `FastAPI`.
- Use `Pydantic AI`.
- Use `uv` instead of Python calls.
- Create a minimal `Dockerfile` for production deployment.

### Frontend

- HTML + Javascript only.

### Testing

- Use `unittest` instead of pytest.
- Validate functionality of every interface element which the end user will interact with.
- Front-end testing must be performed (including mobile devices), and you should validate the interface using screenshots.

## Requirements

### Minimum Viable Product

- Implement remote API management capabilities of List Models, Load Model, Unload Model.
- Implement chat functionality with any OpenAI compatible endpoint - models should be populated based on API models list response.
- Render messages in Markdown.
- User should be able to input multi-line messages using SHIFT + ENTER for newline.
- Allow tweaking of System Prompt and Temperature for chat.
- Save last used details (e.g. endpoint, model, system prompt, etc) if the user closes the browser.
- Trace logging of all requests shown on the server side console.
- A collapsable section in the UI which also displays the live trace logging of the application to the user if expanded.
- Show metrics for the current chat such as tokens per seconds, time taken for first token, and total tokens.
- Chat session history should be saved for atleast the last 10 sessions. Allow the user to `Delete` or `Continue` chat sessions saved in history.
- A `Copy` button for each chat response which allows the user to copy the message to their clipboard.
- Responses can render graphs using `Mermaid JS`.
- The button used for sending messages should also double as a stop button for cancelling the current request. Creating a new chat should also implicitly cancel any current in-progress requests. Note that it is **critical** that the request(s) are cancelled on the server/endpoint side, not just on the client/UI side.
- Allow user to attach files, this is important for multimodal models supporting vision, audio, etc.
- `3` UI templates/designs which the user can toggle between - the user's preference must be saved so that the template is used for their future sessions. The design language of the templates **must** be very different from each other, and the implementation must be modular and elegant so that more templates can be added in future.
- Add toggles to allow the user to use agentic functions such as tool calls.
- Required tools:
    - `Web search` tool call so the LLM can search the web.
    - `Open web page` tool call to get contents of specific pages.
    - `Run python code` tool call to execute python code - **ensure that this is done in a safe way to minimise security concerns**.
- Assume that the application runs within a trusted local home network - access to the console must be unauthenticated.

### User Feedback 

- UI must show feedback to the user for all in-progress states - e.g. connecting, listing model, unloading model, loading model, waiting for chat responses, while streaming chat response until completion, etc.
- UI must show feedback to the user for all in-progress tool calls - e.g. if searching the web, running python code, etc.
- Timeouts must be appropriately set - e.g. loading a model can take up to 5 minutes for large models.
- Selecting a different model in the UI or loading/unloading a model should **NOT** clear or remove the existing chat messages. User should be able to continue the existing chat even if a new model is selected.
- When displaying the list of available models, the UI should always show which (if any) models are currently loaded on the server.
- UI `Connected` status should update automatically if the endpoint becomes unavailable at any point while connected.
- When an LLM is thinking, show feedback on the UI. Also allow the thinking content to be expanded to show the thinking tokens streamed in realtime.
- Pressing CTRL + C for the server-side process must kill all connections and stop the server (force stop after 5 seconds).
- UI must have `responsive` design which works across a wide range of screen sizes such as large monitors, laptops, tablets, or mobile phones.
- UI elements other than the core chat window where the user sends and receives messages, should all be collapsable or be able to be hidden.
- Generated messages in the UI (including thinking) should automatically scroll to the latest content while streaming.

### Minimum Test Cases

Assume that following refers to front-end (browser) and back-end (server) testing.  

- Connect to a endpoint.
- List models from the endpoint.
- Select a model from a list of models and load it.
- Select a loaded model and send a message. Verify that a response is sent and rendered in the window correctly. 
- Verify that metrics are updated and render correctly in the UI.
- Verify that a new chat creates a brand new chat, but saves the previous session in the history tab.
- Unload a loaded model.
- Change the system prompt and temperature and ensure that it is correctly sent to the endpoint.
- Load a previous session from history and continue sending a message within that session. Verify that a response is received.
- Verify that saved sessions can be deleted.
- Toggle web search for the session so that the LLM can use the web search tool call.
- Verify trace logs are being shown in the UI.
- Toggle between all templates and verify they look as expected.
- Resize the application window and validate that UI elements are resized, displayed, and work correctly.
- Collapse and expand (or hide/unhide) all UI sections which have this feature, and verify that the UI remains a good user experience.

## Reference

Use these resources to research more information about specific topics.

- https://lmstudio.ai/docs-md/developer/rest
- https://lmstudio.ai/docs-md/developer/rest/streaming-events
- https://lmstudio.ai/docs-md/developer/openai-compat
- https://github.com/mermaid-js/mermaid/blob/develop/README.md
- https://github.com/fastapi/fastapi/blob/master/README.md
- https://github.com/pydantic/pydantic-ai/blob/main/README.md
- https://developers.openai.com/api/reference/resources/chat/index.md

Use the following LIVE LM Studio/OpenAI compatible endpoint for validating testing assumptions:  
```http://192.168.0.5:1234```
