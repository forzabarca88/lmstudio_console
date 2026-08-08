"""Pydantic AI Agent for chat with tool call support.

Handles the entire tool call loop automatically:
1. Send to LLM → detect tool calls → execute tools → send results back → get final response
2. Streams clean text deltas to the frontend

The frontend sends OpenAI-compatible messages; this module converts them
to Pydantic AI's internal format and runs the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.capabilities import Thinking
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from backend.logger import trace_logger
from backend.tools import _agent


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
            logging.getLogger(__name__).warning(
                f"Unsupported content type: {part_type!r}"
            )
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
    # Map tool_call_id -> tool_name for resolving ToolReturnPart names
    tool_call_map: dict[str, str] = {}

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
                tc_name = tc_func.get("name", "")
                tool_call_map[tc_id] = tc_name
                response_parts.append(ToolCallPart(
                    tool_name=tc_name,
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
            # Resolve tool_name from the map built during assistant processing.
            tool_name = tool_call_map.get(tool_call_id)
            if tool_name is None:
                logging.getLogger(__name__).warning(
                    f"Unresolvable tool_call_id: {tool_call_id!r} — skipping ToolReturnPart"
                )
                continue
            response_parts.append(ToolReturnPart(
                tool_name=tool_name,
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
        cancel_event: asyncio.Event | None = None,
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
            cancel_event: Optional asyncio.Event; when set during streaming,
                the stream is cancelled and a {"__cancelled__": True} payload
                is emitted instead of usage stats.

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
        # If tools are enabled, inherit toolsets from the shared tools agent
        agent = Agent(
            model_obj,
            model_settings=model_settings,
            toolsets=_agent.toolsets if tool_call_enabled else None,
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
        # Declare tracking variables before try so finally can access them
        # even if __aenter__ raises (e.g., connection error)
        saw_thinking = False
        pending_tool_calls: dict[str, dict] = {}
        prev_text: str = ""
        cancelled = False

        try:
            # If already cancelled before starting, emit and stop without
            # sending a request to the model.
            if cancel_event and cancel_event.is_set():
                yield {"__cancelled__": True}
                return

            async with agent.run_stream(
                user_prompt=None,
                message_history=pai_messages,
                capabilities=[Thinking()],
            ) as result:
                async for response in result.stream_response(debounce_by=0.05):
                    for part in response.parts:
                        pname = type(part).__name__
                        if isinstance(part, ToolCallPart):
                            # Tool call detected - emit executing event
                            log.debug(f"PART: {pname} tool={part.tool_name} id={part.tool_call_id} args={part.args!r:.60s}")
                            pending_tool_calls[part.tool_call_id] = {
                                "name": part.tool_name,
                                "args": part.args,
                            }
                            yield {
                                "tool_call": {
                                    "tool_call_id": part.tool_call_id,
                                    "name": part.tool_name,
                                    "args": part.args,
                                    "status": "executing",
                                }
                            }
                        elif isinstance(part, ToolReturnPart):
                            # Tool result from Pydantic AI's internal execution.
                            # Match by tool_call_id to find the corresponding pending call.
                            log.debug(f"PART: {pname} id={part.tool_call_id} content={part.content!r:.60s}")
                            tc_id = part.tool_call_id
                            if tc_id in pending_tool_calls:
                                tc = pending_tool_calls.pop(tc_id)
                                yield {
                                    "tool_result": {
                                        "tool_call_id": tc_id,
                                        "name": tc["name"],
                                        "status": "done",
                                        "result": part.content,
                                    }
                                }
                        elif isinstance(part, ThinkingPartDelta):
                            log.debug(f"PART: {pname} delta={part.content_delta!r:.60s}")
                            saw_thinking = True
                            delta = part.content_delta
                            if delta:
                                yield {"thinking": delta}
                        elif isinstance(part, ThinkingPart):
                            log.debug(f"PART: {pname} content={part.content!r:.60s}")
                            saw_thinking = True
                            # ThinkingPart.content is FULL accumulated text (like TextPart)
                            # Emit thinking_full for every ThinkingPart so frontend updates in real-time
                            if part.content:
                                yield {"thinking_full": part.content}
                        elif isinstance(part, TextPart):
                            # TextPart.content is FULL accumulated text.
                            # Emit only the incremental delta so each SSE event
                            # represents new text (1+ tokens) for accurate metrics.
                            if part.content:
                                delta = part.content[len(prev_text):]
                                prev_text = part.content
                                if delta:
                                    yield {"content": delta}

                    # Check for cancellation after each response batch.
                    # Breaking here exits the async for; the async with
                    # __aexit__ then cancels the underlying HTTP stream.
                    if cancel_event and cancel_event.is_set():
                        cancelled = True
                        break

                if not cancelled:
                    # Emit thinking_done at end of stream if thinking was seen
                    if saw_thinking:
                        yield {"thinking_done": True}

                    # Handle orphaned tool calls (tool was called but result never arrived)
                    for tc_id, tc_info in pending_tool_calls.items():
                        log.warning(
                            f"ORPHANED_TOOL_CALL id={tc_id} name={tc_info['name']}"
                        )
                        yield {
                            "tool_result": {
                                "tool_call_id": tc_id,
                                "name": tc_info["name"],
                                "status": "error",
                                "result": "Tool execution did not complete",
                            }
                        }

            # Exiting the async with block via break triggers __aexit__, which
            # cancels the upstream HTTP stream. Emit a cancellation marker so
            # the frontend can distinguish cancellation from errors.
            if cancelled:
                yield {"__cancelled__": True}
                return

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
            # Handle orphaned tool calls on error (stream was interrupted)
            for tc_id, tc_info in pending_tool_calls.items():
                log.warning(
                    f"ORPHANED_TOOL_CALL id={tc_id} name={tc_info['name']}"
                )
                yield {
                    "tool_result": {
                        "tool_call_id": tc_id,
                        "name": tc_info["name"],
                        "status": "error",
                        "result": "Tool execution did not complete",
                    }
                }
        finally:
            pending_tool_calls.clear()
