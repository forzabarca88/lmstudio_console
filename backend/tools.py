"""Agentic tool definitions using Pydantic AI.

Provides tool schemas for OpenAI-compatible endpoints and
tool execution for backend-invoked tools.
"""

import json
from collections.abc import Callable

from pydantic_ai import Agent
from pydantic_ai.tools import ToolDefinition

from backend.logger import TraceLogger

trace_logger = TraceLogger()

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
