"""FastAPI server that serves static files and proxies API calls to LM Studio."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterable, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import get_host, get_port, get_lm_studio_url, get_static_dir
from backend.proxy import proxy_request, proxy_stream_iter, close_client, trace_logger
from backend.agent import ChatAgent
from backend.tools import get_tool_schemas, execute_tool
from backend.log_streamer import log_streamer

logger = logging.getLogger(__name__)

# Hop-by-hop headers that must not be forwarded from upstream responses.
# JSONResponse computes its own Content-Length; forwarding the upstream value
# causes h11 LocalProtocolError when re-serialized JSON differs in size.
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
}

# Header the frontend sends to override the LM Studio target URL.
_HEADER_LM_STUDIO_URL = "X-LM-Studio-URL"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - prints startup info."""
    host = get_host()
    port = get_port()
    lm_url = get_lm_studio_url()
    print(f"LM Studio Console running at http://{host if host != '0.0.0.0' else 'localhost'}:{port}", flush=True)
    print(f"Proxying to LM Studio at {lm_url} (default)", flush=True)
    yield
    await close_client()


app = FastAPI(
    title="LM Studio Console",
    lifespan=lifespan,
)


def _resolve_target_url(request: Request) -> str:
    """Resolve the target URL from request header, falling back to env var."""
    return request.headers.get(_HEADER_LM_STUDIO_URL) or get_lm_studio_url()


def _make_json_response(response: httpx.Response) -> JSONResponse:
    """Build a JSONResponse from an upstream httpx Response.

    Filters out hop-by-hop headers that would conflict with JSONResponse's
    own header computation (e.g. Content-Length mismatch on re-serialized JSON).
    """
    safe_headers = {
        k: v for k, v in response.headers.items()
        if k not in _HOP_BY_HOP_HEADERS
    }
    return JSONResponse(
        status_code=response.status_code,
        content=response.json(),
        headers=safe_headers,
    )


def _error_label(error: Exception) -> str:
    """Format error for display, using type name when message is empty."""
    msg = str(error)
    return msg if msg else type(error).__name__


def _handle_proxy_error(method: str, path: str, target: str, error: Exception) -> JSONResponse:
    """Log a proxy error and return an appropriate error response."""
    trace_logger.log_server_error(method, path, error)
    if isinstance(error, httpx.ConnectError):
        return JSONResponse(
            status_code=502,
            content={"error": _error_label(error), "message": f"Failed to connect to {target}"},
        )
    elif isinstance(error, httpx.TimeoutException):
        return JSONResponse(
            status_code=502,
            content={"error": _error_label(error), "message": f"Timeout connecting to {target}"},
        )
    elif isinstance(error, httpx.HTTPStatusError):
        return JSONResponse(
            status_code=error.response.status_code,
            content=error.response.json() if error.response.content else {"error": str(error)},
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"error": str(error), "message": "Internal server error"},
        )


# Model load/unload can take a long time (minutes for large models).
# Use a 10-minute timeout for these operations.
_MODEL_OP_TIMEOUT = 600.0


