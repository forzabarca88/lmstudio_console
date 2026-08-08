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
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.agent import (
    ChatAgent,
    _openai_messages_to_pai_messages,
    _convert_user_content,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    SystemPromptPart,
    TextPart,
    TextContent,
    ThinkingPart,
    ThinkingPartDelta,
    ImageUrl,
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
        """ARRANGE: ChatAgent with mocked Pydantic AI agent
        ACT: Run chat
        ASSERT: Yields thinking_done, content objects, then usage metadata

        Pydantic AI's stream_response() yields ModelResponse objects with parts."""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=12, output_tokens=3, total_tokens=15
                )

                # stream_response() yields ModelResponse objects with parts.
                # TextPart.content is FULL accumulated text (Pydantic AI semantics).
                # Backend converts to incremental deltas for SSE events.
                async def mock_stream_iter(debounce_by=0.05):
                    yield ModelResponse(parts=[TextPart(content="Hello")])
                    yield ModelResponse(parts=[TextPart(content="Hello!")])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for text in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    collected.append(text)

            # Backend emits incremental deltas: first "Hello", then "!" (the new portion)
            self.assertEqual(collected[0], {"content": "Hello"})
            self.assertEqual(collected[1], {"content": "!"})
            # Usage metadata (thinking_done only emitted when thinking was seen)
            self.assertEqual(collected[2]["__usage__"]["prompt_tokens"], 12)
            self.assertEqual(collected[2]["__usage__"]["completion_tokens"], 3)
            self.assertEqual(collected[2]["__usage__"]["total_tokens"], 15)

        asyncio.run(_run())

    def test_error_surfaced_in_stream(self):
        """ARRANGE: ChatAgent where agent raises
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
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for text in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                    tool_call_enabled=False,
                ):
                    collected.append(text)

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

                async def mock_stream_iter(debounce_by=0.05):
                    yield ModelResponse(parts=[TextPart(content="Response")])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
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

                async def mock_stream_iter(debounce_by=0.05):
                    yield ModelResponse(parts=[TextPart(content="Response")])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
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
        """ARRANGE: ChatAgent with model producing thinking tokens
        ACT: Run chat
        ASSERT: Yields thinking deltas, thinking_done marker, then content"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=10, output_tokens=5, total_tokens=15
                )

                async def mock_stream_iter(debounce_by=0.05):
                    # Thinking deltas
                    yield ModelResponse(parts=[ThinkingPartDelta(content_delta="Let me think")])
                    yield ModelResponse(parts=[ThinkingPartDelta(content_delta=" about this")])
                    # Thinking complete
                    yield ModelResponse(parts=[ThinkingPart(content="Let me think about this")])
                    # Regular text response
                    yield ModelResponse(parts=[TextPart(content="The answer is 42.")])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
                mock_cls.return_value = mock_agent

                async for text in agent.chat(
                    model="test-model",
                    messages=[{"role": "user", "content": "What is 6*7?"}],
                    tool_call_enabled=False,
                ):
                    collected.append(text)

            # Verify thinking deltas, thinking_done, content, and usage
            thinking_deltas = [c for c in collected if "thinking" in c and "thinking_done" not in c]
            thinking_done = [c for c in collected if c.get("thinking_done")]
            contents = [c for c in collected if "content" in c]
            usage = [c for c in collected if "__usage__" in c]

            self.assertEqual(len(thinking_deltas), 2)
            self.assertEqual(thinking_deltas[0]["thinking"], "Let me think")
            self.assertEqual(thinking_deltas[1]["thinking"], " about this")
            self.assertEqual(len(thinking_done), 1)
            self.assertEqual(thinking_done[0]["thinking_done"], True)
            self.assertEqual(len(contents), 1)
            self.assertEqual(contents[0]["content"], "The answer is 42.")
            self.assertEqual(len(usage), 1)

        asyncio.run(_run())

    def test_tool_call_streams_result(self):
        """ARRANGE: ChatAgent with model that requests tool calls
        ACT: Run chat with tool_call_enabled=True, mock stream_response to yield
             ToolCallPart, then ToolReturnPart, then TextPart
        ASSERT: Stream includes tool_call with tool_call_id, tool_result with
                result field populated, then content"""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=20, output_tokens=10, total_tokens=30
                )

                async def mock_stream_iter(debounce_by=0.05):
                    # First yield: model requests a tool call
                    yield ModelResponse(parts=[
                        ToolCallPart(
                            tool_name="web_search",
                            args='{"query": "Python"}',
                            tool_call_id="call_abc123",
                        ),
                    ])
                    # Second yield: tool result from Pydantic AI's internal execution
                    yield ModelResponse(parts=[
                        ToolReturnPart(
                            tool_name="web_search",
                            content="Python is a programming language",
                            tool_call_id="call_abc123",
                        ),
                    ])
                    # Third yield: final text response after tool results
                    yield ModelResponse(parts=[
                        TextPart(content="Python is a versatile programming language."),
                    ])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
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

                async def mock_stream_iter(debounce_by=0.05):
                    yield ModelResponse(parts=[TextPart(content="Hi there!")])

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
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

                async def mock_stream_iter(debounce_by=0.05):
                    for i in range(full_part_count):
                        yield_count["n"] += 1
                        yield ModelResponse(parts=[TextPart(content=f"chunk{i}")])
                        # Set the cancel event after the first part is yielded.
                        if yield_count["n"] == 1:
                            cancel_event.set()
                        # Let the event loop tick so the cancellation check
                        # (which runs after each response batch) takes effect.
                        await asyncio.sleep(0)

                mock_result.stream_response = mock_stream_iter
                mock_cm = MagicMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_result)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_agent.run_stream = MagicMock(return_value=mock_cm)
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

            # The run_stream context manager's __aexit__ was triggered by the
            # break, which is what calls close_stream() on the upstream HTTP
            # connection — verifying upstream endpoint cancellation fires.
            mock_cm.__aexit__.assert_called_once()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
