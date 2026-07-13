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


class TestToolExecution(unittest.TestCase):
    """Tool execution works correctly."""

    def test_execute_web_search(self):
        """ARRANGE: web_search tool available
        ACT: Execute with query
        ASSERT: Results returned as string"""
        result = asyncio.run(execute_tool("web_search", {"query": "Python"}))
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_execute_unknown_tool(self):
        """ARRANGE: Unknown tool name
        ACT: Execute tool
        ASSERT: ValueError raised"""
        with self.assertRaises(ValueError):
            asyncio.run(execute_tool("nonexistent", {}))


if __name__ == "__main__":
    unittest.main()
