"""Pydantic AI Agent for chat with tool call support.

Handles the entire tool call loop automatically:
1. Send to LLM → detect tool calls → execute tools → send results back → get final response
2. Streams clean text deltas to the frontend

The frontend sends OpenAI-compatible messages; this module converts them
to Pydantic AI's internal format and runs the agent.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterable

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from backend.logger import trace_logger


# --- Tool definitions registered on a shared agent ---

# Shared agent holds tool definitions. Per-request agents are created
# with the correct model and inherit these tools.
_tool_agent = Agent()


@_tool_agent.tool_plain
async def web_search(query: str) -> str:
    """Search the web for information about a given query."""
    from ddgs import DDGS

    log = trace_logger.logger
    log.debug(f"WEB_SEARCH query={query!r}")

    try:
        ddgs = DDGS()
        results = ddgs.text(query, max_results=5)
        entries = []
        for i, r in enumerate(results, 1):
            entries.append(
                f"{i}. {r['title']}\n   {r['href']}\n   {r['body'][:200]}"
            )
        output = "\n\n".join(entries) if entries else "No results found."
        log.debug(f"WEB_SEARCH results={len(results)}")
        return output
    except Exception as e:
        log.error(f"WEB_SEARCH error: {e}")
        return f"Search failed: {e}"


# --- Message conversion ---


def _convert_user_content(content: Any) -> list[UserContent]:
    """Convert OpenAI-compatible multimodal content to Pydantic AI UserContent list.

    Handles:
    - Plain string → [TextContent(content=string)]
    - Array of parts → mapped to TextContent / ImageUrl
    """
    if isinstance(content, str):
        return [TextContent(content=content)]

    if isinstance(content, list):
        parts: list[UserContent] = []
        for part in content:
            part_type = part.get("type", "")
            if part_type == "text":
                parts.append(TextContent(content=part["text"]))
            elif part_type == "image_url":
                url = part["image_url"].get("url", "")
                parts.append(ImageUrl(url=url))
            # Skip unsupported types silently
        return parts if parts else [TextContent(content="")]

    return [TextContent(content=str(content))]


def _openai_messages_to_pai_messages(
    messages: list[dict],
) -> tuple[list[ModelMessage], list[str]]:
    """Convert OpenAI-compatible messages to Pydantic AI ModelMessages.

    OpenAI messages have {role, content, ...} format.
    Pydantic AI uses ModelRequest (for user/system) and ModelResponse (for assistant/tool).

    The conversion groups consecutive request messages (user) and
    response messages (assistant/tool) into proper ModelMessage objects.
    System prompts are extracted and returned separately to avoid
    duplicate system messages when combined with the system_prompt parameter.
    This ensures the system message is always first, which is required
    by some model chat templates (e.g. LM Studio Jinja2 templates).

    Returns:
        Tuple of (converted messages list, extracted system prompt texts).
    """
    pai_messages: list[ModelMessage] = []
    system_prompts: list[str] = []
    request_parts = []
    response_parts = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            # Extract system prompts separately instead of including in request_parts.
            # They will be merged with the system_prompt parameter in chat().
            if isinstance(content, str):
                if content:
                    system_prompts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text" and part.get("text"):
                        system_prompts.append(part["text"])
            # Flush any pending response parts
            if response_parts:
                pai_messages.append(ModelResponse(parts=response_parts))
                response_parts = []

        elif role == "user":
            # Flush any pending response parts
            if response_parts:
                pai_messages.append(ModelResponse(parts=response_parts))
                response_parts = []

            user_parts = _convert_user_content(content)
            request_parts.append(UserPromptPart(content=user_parts))

        elif role == "assistant":
            # Flush any pending request parts
            if request_parts:
                pai_messages.append(ModelRequest(parts=request_parts))
                request_parts = []

            if isinstance(content, str) and content:
                response_parts.append(TextPart(content=content))

            # Handle tool_calls from assistant
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                tc_func = tc.get("function", {})
                response_parts.append(ToolCallPart(
                    tool_name=tc_func.get("name", ""),
                    args=tc_func.get("arguments", "{}"),
                    tool_call_id=tc_id,
                ))

        elif role == "tool":
            # Flush any pending request parts
            if request_parts:
                pai_messages.append(ModelRequest(parts=request_parts))
                request_parts = []

            tool_result = msg.get("content", "")
            tool_call_id = msg.get("tool_call_id", "")
            response_parts.append(ToolReturnPart(
                tool_name="",
                content=tool_result,
                tool_call_id=tool_call_id,
            ))

    # Flush remaining parts
    if request_parts:
        pai_messages.append(ModelRequest(parts=request_parts))
    if response_parts:
        pai_messages.append(ModelResponse(parts=response_parts))

    return pai_messages, system_prompts


# --- Chat agent ---


class ChatAgent:
    """Pydantic AI Agent wrapper for chat with tool call support.

    Creates a per-request agent with the correct model and optionally
    inherits tools from the shared tool agent.
    """

    def __init__(self, base_url: str, api_key: str = ""):
        # OpenAI-compatible API is at /v1 (OpenAI client appends paths to base_url)
        openai_base = base_url.rstrip("/") + "/v1"
        provider = OpenAIProvider(base_url=openai_base, api_key=api_key or "lm-studio-key")
        self._provider = provider

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        system_prompt: str = "",
        tool_call_enabled: bool = False,
    ) -> AsyncIterable[str]:
        """Stream chat response with optional tool calls.

        The agent handles tool calls automatically:
        1. Sends messages to LLM
        2. If LLM requests tool calls, executes them
        3. Sends tool results back to LLM
        4. Streams the final response text deltas

        Args:
            model: Model name to use.
            messages: OpenAI-compatible messages list.
            temperature: Sampling temperature.
            system_prompt: System prompt (prepended to messages).
            tool_call_enabled: Whether to enable tool calls.

        Yields:
            Text deltas as they are generated, followed by usage metadata.
        """
        log = trace_logger.logger
        log.debug(
            f"CHAT model={model} temp={temperature} "
            f"system_prompt={system_prompt!r:.60s} "
            f"tool_call_enabled={tool_call_enabled} "
            f"messages={len(messages)}"
        )

        # Convert messages to Pydantic AI format.
        # System prompts from the messages list are extracted separately to avoid
        # duplicates when combined with the system_prompt parameter.
        pai_messages, extracted_system_prompts = _openai_messages_to_pai_messages(messages)

        # Merge system prompts: explicit parameter takes priority, append extracted ones.
        combined_system = system_prompt.strip() if system_prompt.strip() else None
        if extracted_system_prompts:
            if combined_system:
                combined_system += "\n\n" + "\n\n".join(extracted_system_prompts)
            else:
                combined_system = "\n\n".join(extracted_system_prompts)

        # Build model for this request
        model_obj = OpenAIChatModel(model, provider=self._provider)

        # Build model settings
        model_settings = ModelSettings(temperature=temperature)

        # Create per-request agent
        # If tools are enabled, inherit toolsets from the shared tool agent
        agent = Agent(
            model_obj,
            model_settings=model_settings,
            toolsets=_tool_agent.toolsets if tool_call_enabled else None,
        )

        # Prepend combined system prompt if any exists.
        # This ensures the system message is always first in the message list,
        # which is required by some model chat templates (e.g. LM Studio Jinja2).
        if combined_system:
            pai_messages.insert(
                0,
                ModelRequest(parts=[SystemPromptPart(content=combined_system)]),
            )

        # Run agent with streaming
        try:
            async with agent.run_stream(
                user_prompt=None,
                message_history=pai_messages,
            ) as result:
                # stream_text(delta=True) yields incremental text deltas
                async for delta in result.stream_text(delta=True, debounce_by=0.05):
                    yield {"content": delta}

            # Log usage
            usage = result.usage
            log.debug(
                f"CHAT_COMPLETE model={model} "
                f"input_tokens={usage.input_tokens} "
                f"output_tokens={usage.output_tokens} "
                f"total_tokens={usage.total_tokens}"
            )

            # Yield usage info as structured data
            yield {
                "__usage__": {
                    "prompt_tokens": usage.input_tokens,
                    "completion_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                },
            }
        except Exception as e:
            log.error(f"CHAT_ERROR: {e}")
            yield {"__error__": str(e)}
