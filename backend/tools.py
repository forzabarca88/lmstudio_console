"""Agentic tool definitions using Pydantic AI.

Provides tool schemas for OpenAI-compatible endpoints and
tool execution for backend-invoked tools.
"""

import json
from collections.abc import Callable

from pydantic_ai import Agent
from pydantic_ai.tools import ToolDefinition

from backend.logger import trace_logger

# Agent holds tool definitions; model is set per-request by the caller.
_agent = Agent()


@_agent.tool_plain
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


# --- HTML-to-text extraction ---

_MAX_PAGE_SIZE = 50_000  # 50KB limit
_FETCH_TIMEOUT = 30  # seconds


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML using stdlib HTMLParser."""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._text = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip += 1

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self._skip > 0:
                self._skip -= 1

        def handle_data(self, data):
            if self._skip == 0 and data.strip():
                self._text.append(data.strip())

        def get_text(self):
            return "\n".join(t for t in self._text if t)

    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.get_text()


@_agent.tool_plain
async def open_web_page(url: str) -> str:
    """Fetch and extract readable text content from a web page URL."""
    import httpx

    log = trace_logger.logger
    log.debug(f"OPEN_WEB_PAGE url={url!r}")

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()

            raw = response.text
            if len(raw) > _MAX_PAGE_SIZE:
                raw = raw[:_MAX_PAGE_SIZE]

            text = _html_to_text(raw)
            log.debug(f"OPEN_WEB_PAGE extracted {len(text)} chars")
            return text if text else "(page contained no readable text)"
    except httpx.TimeoutException:
        log.error(f"OPEN_WEB_PAGE timeout: {url}")
        return f"Error: Request timed out after {_FETCH_TIMEOUT}s"
    except Exception as e:
        log.error(f"OPEN_WEB_PAGE error: {e}")
        return f"Error fetching page: {e}"


# --- Public API ---


def get_tool_schemas() -> list[dict]:
    """Return OpenAI-compatible tool definitions for all registered tools.

    Returns:
        List of dicts in OpenAI tool format:
        [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
    """
    toolset = _agent._function_toolset
    schemas = []
    for name, tool in toolset.tools.items():
        td: ToolDefinition = tool.tool_def
        schemas.append({
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description or "",
                "parameters": td.parameters_json_schema,
            },
        })
    return schemas


async def execute_tool(name: str, arguments: dict) -> str:
    """Execute a registered tool with the given arguments.

    Args:
        name: Tool name (e.g. "web_search").
        arguments: Dict of argument values matching the tool's schema.

    Returns:
        String result from the tool execution.

    Raises:
        ValueError: If the tool is not registered.
    """
    toolset = _agent._function_toolset
    if name not in toolset.tools:
        raise ValueError(f"Unknown tool: {name}")

    tool = toolset.tools[name]
    func = tool.function_schema.function

    trace_logger.logger.debug(f"EXECUTE_TOOL {name} args={arguments}")

    if tool.function_schema.is_async:
        result = await func(**arguments)
    else:
        result = func(**arguments)

    # Ensure string result
    if not isinstance(result, str):
        result = json.dumps(result)

    trace_logger.logger.debug(f"EXECUTE_TOOL {name} result={result[:200]}")
    return result
