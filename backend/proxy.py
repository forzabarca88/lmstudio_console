"""Proxy service that forwards requests to LM Studio / OpenAI-compatible endpoints."""

import asyncio
import json
import time
from typing import AsyncIterable, Optional

import httpx

from backend.config import get_lm_studio_url
from backend.logger import trace_logger

# Shared client with connection pooling and reasonable timeouts.
# Created lazily to avoid issues if config isn't loaded yet.
_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """Return a shared AsyncClient for proxying requests."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    return _client


async def close_client() -> None:
    """Close the shared client. Call during app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def proxy_request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    target_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """Forward a buffered request to the target API server.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g. "/api/v1/models").
        body: Optional JSON body.
        headers: Optional extra headers.
        target_url: Optional base URL. Falls back to LM_STUDIO_URL env var.
        timeout: Optional read timeout in seconds. Uses client default if not set.

    Returns:
        httpx.Response from the target server.

    Raises:
        httpx.ConnectError: If the target server is unreachable.
        httpx.TimeoutException: If the connection or read times out.
        httpx.HTTPStatusError: If the target returns an error status.
    """
    base_url = target_url or get_lm_studio_url()
    url = f"{base_url}{path}"

    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    request_body = json.dumps(body) if body else None

    # Start trace
    trace = trace_logger.log_request(method, path, url, request_body)
    start_time = time.monotonic()

    client = get_client()
    try:
        kwargs = {"content": request_body, "headers": request_headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await client.request(method, url, **kwargs)
        duration = time.monotonic() - start_time
        resp_body = response.text if response.content else None
        trace_logger.log_response(
            trace, response.status_code,
            response.headers.get("Content-Type", ""), duration, resp_body
        )
        return response

    except httpx.ConnectError as e:
        duration = time.monotonic() - start_time
        trace_logger.log_error(trace, e, duration)
        raise

    except httpx.TimeoutException as e:
        duration = time.monotonic() - start_time
        trace_logger.log_error(trace, e, duration)
        raise

    except httpx.HTTPStatusError as e:
        duration = time.monotonic() - start_time
        resp_body = e.response.text if e.response.content else None
        trace_logger.log_response(
            trace, e.response.status_code,
            e.response.headers.get("Content-Type", ""), duration, resp_body
        )
        raise


# Idle timeout for proxied upstream *streams*: bounds how long we wait for
# the next chunk from upstream while streaming a response. Without it, a
# stalled upstream (headers sent, body never arriving) would hang the client
# indefinitely, since `client.stream(..., timeout=None)` bypasses the client
# default. 120s is the idle (read) timeout between chunks; connect stays at
# the client's usual 10s.
_STREAM_READ_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def proxy_stream_iter(
    method: str,
    path: str,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    target_url: Optional[str] = None,
) -> AsyncIterable[bytes]:
    """Stream a response from the target API server.

    Yields raw bytes as they arrive from the upstream server.

    The stream is bounded by _STREAM_READ_TIMEOUT (120s idle between
    chunks): a stalled upstream raises httpx.TimeoutException instead of
    hanging the client forever. The exception is re-raised so the SSE
    consumer (backend/server.py) can surface it as an error data: line.

    Args:
        method: HTTP method.
        path: API path.
        body: Optional JSON body.
        headers: Optional extra headers.
        target_url: Optional base URL. Falls back to LM_STUDIO_URL env var.

    Yields:
        Raw response bytes.

    Raises:
        httpx.ConnectError: If the target server is unreachable.
        httpx.TimeoutException: If the connection times out, or the upstream
            stream idles past _STREAM_READ_TIMEOUT between chunks.
        httpx.HTTPStatusError: If the target returns an error status.
    """
    base_url = target_url or get_lm_studio_url()
    url = f"{base_url}{path}"

    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    request_body = json.dumps(body) if body else None

    # Start trace
    trace = trace_logger.log_request(method, path, url, request_body)
    start_time = time.monotonic()

    client = get_client()
    try:
        async with client.stream(
            method, url,
            content=request_body,
            headers=request_headers,
            timeout=_STREAM_READ_TIMEOUT,
        ) as response:
            duration = time.monotonic() - start_time
            content_type = response.headers.get("Content-Type", "")

            if response.status_code >= 400:
                # Read error body for tracing
                error_body = await response.aread()
                trace_logger.log_response(
                    trace, response.status_code, content_type,
                    duration, error_body.decode(errors="replace") if error_body else None
                )
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response
                )

            streamed_chunks = []
            stream_start = time.monotonic()

            async for chunk in response.aiter_bytes(chunk_size=1024):
                streamed_chunks.append(chunk)
                yield chunk

            total_duration = duration + (time.monotonic() - stream_start)
            combined = b"".join(streamed_chunks)
            if combined:
                trace_logger.log_response(
                    trace, response.status_code, content_type,
                    total_duration, combined.decode(errors="replace")
                )

    except httpx.ConnectError as e:
        duration = time.monotonic() - start_time
        trace_logger.log_error(trace, e, duration)
        raise

    except httpx.TimeoutException as e:
        duration = time.monotonic() - start_time
        trace_logger.log_error(trace, e, duration)
        raise

    except asyncio.CancelledError:
        # The consumer abandoned the stream (client disconnect or task
        # cancellation). The `async with client.stream(...)` block above
        # already closed the upstream connection via __aexit__ while the
        # exception unwound, so the connection is released cleanly. Record
        # the cancellation on the trace for observability, then re-raise to
        # preserve asyncio cancellation semantics.
        duration = time.monotonic() - start_time
        trace_logger.log_cancelled(trace, duration)
        raise
