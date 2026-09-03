"""Agentic tool definitions using Pydantic AI.

Tools are registered on the shared module-level ``_agent``; the chat
agent (``backend/agent.py``) passes its toolsets to per-request agents
and lets Pydantic AI's internal tool loop execute them.
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
from urllib.parse import urljoin

import anyio
import httpx
from pydantic_ai import Agent

from backend.logger import trace_logger
from backend.url_security import URLSecurityError, validate_public_url

# Agent holds tool definitions; model is set per-request by the caller.
_agent = Agent()


@_agent.tool_plain
async def web_search(query: str) -> str:
    """Search the web for information about a given query."""
    from ddgs import DDGS

    log = trace_logger.logger
    log.debug(f"WEB_SEARCH query={query!r}")

    def _search():
        ddgs = DDGS()
        return ddgs.text(query, max_results=5)

    try:
        results = await anyio.to_thread.run_sync(_search)
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
_MAX_REDIRECTS = 5  # manual redirect walk hop bound
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


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
    """Fetch and extract readable text content from a web page URL.

    Redirects are followed manually (up to ``_MAX_REDIRECTS`` hops) and
    every redirect target is re-validated with ``validate_public_url``;
    a redirect to an internal address is rejected instead of followed.
    """
    log = trace_logger.logger
    log.debug(f"OPEN_WEB_PAGE url={url!r}")

    try:
        # The validator performs DNS lookups, which block; keep the event
        # loop free while the URL is checked.
        await asyncio.to_thread(validate_public_url, url)
    except URLSecurityError as e:
        return f"Error: {e}"

    current_url = url
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            raw = ctype = None
            truncated = False
            hops = 0
            while True:
                async with client.stream("GET", current_url, follow_redirects=False) as resp:
                    if resp.status_code in _REDIRECT_STATUSES:
                        location = resp.headers.get("location")
                        if not location:
                            log.error(f"OPEN_WEB_PAGE redirect without Location: {current_url}")
                            return f"Error: Redirect (HTTP {resp.status_code}) without Location header"
                        if hops >= _MAX_REDIRECTS:
                            log.error(f"OPEN_WEB_PAGE redirect loop at {current_url}")
                            return f"Error: Too many redirects (exceeded {_MAX_REDIRECTS} hops)"
                        next_url = urljoin(current_url, location)
                        try:
                            await asyncio.to_thread(validate_public_url, next_url)
                        except URLSecurityError as e:
                            log.warning(f"OPEN_WEB_PAGE blocked redirect to {next_url!r}: {e}")
                            return f"Error: {e}"
                        hops += 1
                        current_url = next_url
                        continue
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "")
                    chunks, total = [], 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_PAGE_SIZE:
                            truncated = True
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    break
    except httpx.TimeoutException:
        log.error(f"OPEN_WEB_PAGE timeout: {url}")
        return f"Error: Request timed out after {_FETCH_TIMEOUT}s"
    except Exception as e:
        log.error(f"OPEN_WEB_PAGE error: {e}")
        return f"Error fetching page: {e}"

    body = raw.decode("utf-8", "replace")
    is_html = "text/html" in ctype or body.lstrip().lower().startswith("<!doc")
    text = _html_to_text(body) if is_html else body
    result = text if text.strip() else "(page contained no readable text)"
    if truncated:
        result += " [truncated]"
    log.debug(f"OPEN_WEB_PAGE extracted {len(result)} chars (truncated={truncated})")
    return result


# --- Python code execution ---

_PY_MAX_CODE_SIZE = 10_000  # 10KB limit
_PY_TIMEOUT = 10  # seconds
_PY_MAX_OUTPUT = 10 * 1024 * 1024  # 10MB cap per stream (stdout / stderr)


def _run_python_subprocess(code: str) -> tuple[int, str, str, bool, bool]:
    """Run *code* in a disposable isolated subprocess.

    Returns ``(returncode, stdout, stderr, timed_out, truncated)``. The
    child runs in its own process group (session) so the whole group can
    be killed on timeout. stdout and stderr are read in bounded chunks by
    daemon reader threads; when either stream exceeds ``_PY_MAX_OUTPUT``
    the process group is killed, the pipe is drained (without storing) so
    the child is never left blocked, and ``truncated`` is set.
    """
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True   # own process group
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        env={"PYTHONHASHSEED": "0"},          # minimal env, isolated mode
        **kwargs,
    )

    state = {"truncated": False, "killed": False}
    kill_lock = threading.Lock()

    def _kill_group():
        """Idempotently kill the child process group (SIGKILL on POSIX,
        ``proc.kill()`` elsewhere / as fallback)."""
        with kill_lock:
            if state["killed"]:
                return
            state["killed"] = True
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.kill()
        except Exception:
            pass

    def _read_bounded(stream, parts: list):
        """Read *stream* in bounded chunks into *parts* up to the cap.

        Once a chunk would push the total past ``_PY_MAX_OUTPUT``, kill the
        process group and drain the remaining pipe without storing so the
        child is not left blocked on a full pipe.
        """
        size = 0
        try:
            while True:
                chunk = stream.read1(65536)  # bounded chunk, no unbounded buffering
                if not chunk:
                    return
                if size + len(chunk) > _PY_MAX_OUTPUT:
                    state["truncated"] = True
                    _kill_group()
                    while stream.read1(65536):
                        pass  # drain without storing
                    return
                size += len(chunk)
                parts.append(chunk)
        except Exception:
            pass

    out_parts: list[bytes] = []
    err_parts: list[bytes] = []
    t1 = threading.Thread(target=_read_bounded, args=(proc.stdout, out_parts), daemon=True)
    t2 = threading.Thread(target=_read_bounded, args=(proc.stderr, err_parts), daemon=True)
    t1.start()
    t2.start()

    timed_out = False
    try:
        proc.wait(timeout=_PY_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        timed_out = True

    t1.join(5)
    t2.join(5)

    returncode = proc.returncode if proc.returncode is not None else -1
    stdout = b"".join(out_parts).decode("utf-8", "replace")
    stderr = b"".join(err_parts).decode("utf-8", "replace")
    return returncode, stdout, stderr, timed_out, state["truncated"]


@_agent.tool_plain
async def run_python_code(code: str) -> str:
    """Execute Python code in a disposable isolated subprocess.

    The code runs in a fresh Python process (isolated mode, minimal
    environment) with a hard timeout; the process group is terminated if
    it exceeds the timeout. stdout and stderr are each capped at 10MB —
    a process that exceeds the cap is killed and the result is marked
    truncated.

    Args:
        code: Python code to execute.

    Returns:
        Captured stdout/stderr output, or an error message. The result
        format is stdout, then a ``Stderr:`` section, then an
        ``[exit code N]`` line (when non-zero), and finally an
        ``[output truncated: exceeded 10MB]`` line when the cap was hit.
    """
    log = trace_logger.logger
    log.debug(f"RUN_PYTHON_CODE code_length={len(code)}")

    if len(code) > _PY_MAX_CODE_SIZE:
        log.warning(f"RUN_PYTHON_CODE rejected: code exceeds {_PY_MAX_CODE_SIZE} chars")
        return f"Error: Code exceeds {_PY_MAX_CODE_SIZE} character limit ({len(code)} chars)"

    returncode, stdout, stderr, timed_out, truncated = await anyio.to_thread.run_sync(
        _run_python_subprocess, code
    )

    if timed_out:
        log.error(f"RUN_PYTHON_CODE timeout: exceeded {_PY_TIMEOUT}s")
        return f"Error: Code execution exceeded {_PY_TIMEOUT}s timeout (process terminated)"

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"Stderr:\n{stderr}")
    if returncode != 0:
        parts.append(f"[exit code {returncode}]")
    if truncated:
        parts.append("[output truncated: exceeded 10MB]")
    result = "\n".join(parts) if parts else "(no output)"
    log.debug(f"RUN_PYTHON_CODE output={result[:200]}")
    return result
