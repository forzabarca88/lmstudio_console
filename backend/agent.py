"""Pydantic AI Agent for chat with tool call support.

Handles the entire tool call loop automatically:
1. Send to LLM → detect tool calls → execute tools → send results back → get final response
2. Streams normalized content, thinking, and tool lifecycle events to the frontend

The frontend sends OpenAI-compatible messages; this module converts them
to Pydantic AI's internal format and runs the agent.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any, AsyncIterable

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.messages import (
    AudioUrl,
    DocumentUrl,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    TextPartDelta,
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

# Audio media types Pydantic AI's OpenAI mapping accepts (it asserts
# wav/mp3 when building the chat-completion input_audio part).
_AUDIO_MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg"})

# Document media types passed through as DocumentUrl; everything else
# degrades to a text placeholder so the model sees something meaningful.
_DOCUMENT_MEDIA_TYPES = frozenset({
    "application/pdf",
    "text/csv",
    "text/markdown",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
})


def _media_type_from_data_uri(s: str) -> str:
    """Extract the media type from a ``data:<mime>;base64,...`` URI.

    Returns "" when ``s`` is not a data URI.
    """
    if not isinstance(s, str) or not s.startswith("data:"):
        return ""
    return s[len("data:"):].split(";", 1)[0]


def _convert_user_content(content: Any) -> list[UserContent]:
    """Convert OpenAI-compatible multimodal content to Pydantic AI UserContent list.

    Handles:
    - Plain string → [TextContent(content=string)]
    - Array of parts → mapped to TextContent / ImageUrl / AudioUrl / DocumentUrl

    ``input_audio`` parts map to AudioUrl only for the formats Pydantic AI's
    OpenAI mapping accepts (wav/mp3); other audio types degrade to a text
    placeholder. ``input_file`` parts inline text/plain payloads as text, map
    known document types to DocumentUrl, and degrade anything else to a text
    placeholder.
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
            elif part_type == "input_audio":
                data_uri = part.get("file_data") or part.get("file") or ""
                media_type = _media_type_from_data_uri(data_uri)
                if media_type in _AUDIO_MEDIA_TYPES:
                    parts.append(AudioUrl(url=data_uri, media_type=media_type))
                else:
                    parts.append(TextContent(
                        content=(
                            f"[Audio attachment ({media_type or 'unknown'})"
                            " — format not supported by the model]"
                        )
                    ))
            elif part_type == "input_file":
                data_uri = part.get("file_data") or part.get("file") or ""
                media_type = _media_type_from_data_uri(data_uri)
                if media_type == "text/plain":
                    payload = data_uri.split(",", 1)[1] if "," in data_uri else ""
                    try:
                        decoded = base64.b64decode(payload).decode("utf-8", "replace")
                    except ValueError:
                        decoded = ""
                    parts.append(TextContent(content=decoded))
                elif media_type in _DOCUMENT_MEDIA_TYPES:
                    parts.append(DocumentUrl(url=data_uri, media_type=media_type))
                else:
                    parts.append(TextContent(
                        content=(
                            f"[File attachment ({media_type or 'unknown'})"
                            " — format not supported by the model]"
                        )
                    ))
            else:
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


