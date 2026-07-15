"""Tools module tests.

Tests Pydantic AI tool schema generation and execution.
"""

import unittest
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.tools import get_tool_schemas, execute_tool


class TestToolSchemas(unittest.TestCase):
    """Tool schemas generated correctly."""

    def test_web_search_schema(self):
        """ARRANGE: Tools module loaded
        ACT: Get tool schemas
        ASSERT: web_search tool has correct OpenAI-compatible schema"""
        schemas = get_tool_schemas()
        self.assertIsInstance(schemas, list)
        self.assertGreater(len(schemas), 0)

        ws = next((s for s in schemas if s["function"]["name"] == "web_search"), None)
        self.assertIsNotNone(ws)
        self.assertEqual(ws["type"], "function")
        self.assertIn("description", ws["function"])
        params = ws["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("query", params["properties"])
        self.assertIn("query", params["required"])

    def test_open_web_page_schema(self):
        """ARRANGE: Tools module loaded
        ACT: Get tool schemas
        ASSERT: open_web_page tool has correct OpenAI-compatible schema"""
        schemas = get_tool_schemas()
        owp = next((s for s in schemas if s["function"]["name"] == "open_web_page"), None)
        self.assertIsNotNone(owp)
        self.assertEqual(owp["type"], "function")
        self.assertIn("description", owp["function"])
        params = owp["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("url", params["properties"])
        self.assertIn("url", params["required"])


class TestToolExecution(unittest.TestCase):
    """Tool execution works correctly."""

    def test_execute_web_search(self):
        """ARRANGE: web_search tool available
        ACT: Execute with query
        ASSERT: Results returned as string"""
        result = asyncio.run(execute_tool("web_search", {"query": "Python"}))
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_execute_open_web_page(self):
        """ARRANGE: open_web_page tool available, mock HTTP fetch
        ACT: Execute with URL
        ASSERT: Extracted text returned"""
        from unittest.mock import patch, AsyncMock, MagicMock
        import httpx

        async def _run():
            mock_response = MagicMock()
            mock_response.text = "<html><body><h1>Hello</h1><p>World</p></body></html>"
            mock_response.raise_for_status = MagicMock()
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)

            with patch("httpx.AsyncClient", return_value=mock_ctx):
                result = await execute_tool("open_web_page", {"url": "http://example.com"})

            self.assertIsInstance(result, str)
            self.assertIn("Hello", result)
            self.assertIn("World", result)

        asyncio.run(_run())

    def test_execute_open_web_page_error(self):
        """ARRANGE: open_web_page tool available, mock connection error
        ACT: Execute with bad URL
        ASSERT: Graceful error message returned"""
        from unittest.mock import patch, AsyncMock, MagicMock
        import httpx

        async def _run():
            mock_client = MagicMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)

            with patch("httpx.AsyncClient", return_value=mock_ctx):
                result = await execute_tool("open_web_page", {"url": "http://bad-domain.invalid"})

            self.assertIsInstance(result, str)
            self.assertIn("Error", result)

        asyncio.run(_run())

    def test_execute_unknown_tool(self):
        """ARRANGE: Unknown tool name
        ACT: Execute tool
        ASSERT: ValueError raised"""
        with self.assertRaises(ValueError):
            asyncio.run(execute_tool("nonexistent", {}))


if __name__ == "__main__":
    unittest.main()
