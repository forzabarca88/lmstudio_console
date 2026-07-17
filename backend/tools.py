"""Agentic tool definitions using Pydantic AI.

Provides tool schemas for OpenAI-compatible endpoints and
tool execution for backend-invoked tools.
"""

import json
import re
import sys
import threading
from collections.abc import Callable
from io import StringIO

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


# --- Python code execution ---

_PY_MAX_CODE_SIZE = 10_000  # 10KB limit
_PY_TIMEOUT = 10  # seconds
_PY_BLOCKED = frozenset([
    "import", "__import__", "open", "eval", "exec", "compile",
    "getattr", "setattr", "delattr", "hasattr", "dir",
    "globals", "locals", "breakpoint", "input",
    "__build_class__", "help", "memoryview", "super",
])

# Safe builtins allowed in the sandbox
_PY_SAFE_BUILTINS = {
    "abs": abs,
    "bool": bool,
    "bytearray": bytearray,
    "bytes": bytes,
    "callable": callable,
    "chr": chr,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "frozenset": frozenset,
    "hash": hash,
    "hex": hex,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "AssertionError": AssertionError,
    "NotImplementedError": NotImplementedError,
    "OverflowError": OverflowError,
    "ZeroDivisionError": ZeroDivisionError,
    "AttributeError": AttributeError,
    "NameError": NameError,
    "SyntaxError": SyntaxError,
    "ImportError": ImportError,
    "ModuleNotFoundError": ModuleNotFoundError,
    "FileNotFoundError": FileNotFoundError,
    "PermissionError": PermissionError,
    "OSError": OSError,
    "IOError": IOError,
    "UnicodeError": UnicodeError,
    "UnicodeEncodeError": UnicodeEncodeError,
    "UnicodeDecodeError": UnicodeDecodeError,
    "UnicodeTranslateError": UnicodeTranslateError,
    "FloatingPointError": FloatingPointError,
    "ArithmeticError": ArithmeticError,
    "LookupError": LookupError,
    "BufferError": BufferError,
    "MemoryError": MemoryError,
    "ReferenceError": ReferenceError,
    "SystemError": SystemError,
    "EOFError": EOFError,
}


def _safe_builtins() -> dict:
    """Return a restricted builtins dict that blocks dangerous operations."""
    return dict(_PY_SAFE_BUILTINS)


def _check_code_safety(code: str) -> str | None:
    """Validate code for safety. Returns an error message if unsafe, or None if OK."""
    if len(code) > _PY_MAX_CODE_SIZE:
        return f"Code exceeds {_PY_MAX_CODE_SIZE} character limit ({len(code)} chars)"

    # Check for blocked names in the code
    import re
    for name in _PY_BLOCKED:
        # Use word boundary to avoid false positives (e.g. "compute" contains "pute")
        if re.search(r'\b' + re.escape(name) + r'\b', code):
            return f"Blocked operation: '{name}' is not allowed"

    return None


@_agent.tool_plain
async def run_python_code(code: str) -> str:
    """Execute Python code in a sandboxed environment.

    Only safe built-in functions are available. No imports, file access,
    network access, or reflection operations are permitted.

    Args:
        code: Python code to execute.

    Returns:
        Captured stdout/stderr output, or an error message.
    """
    log = trace_logger.logger
    log.debug(f"RUN_PYTHON_CODE code_length={len(code)}")

    # Safety checks
    error = _check_code_safety(code)
    if error:
        log.warning(f"RUN_PYTHON_CODE blocked: {error}")
        return f"Error: {error}"

    # Use threading for timeout (signal.alarm only works in main thread)
    result_holder: list = [None]
    error_holder: list = [None]

    def _run_code():
        try:
            captured_stdout = StringIO()
            captured_stderr = StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr

            sys.stdout = captured_stdout
            sys.stderr = captured_stderr

            namespace = _safe_builtins()
            exec(code, namespace, namespace)

            output = captured_stdout.getvalue()
            err_output = captured_stderr.getvalue()

            sys.stdout = old_stdout
            sys.stderr = old_stderr

            result = output if output else "(no output)"
            if err_output:
                result += f"\nStderr:\n{err_output}"
            result_holder[0] = result
        except Exception as e:
            error_holder[0] = e

    t = threading.Thread(target=_run_code)
    t.start()
    t.join(timeout=_PY_TIMEOUT)

    if t.is_alive():
        # Thread didn't finish within timeout
        log.error(f"RUN_PYTHON_CODE timeout: exceeded {_PY_TIMEOUT}s")
        return f"Error: Code execution exceeded {_PY_TIMEOUT}s timeout"

    if error_holder[0] is not None:
        e = error_holder[0]
        log.error(f"RUN_PYTHON_CODE error: {e}")
        result = f"Error: {type(e).__name__}: {e}"
    else:
        result = result_holder[0]
        log.debug(f"RUN_PYTHON_CODE output={result[:200]}")

    return result


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
