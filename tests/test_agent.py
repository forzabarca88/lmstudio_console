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
    ImageUrl,
    ToolCallPart,
    ToolReturnPart,
)


class TestMessageConversion(unittest.TestCase):
    """OpenAI messages convert correctly to Pydantic AI format."""

    def test_single_user_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ModelRequest)
        self.assertIsInstance(result[0].parts[0], UserPromptPart)

    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].parts[0], SystemPromptPart)
        self.assertEqual(result[0].parts[0].content, "You are helpful")
        self.assertIsInstance(result[0].parts[1], UserPromptPart)

    def test_user_assistant_conversation(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], ModelRequest)
        self.assertIsInstance(result[1], ModelResponse)
        self.assertEqual(result[1].parts[0].content, "Hi there!")

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
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], ModelRequest)
        self.assertIsInstance(result[1], ModelResponse)
        parts = result[1].parts
        # Empty content string is skipped; ToolCallPart comes first
        self.assertIsInstance(parts[0], ToolCallPart)
        self.assertEqual(parts[0].tool_call_id, "call_1")
        self.assertEqual(parts[0].tool_name, "web_search")
        self.assertIsInstance(parts[1], ToolReturnPart)
        self.assertEqual(parts[1].tool_call_id, "call_1")
        self.assertEqual(parts[1].content, "Python is a programming language")

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
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 2)
        parts = result[1].parts
        self.assertIsInstance(parts[0], TextPart)
        self.assertEqual(parts[0].content, "Let me search for that.")
        self.assertIsInstance(parts[1], ToolCallPart)
        self.assertEqual(parts[1].tool_call_id, "call_1")
        self.assertIsInstance(parts[2], ToolReturnPart)

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
        result = _openai_messages_to_pai_messages(messages)
        self.assertEqual(len(result), 1)
        part = result[0].parts[0]
        self.assertIsInstance(part, UserPromptPart)
        self.assertIsInstance(part.content[0], TextContent)
        self.assertEqual(part.content[0].content, "What is in this image?")
        self.assertIsInstance(part.content[1], ImageUrl)


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
    """ChatAgent streams text deltas and usage metadata correctly.

    Tests the observable output of agent.chat():
    - Text deltas are yielded as {"content": "..."} objects
    - Usage metadata is yielded as {"__usage__": {...}} object
    - Errors are surfaced as {"__error__": "..."} in stream
    """

    def test_streams_text_and_usage(self):
        """ARRANGE: ChatAgent with mocked Pydantic AI agent
        ACT: Run chat
        ASSERT: Yields content objects followed by usage metadata

        Pydantic AI's stream_text(delta=True) yields incremental deltas."""
        agent = ChatAgent("http://localhost:1234")

        async def _run():
            collected = []

            with patch("backend.agent.Agent") as mock_cls:
                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.usage = MagicMock(
                    input_tokens=12, output_tokens=3, total_tokens=15
                )

                # stream_text(delta=True) yields deltas directly
                async def mock_stream_iter(delta=False, debounce_by=0.05):
                    yield "Hello"
                    yield "!"

                mock_result.stream_text = mock_stream_iter
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

            # Text deltas wrapped as {"content": "..."}
            self.assertEqual(collected[0], {"content": "Hello"})
            self.assertEqual(collected[1], {"content": "!"})
            # Usage metadata
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

                async def mock_stream_iter(delta=False, debounce_by=0.05):
                    yield "Response"

                mock_result.stream_text = mock_stream_iter
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

                async def mock_stream_iter(delta=False, debounce_by=0.05):
                    yield "Response"

                mock_result.stream_text = mock_stream_iter
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


if __name__ == "__main__":
    unittest.main()
