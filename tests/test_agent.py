"""Agent module tests.

Tests the ChatAgent's observable behaviour:
- Message conversion from OpenAI format to Pydantic AI format
- Streaming chat responses (with and without tools)
- Tool call execution and result handling

Validated against live LM Studio endpoint (192.168.0.5:1234).
"""

import unittest
import os
import sys
import json
import base64
import asyncio
import contextlib
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.agent import (
    ChatAgent,
    _openai_messages_to_pai_messages,
    _convert_user_content,
)
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    UserPromptPart,
    TextPart,
    TextPartDelta,
    TextContent,
    ThinkingPart,
    ThinkingPartDelta,
    ImageUrl,
    AudioUrl,
    DocumentUrl,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)


class TestMessageConversion(unittest.TestCase):
    """OpenAI messages convert correctly to Pydantic AI format."""

    def test_single_user_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(pai_messages), 1)
        self.assertIsInstance(pai_messages[0], ModelRequest)
        self.assertIsInstance(pai_messages[0].parts[0], UserPromptPart)
        self.assertEqual(system_prompts, [])

    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        # System prompt is extracted separately, not included in ModelRequest parts
        self.assertEqual(len(pai_messages), 1)
        self.assertEqual(len(pai_messages[0].parts), 1)
        self.assertIsInstance(pai_messages[0].parts[0], UserPromptPart)
        # System prompt collected separately
        self.assertEqual(system_prompts, ["You are helpful"])

    def test_user_assistant_conversation(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(pai_messages), 2)
        self.assertIsInstance(pai_messages[0], ModelRequest)
        self.assertIsInstance(pai_messages[1], ModelResponse)
        self.assertEqual(pai_messages[1].parts[0].content, "Hi there!")
        self.assertEqual(system_prompts, [])

    def test_tool_call_empty_content(self):
        """ARRANGE: Assistant with empty content + tool calls
        ACT: Convert
        ASSERT: ToolCallPart comes first (empty content skipped)"""
        messages = [
            {"role": "user", "content": "Search for Python"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "web_search", "arguments": '{"query": "Python"}'}}
            ]},
            {"role": "tool", "content": "Python is a programming language", "tool_call_id": "call_1"},
        ]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(pai_messages), 2)
        self.assertIsInstance(pai_messages[0], ModelRequest)
        self.assertIsInstance(pai_messages[1], ModelResponse)
        parts = pai_messages[1].parts
        # Empty content string is skipped; ToolCallPart comes first
        self.assertIsInstance(parts[0], ToolCallPart)
        self.assertEqual(parts[0].tool_call_id, "call_1")
        self.assertEqual(parts[0].tool_name, "web_search")
        self.assertIsInstance(parts[1], ToolReturnPart)
        self.assertEqual(parts[1].tool_call_id, "call_1")
        self.assertEqual(parts[1].content, "Python is a programming language")
        self.assertEqual(system_prompts, [])

    def test_tool_call_with_content(self):
        """ARRANGE: Assistant with content AND tool calls
        ACT: Convert
        ASSERT: TextPart followed by ToolCallPart"""
        messages = [
            {"role": "user", "content": "Search for Python"},
            {"role": "assistant", "content": "Let me search for that.", "tool_calls": [
                {"id": "call_1", "function": {"name": "web_search", "arguments": '{"query": "Python"}'}}
            ]},
            {"role": "tool", "content": "Python results", "tool_call_id": "call_1"},
        ]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(pai_messages), 2)
        parts = pai_messages[1].parts
        self.assertIsInstance(parts[0], TextPart)
        self.assertEqual(parts[0].content, "Let me search for that.")
        self.assertIsInstance(parts[1], ToolCallPart)
        self.assertEqual(parts[1].tool_call_id, "call_1")
        self.assertIsInstance(parts[2], ToolReturnPart)
        self.assertEqual(system_prompts, [])

    def test_multimodal_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        pai_messages, system_prompts = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(pai_messages), 1)
        part = pai_messages[0].parts[0]
        self.assertIsInstance(part, UserPromptPart)
        self.assertIsInstance(part.content[0], TextContent)
        self.assertEqual(part.content[0].content, "What is in this image?")
        self.assertIsInstance(part.content[1], ImageUrl)
        self.assertEqual(system_prompts, [])