async def _proxy_buffered_request(
    method: str,
    path: str,
    target_url: str,
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> JSONResponse:
    """Proxy a buffered request and return a JSONResponse.

    Handles all proxy exceptions, logs them, and returns appropriate error responses.
    """
    try:
        response = await proxy_request(method, f"/{path}", body=body, headers=headers, target_url=target_url, timeout=timeout)
        return _make_json_response(response)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return _handle_proxy_error(method, path, target_url, e)


# Polling interval for client disconnect detection in streaming endpoints.
_DISCONNECT_POLL_INTERVAL = 0.1


async def _watch_disconnect(request: Request, cancel_event: asyncio.Event) -> None:
    """Poll the request for client disconnect and set cancel_event when detected.

    Starlette exposes no push-based disconnect notification, so we poll
    `request.is_disconnected()` at a short interval until the client
    disconnects or the event is set externally (which exits the loop).
    """
    while not cancel_event.is_set():
        try:
            disconnected = await request.is_disconnected()
        except Exception as e:
            # If disconnect detection fails transiently, keep polling rather
            # than tearing down the stream spuriously. Log at debug level so
            # failures are not silently swallowed but also don't spam logs.
            logger.debug("Disconnect poll failed: %s", e)
            disconnected = False
        if disconnected:
            cancel_event.set()
            return
        await asyncio.sleep(_DISCONNECT_POLL_INTERVAL)


# --- Routes ---

@app.get("/")
async def serve_index():
    """Serve the main HTML page."""
    return FileResponse(f"{get_static_dir()}/index.html")


@app.get("/favicon.ico")
async def serve_favicon():
    """Serve the favicon."""
    return FileResponse(f"{get_static_dir()}/favicon.svg", media_type="image/svg+xml")


@app.options("/")
async def serve_index_options():
    """Handle CORS preflight for root."""
    return JSONResponse(status_code=200, content={})


# --- Model Management (proxied to LM Studio native API) ---

@app.get("/proxy/{path:path}")
async def proxy_get(request: Request, path: str):
    """Proxy GET requests to the backend API."""
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else None
    target_url = _resolve_target_url(request)
    return await _proxy_buffered_request("GET", path, target_url, headers=headers)


@app.options("/proxy/{path:path}")
async def proxy_options(path: str):
    """Handle CORS preflight requests for proxy endpoints."""
    return JSONResponse(status_code=200, content={})


@app.post("/proxy/{path:path}")
async def proxy_post(request: Request, path: str):
    """Proxy POST requests to the backend API.

    Handles both buffered responses and streaming (SSE) responses.
    Streaming is detected by the path containing 'chat/completions'
    and the body containing stream: true.
    """
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else None
    body = await request.json()
    target_url = _resolve_target_url(request)

    # Detect streaming: chat completions with stream=true
    is_stream = "chat/completions" in path and body.get("stream", False)

    if is_stream:
        return await _proxy_stream_response(request, path, target_url, body, headers)

    # Model load/unload operations can take minutes
    op_timeout = _MODEL_OP_TIMEOUT if "models/load" in path or "models/unload" in path else None

    return await _proxy_buffered_request("POST", path, target_url, body=body, headers=headers, timeout=op_timeout)


async def _proxy_stream_response(
    request: Request,
    path: str,
    target_url: str,
    body: dict,
    headers: Optional[dict],
) -> StreamingResponse:
    """Return a streaming SSE response proxied from the backend.

    Stops early if the downstream client disconnects; breaking out of the
    upstream iterator closes the proxied connection via proxy_stream_iter's
    cleanup.
    """
    async def event_generator() -> AsyncIterable[str]:
        try:
            async for chunk in proxy_stream_iter("POST", f"/{path}", body=body, headers=headers, target_url=target_url):
                if await request.is_disconnected():
                    break
                yield chunk.decode("utf-8", errors="replace")
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            trace_logger.log_server_error("POST", path, e)
            yield f"data: {type(e).__name__}: {e}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Chat endpoint (Pydantic AI powered) ---

@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Chat with Pydantic AI Agent.

    Handles tool calls automatically on the backend while forwarding
    normalized content, thinking, and tool lifecycle events to the frontend.

    Request body:
        {
            "model": str,
            "messages": [{role, content}, ...],
            "temperature": float,
            "system_prompt": str (optional),
            "toolCallEnabled": bool (optional, default false),
        }

    Response: SSE stream of structured content, thinking, tool lifecycle,
    cancellation, error, and usage events.
    """
    body = await request.json()
    target_url = _resolve_target_url(request)
    api_token = request.headers.get("Authorization", "")
    if api_token.startswith("Bearer "):
        api_token = api_token[7:]

    model = body.get("model", "")
    messages = body.get("messages", [])
    temperature = body.get("temperature", 0.7)
    system_prompt = body.get("system_prompt", "")
    tool_call_enabled = body.get("toolCallEnabled", False)

    # Create agent for this request
    agent = ChatAgent(base_url=target_url, api_key=api_token)

    # Set when the client disconnects; the agent's stream loop checks this and
    # exits cleanly, cancelling the upstream HTTP stream via the agent event
    # stream context manager's cleanup. This is the cooperative-cancellation
    # channel between the disconnect watcher and the agent.
    cancel_event = asyncio.Event()

    async def chat_generator() -> AsyncIterable[str]:
        # Watch for client disconnect in the background and signal cancellation.
        watcher = asyncio.create_task(_watch_disconnect(request, cancel_event))
        try:
            cancelled_emitted = False
            async for payload in agent.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                system_prompt=system_prompt,
                tool_call_enabled=tool_call_enabled,
                cancel_event=cancel_event,
            ):
                yield f"data: {json.dumps(payload)}\n\n"
                if isinstance(payload, dict) and payload.get("__cancelled__"):
                    cancelled_emitted = True
                # If the client has disconnected (cancel_event set), stop pulling
                # from the agent. Emit a cancellation marker unless the agent
                # already emitted one, so the frontend can distinguish
                # cancellation from a normal end or error.
                if cancel_event.is_set():
                    if not cancelled_emitted:
                        yield f'data: {json.dumps({"__cancelled__": True})}\n\n'
                    break
        except Exception as e:
            trace_logger.log_server_error("POST", "/api/chat", e)
            yield f"data: {json.dumps({'__error__': str(e)})}\n\n"
        finally:
            # Stop the disconnect watcher and await its cleanup so we don't
            # leak a background task after the response completes or cancels.
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        chat_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Tool endpoints (kept for backward compatibility) ---

@app.get("/api/tools")
async def list_tools():
    """Return available tool schemas in OpenAI-compatible format."""
    return JSONResponse(content=get_tool_schemas())


# --- SSE trace log streaming ---

@app.get("/api/trace-logs")
async def trace_logs(request: Request):
    """Stream trace logs as SSE.

    On connect, sends buffered recent logs, then streams new logs in real-time.
    Detects client disconnect and cleans up the subscriber.

    A wrapper generator ensures cleanup runs even if the inner generator is
    garbage-collected before its finally block executes (possible under certain
    ASGI server behaviours where abandoned StreamingResponse bodies are not
    properly closed).
    """
    sub_id, sub_iter = await log_streamer.subscribe()

    async def log_generator():
        # Send recent buffered entries for catch-up
        recent = log_streamer.get_recent(50)
        for entry in recent:
            yield f"data: {json.dumps(entry)}\n\n"
            if await request.is_disconnected():
                return

        # Stream new entries in real-time
        try:
            async for sse_line in sub_iter:
                yield sse_line
                if await request.is_disconnected():
                    break
        except asyncio.CancelledError:
            return

    async def safe_generator():
        """Wrapper that guarantees subscriber cleanup even if the inner
        generator is garbage-collected without proper closure."""
        try:
            async for chunk in log_generator():
                yield chunk
        except asyncio.CancelledError:
            raise
        finally:
            await log_streamer.remove(sub_id)

    return StreamingResponse(
        safe_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/api/tool-exec")
async def execute_tool_endpoint(request: Request):
    """Execute a registered tool with the given arguments."""
    body = await request.json()
    name = body.get("name", "")
    arguments = body.get("arguments", {})
    try:
        result = await execute_tool(name, arguments)
        return JSONResponse(content={"result": result})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        trace_logger.log_server_error("POST", "/api/tool-exec", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


# --- File Upload ---

@app.post("/api/upload")
async def upload_file(request: Request):
    """Handle file uploads for multimodal chat.

    Accepts multipart form data with a 'file' field.
    Returns base64-encoded content and MIME type for the frontend
    to include in multimodal messages.
    """
    form = await request.form()
    file = form.get("file")
    if not file:
        return JSONResponse(status_code=400, content={"error": "No file provided"})

    # Read and encode
    content = await file.read()
    b64 = base64.b64encode(content).decode("ascii")
    mime_type = file.content_type or "application/octet-stream"

    # Determine if it's an image
    is_image = mime_type.startswith("image/")

    return JSONResponse(content={
        "filename": file.filename or "unnamed",
        "mimeType": mime_type,
        "size": len(content),
        "base64": b64,
        "isImage": is_image,
    })


# --- Static file serving ---
app.mount("/static", StaticFiles(directory=get_static_dir()), name="static")

# --- CORS middleware (added last so it wraps all routes and mounts) ---
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", _HEADER_LM_STUDIO_URL],
    expose_headers=["Content-Type", "X-Accel-Buffering"],
)


def run() -> None:
    """Start the FastAPI server using uvicorn."""
    host = get_host()
    port = get_port()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