def _tool_value_to_text(value: Any) -> str:
    """Return a readable string for a tool result of any supported shape.

    Pydantic AI tools may return strings, mappings, sequences, or multimodal
    content. The browser's tool card is deliberately text-only, so normalize
    structured values here instead of relying on JSON serialization of the
    Pydantic AI dataclasses.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""

    def _json_default(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if hasattr(item, "__dict__"):
            return vars(item)
        return str(item)

    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except (TypeError, ValueError):
        return str(value)


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
    ) -> AsyncIterable[dict[str, Any]]:
        """Stream structured chat events with optional tool calls.

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
            Structured content, thinking, tool lifecycle, cancellation, error,
            and usage payloads for the SSE endpoint.
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

        # Merge system prompts: the explicit system_prompt parameter comes
        # first, followed by any system prompts extracted from `messages`.
        # Deduplicate so a client that sends the same prompt both as
        # system_prompt and as a system message gets a single prompt.
        parts = []
        if system_prompt and system_prompt.strip():
            parts.append(system_prompt.strip())
        for p in extracted_system_prompts:
            p = p.strip()
            if p and p not in parts:
                parts.append(p)
        combined_system = "\n\n".join(parts) if parts else None

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

        # `run_stream_events()` is the event-level API. Unlike
        # `run_stream().stream_response()`, it includes every model turn and
        # the internal function-tool loop. It also gives us true deltas for
        # thinking and text, rather than repeatedly exposing accumulated
        # `ModelResponse` snapshots.
        thinking_open = False
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        run_result = None
        cancelled = False

        def finish_thinking() -> dict[str, bool] | None:
            """Return a boundary event once for the active thinking part."""
            nonlocal thinking_open
            if not thinking_open:
                return None
            thinking_open = False
            return {"thinking_done": True}

        def orphaned_tool_events() -> list[dict[str, Any]]:
            """Create error events for calls without a matching result."""
            events = []
            for tc_id, tc_info in pending_tool_calls.items():
                log.warning(
                    f"ORPHANED_TOOL_CALL id={tc_id} name={tc_info['name']}"
                )
                events.append({
                    "tool_result": {
                        "tool_call_id": tc_id,
                        "name": tc_info["name"],
                        "status": "error",
                        "result": "Tool execution did not complete",
                    }
                })
            return events

        try:
            # If already cancelled before starting, emit and stop without
            # sending a request to the model.
            if cancel_event and cancel_event.is_set():
                yield {"__cancelled__": True}
                return

            async with agent.run_stream_events(
                user_prompt=None,
                message_history=pai_messages,
                capabilities=[Thinking()],
            ) as events:
                # Race each next-event fetch against the cancel signal so a
                # silent upstream (no deltas in flight) is cancelled promptly
                # instead of waiting for the next token to arrive.
                anext_task = None
                while True:
                    if cancel_event is None:
                        try:
                            event = await events.__anext__()
                        except StopAsyncIteration:
                            break
                    else:
                        if anext_task is None:
                            anext_task = asyncio.create_task(events.__anext__())
                        wait_task = asyncio.create_task(cancel_event.wait())
                        done, _ = await asyncio.wait(
                            {anext_task, wait_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if wait_task in done:
                            # Cancellation won the race: stop waiting on the
                            # upstream and exit the event context, which
                            # cancels the background Pydantic AI run and its
                            # upstream HTTP stream.
                            anext_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await anext_task
                            anext_task = None
                            cancelled = True
                            break
                        wait_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await wait_task
                        try:
                            event = anext_task.result()
                        except StopAsyncIteration:
                            anext_task = None
                            break
                        anext_task = None
                    if isinstance(event, PartStartEvent):
                        part = event.part
                        if isinstance(part, ThinkingPart):
                            thinking_open = True
                            # A PartStartEvent contains only the initial
                            # content for this part. Subsequent content is
                            # delivered as ThinkingPartDelta, so it is safe
                            # to emit this once without repetition.
                            if part.content:
                                log.debug(
                                    f"PART: ThinkingPartStart chars={len(part.content)}"
                                )
                                yield {"thinking": part.content}
                        elif isinstance(part, TextPart):
                            if (thinking_done := finish_thinking()) is not None:
                                yield thinking_done
                            if part.content:
                                yield {"content": part.content}
                        # ToolCallPart starts are deliberately not emitted:
                        # the later FunctionToolCallEvent is the single,
                        # validated lifecycle event for a function tool.

                    elif isinstance(event, PartDeltaEvent):
                        delta = event.delta
                        if isinstance(delta, ThinkingPartDelta):
                            thinking_open = True
                            if delta.content_delta:
                                log.debug(
                                    f"PART: ThinkingPartDelta delta={delta.content_delta!r:.60s}"
                                )
                                yield {"thinking": delta.content_delta}
                        elif isinstance(delta, TextPartDelta):
                            if (thinking_done := finish_thinking()) is not None:
                                yield thinking_done
                            if delta.content_delta:
                                yield {"content": delta.content_delta}
                        # ToolCallPartDelta events assemble a call. They are
                        # not sent to the browser because their args may be
                        # incomplete; FunctionToolCallEvent follows once the
                        # call is ready to execute.

                    elif isinstance(event, PartEndEvent):
                        # Do not emit `event.part.content` here: PartEndEvent
                        # contains the full accumulated thinking text and
                        # logging/sending it would reintroduce repetition.
                        if isinstance(event.part, ThinkingPart):
                            if (thinking_done := finish_thinking()) is not None:
                                yield thinking_done

                    elif isinstance(event, FunctionToolCallEvent):
                        call = event.part
                        if (thinking_done := finish_thinking()) is not None:
                            yield thinking_done

                        log.debug(
                            f"PART: FunctionToolCallEvent tool={call.tool_name} "
                            f"id={call.tool_call_id} args={call.args!r:.120s}"
                        )
                        pending_tool_calls[call.tool_call_id] = {
                            "name": call.tool_name,
                            "args": call.args,
                        }
                        yield {
                            "tool_call": {
                                "tool_call_id": call.tool_call_id,
                                "name": call.tool_name,
                                "args": call.args,
                                "status": "executing",
                            }
                        }

                    elif isinstance(event, FunctionToolResultEvent):
                        part = event.part
                        tc_id = part.tool_call_id
                        tc_info = pending_tool_calls.pop(tc_id, {})
                        tool_name = getattr(part, "tool_name", None) or tc_info.get("name", "tool")
                        result_text = _tool_value_to_text(getattr(part, "content", None))
                        is_error = isinstance(part, RetryPromptPart) or (
                            isinstance(part, ToolReturnPart)
                            and part.outcome != "success"
                        )
                        log.debug(
                            f"PART: FunctionToolResultEvent tool={tool_name} "
                            f"id={tc_id} status={'error' if is_error else 'done'}"
                        )
                        yield {
                            "tool_result": {
                                "tool_call_id": tc_id,
                                "name": tool_name,
                                "status": "error" if is_error else "done",
                                "result": result_text,
                            }
                        }

                    elif isinstance(event, AgentRunResultEvent):
                        run_result = event.result

                    # FinalResultEvent and other event types are intentionally
                    # ignored; they mark graph state, not user-visible output.

                    # Check cancellation after each event. Exiting the event
                    # context cancels the background Pydantic AI run and its
                    # upstream HTTP stream.
                    if cancel_event and cancel_event.is_set():
                        cancelled = True
                        break

            if cancelled:
                yield {"__cancelled__": True}
                return

            if (thinking_done := finish_thinking()) is not None:
                yield thinking_done

            for orphaned in orphaned_tool_events():
                yield orphaned

            if run_result is None:
                raise RuntimeError("Agent stream ended without a run result")

            # Log usage only after the complete agent/tool loop has finished.
            usage = run_result.usage
            log.debug(
                f"CHAT_COMPLETE model={model} "
                f"input_tokens={usage.input_tokens} "
                f"output_tokens={usage.output_tokens} "
                f"total_tokens={usage.total_tokens}"
            )

            yield {
                "__usage__": {
                    "prompt_tokens": usage.input_tokens,
                    "completion_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                },
            }
        except Exception as e:
            # Send tool-level failures before the stream-level error so the
            # frontend can update the visible card before it handles the
            # terminal error event.
            for orphaned in orphaned_tool_events():
                yield orphaned
            log.error(f"CHAT_ERROR: {e}")
            yield {"__error__": str(e)}
        finally:
            pending_tool_calls.clear()