class TestUserContentConversion(unittest.TestCase):
    """User content converts correctly."""

    def test_string_to_text_content(self):
        result = _convert_user_content("Hello")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(result[0].content, "Hello")

    def test_array_with_text_and_image(self):
        content = [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
        ]
        result = _convert_user_content(content)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(result[0].content, "Describe this")
        self.assertIsInstance(result[1], ImageUrl)
        self.assertEqual(result[1].url, "http://example.com/img.png")

    def test_input_audio_wav_to_audio_url(self):
        data_uri = "data:audio/wav;base64,UklGRiQAAABXRUJQVlA4TlNFQIA="
        result = _convert_user_content(
            [{"type": "input_audio", "file_data": data_uri}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AudioUrl)
        self.assertEqual(result[0].url, data_uri)
        self.assertEqual(result[0].media_type, "audio/wav")

    def test_input_audio_mpeg_to_audio_url(self):
        data_uri = "data:audio/mpeg;base64,//uQx"
        result = _convert_user_content(
            [{"type": "input_audio", "file_data": data_uri}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], AudioUrl)
        self.assertEqual(result[0].url, data_uri)
        self.assertEqual(result[0].media_type, "audio/mpeg")

    def test_input_audio_unsupported_format_is_placeholder(self):
        result = _convert_user_content(
            [{"type": "input_audio", "file_data": "data:audio/ogg;base64,AAAA"}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertIn("not supported", result[0].content)
        self.assertIn("audio/ogg", result[0].content)

    def test_input_file_text_plain_decoded_to_text(self):
        payload = base64.b64encode("Hello, file content!".encode("utf-8")).decode("ascii")
        result = _convert_user_content(
            [{"type": "input_file", "file_data": f"data:text/plain;base64,{payload}"}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(result[0].content, "Hello, file content!")

    def test_input_file_pdf_to_document_url(self):
        data_uri = "data:application/pdf;base64,JVBERi0xLjQ="
        result = _convert_user_content(
            [{"type": "input_file", "file_data": data_uri}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], DocumentUrl)
        self.assertEqual(result[0].url, data_uri)
        self.assertEqual(result[0].media_type, "application/pdf")

    def test_input_file_unsupported_format_is_placeholder(self):
        result = _convert_user_content(
            [{"type": "input_file", "file_data": "data:application/zip;base64,UEsDB"}]
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertIn("not supported", result[0].content)


class TestChatAgentStreaming(unittest.TestCase):
    """ChatAgent streams text deltas, thinking tokens, and usage metadata correctly.

    Tests the observable output of agent.chat():
    - Text deltas are yielded as {"content": "..."} objects
    - Thinking deltas are yielded as {"thinking": "..."} objects
    - Thinking done marker is yielded as {"thinking_done": True}
    - Usage metadata is yielded as {"__usage__": {...}} object
    - Errors are surfaced as {"__error__": "..."} in stream
    """

    def test_streams_text_and_usage(self):
        """ARRANGE: Event stream with text start, delta, and run result
        ACT: Run chat
        ASSERT: Text deltas and usage metadata are emitted once."""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=12, output_tokens=3, total_tokens=15
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="Hello"))
                    yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="!"))
                    yield PartEndEvent(index=0, part=TextPart(content="Hello!"))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for payload in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    collected.append(payload)

            self.assertEqual(collected[0], {"content": "Hello"})
            self.assertEqual(collected[1], {"content": "!"})
            self.assertEqual(collected[2]["__usage__"]["prompt_tokens"], 12)
            self.assertEqual(collected[2]["__usage__"]["completion_tokens"], 3)
            self.assertEqual(collected[2]["__usage__"]["total_tokens"], 15)

        asyncio.run(_run())

    def test_error_surfaced_in_stream(self):
        """ARRANGE: Event-stream context raises while entering
        ACT: Run chat
        ASSERT: Error surfaced as {"__error__": "..."} in stream"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(
                    side_effect=ConnectionError("Connection refused")
                )
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for payload in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    collected.append(payload)

            self.assertTrue(len(collected) > 0)
            self.assertIn("__error__", collected[0])
            self.assertIn("Connection refused", collected[0]["__error__"])

        asyncio.run(_run())

    def test_tool_call_enabled_passes_tools(self):
        """ARRANGE: ChatAgent
        ACT: Run chat with tool_call_enabled=True
        ASSERT: Agent constructed with toolsets"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=10, output_tokens=5, total_tokens=15
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="Response"))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for _ in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=True,
                ):
                    pass

            call_kwargs = mock_cls.call_args.kwargs
            self.assertIsNotNone(call_kwargs.get("toolsets"))

        asyncio.run(_run())

    def test_tool_call_disabled_no_tools(self):
        """ARRANGE: ChatAgent
        ACT: Run chat with tool_call_enabled=False
        ASSERT: Agent constructed with toolsets=None"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=10, output_tokens=5, total_tokens=15
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="Response"))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for _ in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    pass

            call_kwargs = mock_cls.call_args.kwargs
            self.assertIsNone(call_kwargs.get("toolsets"))

        asyncio.run(_run())

    def test_streams_thinking_tokens(self):
        """ARRANGE: Event stream with incremental thinking and text
        ACT: Run chat
        ASSERT: Thinking is emitted once per delta and never as a repeated full snapshot."""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=10, output_tokens=5, total_tokens=15
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=ThinkingPart(content="Let me think"))
                    yield PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" about this"))
                    yield PartEndEvent(index=0, part=ThinkingPart(content="Let me think about this"), next_part_kind="text")
                    yield PartStartEvent(index=1, part=TextPart(content="The answer is 42."))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for payload in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "What is 6*7?"}],
                    tool_call_enabled=False,
                ):
                    collected.append(payload)

            thinking_deltas = [c for c in collected if c.get("thinking") is not None]
            thinking_done = [c for c in collected if c.get("thinking_done")]
            contents = [c for c in collected if c.get("content") is not None]
            usage = [c for c in collected if "__usage__" in c]

            self.assertEqual([c["thinking"] for c in thinking_deltas], ["Let me think", " about this"])
            self.assertEqual(len(thinking_done), 1)
            self.assertEqual(len(contents), 1)
            self.assertEqual(contents[0]["content"], "The answer is 42.")
            self.assertEqual(len(usage), 1)

        asyncio.run(_run())

    def test_tool_call_streams_result(self):
        """ARRANGE: Event stream with function-tool lifecycle events
        ACT: Run chat with tool_call_enabled=True
        ASSERT: Stream includes matching tool call/result events, then content"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=20, output_tokens=10, total_tokens=30
                )

                async def mock_events():
                    yield FunctionToolCallEvent(ToolCallPart(
                        tool_name="web_search",
                        args='{"query": "Python"}',
                        tool_call_id="call_abc123",
                    ))
                    yield FunctionToolResultEvent(ToolReturnPart(
                        tool_name="web_search",
                        content="Python is a programming language",
                        tool_call_id="call_abc123",
                    ))
                    yield PartStartEvent(index=0, part=TextPart(
                        content="Python is a versatile programming language."
                    ))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for text in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "What is Python?"}],
                    tool_call_enabled=True,
                ):
                    collected.append(text)

            # Verify the stream sequence
            tool_calls = [c for c in collected if "tool_call" in c]
            tool_results = [c for c in collected if "tool_result" in c]
            contents = [c for c in collected if "content" in c]
            thinking_done = [c for c in collected if "thinking_done" in c]
            usage = [c for c in collected if "__usage__" in c]

            # One tool call event with tool_call_id
            self.assertEqual(len(tool_calls), 1)
            self.assertEqual(tool_calls[0]["tool_call"]["tool_call_id"], "call_abc123")
            self.assertEqual(tool_calls[0]["tool_call"]["name"], "web_search")
            self.assertEqual(tool_calls[0]["tool_call"]["status"], "executing")

            # One tool result event with result content
            self.assertEqual(len(tool_results), 1)
            self.assertEqual(tool_results[0]["tool_result"]["tool_call_id"], "call_abc123")
            self.assertEqual(tool_results[0]["tool_result"]["name"], "web_search")
            self.assertEqual(tool_results[0]["tool_result"]["status"], "done")
            self.assertEqual(
                tool_results[0]["tool_result"]["result"],
                "Python is a programming language",
            )

            # Final text response
            self.assertEqual(len(contents), 1)
            self.assertEqual(
                contents[0]["content"],
                "Python is a versatile programming language.",
            )

            # thinking_done not emitted (no thinking parts in this mock)
            self.assertEqual(len(thinking_done), 0)
            self.assertEqual(len(usage), 1)

        asyncio.run(_run())

    def test_no_thinking_done_without_thinking(self):
        """ARRANGE: ChatAgent with model that doesn't produce thinking
        ACT: Run chat
        ASSERT: Content emitted, no thinking_done (only emitted when thinking was seen)"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=5, output_tokens=3, total_tokens=8
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="Hi there!"))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for text in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    collected.append(text)

            # Content first, then usage (no thinking_done since no thinking was seen)
            self.assertEqual(collected[0], {"content": "Hi there!"})
            thinking_done = [c for c in collected if "thinking_done" in c]
            self.assertEqual(len(thinking_done), 0)
            self.assertEqual(len(collected), 2)
            self.assertIn("__usage__", collected[1])

        asyncio.run(_run())

    def test_chat_cancels_on_event(self):
        """ARRANGE: ChatAgent with mocked stream that yields several parts,
                     and an asyncio.Event set after the first part is yielded
        ACT: Run chat with cancel_event
        ASSERT: Loop breaks early, __cancelled__ payload emitted, fewer content
               payloads than the full stream would produce"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []
            cancel_event = asyncio.Event()
            full_part_count = 4  # total parts the mock would yield if uninterrupted
            yield_count = {"n": 0}

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=10, output_tokens=5, total_tokens=15
                )

                async def mock_events():
                    for i in range(full_part_count):
                        yield_count["n"] += 1
                        yield PartStartEvent(index=i, part=TextPart(content=f"chunk{i}"))
                        # Set the cancel event after the first part is yielded.
                        if yield_count["n"] == 1:
                            cancel_event.set()
                        # Let the event loop tick so the cancellation check
                        # (which runs after each event) takes effect.
                        await asyncio.sleep(0)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for payload in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                    cancel_event=cancel_event,
                ):
                    collected.append(payload)

            # The first response batch is yielded as a content delta.
            content_payloads = [c for c in collected if "content" in c]
            self.assertGreaterEqual(len(content_payloads), 1)

            # A __cancelled__ payload must be emitted.
            cancelled = [c for c in collected if c.get("__cancelled__")]
            self.assertEqual(len(cancelled), 1)

            # No usage metadata is emitted on cancellation.
            usage = [c for c in collected if "__usage__" in c]
            self.assertEqual(len(usage), 0)

            # The stream broke early: fewer content payloads than the full
            # stream would have produced.
            self.assertLess(len(content_payloads), full_part_count)

            # The underlying iterator was not fully consumed.
            self.assertLess(yield_count["n"], full_part_count)

            # The event stream context's __aexit__ was triggered by the
            # break, which is what cancels the background agent task and its
            # upstream HTTP connection.
            mock_cm.__aexit__.assert_called_once()

        asyncio.run(_run())

    def test_chat_cancels_during_silence(self):
        """ARRANGE: ChatAgent with a mocked stream that yields one TextPart
        start, then blocks forever (a silent upstream that never yields again);
        a background task sets cancel_event after ~50ms
        ACT: Consume the stream under a 5s wait_for budget
        ASSERT: Cancellation is prompt (pre-fix this hangs until the 5s
        timeout): exactly the first content payload followed by the
        __cancelled__ marker, and the stream context's __aexit__ was awaited
        once."""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []
            cancel_event = asyncio.Event()

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="a"))
                    # Upstream goes silent: block forever, never yield again.
                    await asyncio.Event().wait()

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async def consume():
                    async for payload in agent.chat(
                        model="test-model",
                        messages=[{"role": "user", "content": "Hi"}],
                        tool_call_enabled=False,
                        cancel_event=cancel_event,
                    ):
                        collected.append(payload)

                async def cancel_soon():
                    await asyncio.sleep(0.05)
                    cancel_event.set()

                cancel_task = asyncio.create_task(cancel_soon())
                try:
                    await asyncio.wait_for(consume(), timeout=5)
                finally:
                    cancel_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancel_task

                self.assertEqual(
                    collected,
                    [{"content": "a"}, {"__cancelled__": True}],
                )

                # The event stream context's __aexit__ was triggered by the
                # break, which is what cancels the background agent task and
                # its upstream HTTP connection.
                mock_cm.__aexit__.assert_called_once()

        asyncio.run(_run())

    def test_system_prompt_not_duplicated(self):
        """ARRANGE: Client sends the same system prompt both as the
        system_prompt field and as a system message in messages
        ACT: Run chat with both
        ASSERT: The captured message_history has exactly one leading
        ModelRequest with a single SystemPromptPart whose content is the
        prompt exactly once (not repeated)"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=5, output_tokens=3, total_tokens=8
                )

                async def mock_events():
                    yield PartStartEvent(index=0, part=TextPart(content="Hi"))
                    yield AgentRunResultEvent(result=mock_result)

                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_events())
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream_events = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for _ in agent.chat(
                    model="test-model",
                    messages=[
                        {"role": "system", "content": "You are helpful."},
                        {"role": "user", "content": "Hi"},
                    ],
                    system_prompt="You are helpful.",
                    tool_call_enabled=False,
                ):
                    pass

                history = mock_agent.run_stream_events.call_args.kwargs["message_history"]
                # Exactly one leading ModelRequest carrying the system prompt.
                self.assertIsInstance(history[0], ModelRequest)
                self.assertEqual(len(history[0].parts), 1)
                system_parts = [
                    p for p in history[0].parts if isinstance(p, SystemPromptPart)
                ]
                self.assertEqual(len(system_parts), 1)
                self.assertEqual(system_parts[0].content, "You are helpful.")
                # The user message follows, with no system parts anywhere else.
                for other in history[1:]:
                    self.assertFalse(
                        any(isinstance(p, SystemPromptPart) for p in other.parts)
                    )
                self.assertIsInstance(history[1], ModelRequest)
                self.assertIsInstance(history[1].parts[0], UserPromptPart)
                self.assertEqual(history[1].parts[0].content[0].content, "Hi")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
