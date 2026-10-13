"""Authenticated, bounded gateway for the verified direct-sharing HTTP preview.

Inference is streaming-only. Multi-turn state is explicit input history, not a
server-side previous_response_id. Credential lifecycle belongs to the caller.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, Protocol, TypeVar

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

UPSTREAM_BASE_URL = "https://api.openai.com/v1"
MAX_SSE_FRAME_BYTES = 1048576
MAX_STREAM_BYTES = 64 * 1048576
MAX_RETRY_AFTER_SECONDS = 86400
_UNSUPPORTED_FIELDS = frozenset({
    "previous_response_id", "background", "conversation", "max_output_tokens",
    "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt",
    "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs",
    "top_p", "truncation", "user",
})
_FRAME_END = re.compile(rb"(?:\r\n|\r(?!\n)|\n){2}")
_ERROR_TERMINALS = frozenset({"response.failed", "response.incomplete", "error"})


class GatewaySettings(Protocol):
    upstream_base_url: str
    max_body_bytes: int
    max_concurrency: int
    timeout_seconds: float


class Credentials(Protocol):
    async def get_access_token(self) -> str: ...


class _GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str, *, param: str | None = None,
                 error_type: str = "invalid_request_error") -> None:
        self.status = status
        self.error = {"message": message, "type": error_type, "param": param, "code": code}

    def response(self, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"error": self.error}, status_code=self.status, headers=headers)

    def sse(self) -> bytes:
        return b"event: error\ndata: " + json.dumps({"error": self.error}).encode() + b"\n\n"


def _upstream_error(status: int) -> _GatewayError:
    descriptions = {
        400: ("upstream_invalid_request", "Upstream rejected the request."),
        401: ("upstream_authentication_failed", "Upstream authorization failed."),
        403: ("upstream_access_denied", "Upstream denied account eligibility or access."),
        429: ("upstream_rate_limit", "Upstream quota or rate limit exceeded."),
    }
    code, message = descriptions.get(status, ("upstream_error", "Upstream request failed."))
    return _GatewayError(status if status in descriptions else 502, code, message,
                         error_type="upstream_error")


def _invalid_upstream() -> _GatewayError:
    return _GatewayError(502, "invalid_upstream_response", "Upstream returned an invalid response.",
                         error_type="upstream_error")


def _timeout_error() -> _GatewayError:
    return _GatewayError(504, "upstream_timeout", "Upstream request timed out.",
                         error_type="upstream_error")


def _stream_error(code: str = "stream_interrupted") -> _GatewayError:
    return _GatewayError(502, code, "Upstream stream did not complete successfully.",
                         error_type="upstream_error")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _load_json(data: bytes) -> Any:
    # Reject non-finite constants and exponent overflow; decode strict UTF-8.
    return json.loads(data.decode("utf-8"), parse_constant=_reject_constant,
                      parse_float=_finite_float)


def _unsupported(param: str) -> _GatewayError:
    # param is always a fixed gateway field name, never arbitrary client text.
    return _GatewayError(400, "unsupported_capability",
                         "This capability is not supported by the upstream HTTP preview.",
                         param=param)


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _GatewayError(400, "invalid_json", "Request JSON must be an object.")
    for field in sorted(_UNSUPPORTED_FIELDS):
        if field in payload:
            raise _unsupported(field)
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise _GatewayError(400, "invalid_model", "A nonempty model string is required.", param="model")
    if "input" not in payload or not isinstance(payload["input"], (str, list)):
        raise _GatewayError(400, "invalid_input", "Input must be a string or input-history array.",
                             param="input")
    stream = payload.get("stream", True)
    if not isinstance(stream, bool):
        raise _GatewayError(400, "invalid_stream", "Stream must be a boolean.", param="stream")
    if not stream:
        raise _unsupported("stream")
    result = dict(payload)
    if isinstance(result["input"], str):
        result["input"] = [{"role": "user", "content": result["input"]}]
    else:
        for item in result["input"]:
            if not isinstance(item, dict):
                raise _GatewayError(400, "invalid_input", "Input-history items must be objects.",
                                     param="input")
            if item.get("role") == "system":
                raise _unsupported("input")
    if "tools" in result:
        if not isinstance(result["tools"], list):
            raise _GatewayError(400, "invalid_tools", "Tools must be an array.", param="tools")
        for tool in result["tools"]:
            if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom"):
                raise _unsupported("tools")
    result["store"] = False
    result["stream"] = True
    return result


def _model_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise _invalid_upstream()
    models = []
    for model in payload["models"]:
        if not isinstance(model, dict) or not isinstance(model.get("visibility"), str):
            raise _invalid_upstream()
        if model["visibility"] != "list":
            continue
        slug, name = model.get("slug"), model.get("display_name")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               for value in (slug, name)):
            raise _invalid_upstream()
        models.append({"id": slug, "object": "model", "owned_by": "openai", "name": name})
    return {"object": "list", "data": models}


async def _read_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise _GatewayError(413, "request_too_large", "Request body exceeds the configured limit.")
        body.extend(chunk)
    return bytes(body)


_Result = TypeVar("_Result")


async def _until_disconnect(request: Request,
                            operation: Coroutine[Any, Any, _Result]) -> _Result:
    """Race a post-body upstream operation against disconnect, without polling."""
    async def disconnected() -> None:
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    work = asyncio.create_task(operation)
    listener = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({work, listener}, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return await work
        raise _GatewayError(400, "client_disconnected", "Client disconnected before the response.")
    finally:
        for task in (work, listener):
            if not task.done():
                task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(work, listener, return_exceptions=True)


async def _read_upstream(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > limit:
            raise _invalid_upstream()
        body.extend(chunk)
    return bytes(body)


def _retry_after(response: httpx.Response) -> dict[str, str]:
    value = response.headers.get("retry-after", "")
    if re.fullmatch(r"[0-9]{1,5}", value) and int(value) <= MAX_RETRY_AFTER_SECONDS:
        return {"Retry-After": value}
    return {}


class _SSEFrames:
    """Observe bounded complete frames without rewriting successful event bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self._scan_from = 0

    def feed(self, chunk: bytes, *, final: bool = False) -> AsyncIterator[bytes]:
        # Yield with downstream backpressure, without allocating a frame list.
        return self._feed(chunk, final=final)

    async def _feed(self, chunk: bytes, *, final: bool) -> AsyncIterator[bytes]:
        for offset in range(0, max(1, len(chunk)), 65536):
            self.buffer.extend(chunk[offset:offset + 65536])
            while (match := _FRAME_END.search(self.buffer, self._scan_from)) is not None:
                end = match.end()
                if not final and end == len(self.buffer) and self.buffer[end - 1] == 13:
                    # A trailing CR may acquire LF in the next read. Do not emit
                    # a terminal frame early and discard that original LF byte.
                    break
                if end > MAX_SSE_FRAME_BYTES:
                    raise _stream_error("stream_limit_exceeded")
                frame = bytes(self.buffer[:end])
                del self.buffer[:end]
                self._scan_from = 0
                yield frame
            if len(self.buffer) > MAX_SSE_FRAME_BYTES:
                raise _stream_error("stream_limit_exceeded")
            # A newline pair is at most four bytes. Retain only its possible
            # overlapping prefix for search, avoiding quadratic one-byte reads.
            self._scan_from = max(0, len(self.buffer) - 3)


def _event_kind(frame: bytes) -> str | None:
    try:
        text = frame.decode("utf-8")
        event, data = None, []
        for line in re.split(r"\r\n|\r|\n", text):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
        if not data:
            return None  # Valid comments/heartbeats, with no dispatched data event.
        payload = _load_json("\n".join(data).encode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SSE data must be an object")
        kind = payload.get("type")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("Invalid event type")
        if event and kind and event != kind:
            raise ValueError("Contradictory SSE event and JSON type")
        nested = payload.get("response")
        if (payload.get("error") is not None or
                isinstance(nested, dict) and nested.get("error") is not None):
            return "error"
        if event in _ERROR_TERMINALS or kind in _ERROR_TERMINALS:
            return "error"
        if event == "response.completed" or kind == "response.completed":
            if nested is not None and not isinstance(nested, dict):
                raise ValueError("Invalid completed response")
            if isinstance(nested, dict) and nested.get("status", "completed") != "completed":
                return "error"
            return "response.completed"
        return kind or event
    except (ValueError, UnicodeError, RecursionError):
        raise _stream_error("invalid_upstream_stream") from None


async def _stream_response(response: httpx.Response, deadline: float) -> AsyncIterator[bytes]:
    frames = _SSEFrames()

    async def read_frames() -> AsyncIterator[bytes]:
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_STREAM_BYTES:
                raise _stream_error("stream_limit_exceeded")
            async for frame in frames.feed(chunk):
                yield frame
        async for frame in frames.feed(b"", final=True):
            yield frame  # EOF can resolve a trailing CR, never an incomplete frame.

    try:
        async with asyncio.timeout_at(deadline):
            async for frame in read_frames():
                kind = _event_kind(frame)
                if kind == "error":
                    yield _stream_error("upstream_stream_failed").sse()
                    return
                yield frame
                if kind == "response.completed":
                    return  # The upstream, not this gateway, declared completion.
            raise _stream_error()  # EOF without upstream completion is never success.
    except (TimeoutError, httpx.TimeoutException):
        yield _timeout_error().sse()
    except _GatewayError as error:
        yield error.sse()
    except httpx.HTTPError:
        yield _stream_error().sse()


class _ClosingStreamingResponse(StreamingResponse):
    """Close even when ASGI send fails while the generator is suspended at yield."""

    def __init__(self, content: AsyncIterator[bytes], close: Callable[[], Any]) -> None:
        super().__init__(content, media_type="text/event-stream",
                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        self._close = close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self._close()


def create_app(settings: GatewaySettings, credentials: Credentials,
               key_verifier: Callable[[str], bool], *, client: httpx.AsyncClient | None = None
               ) -> FastAPI:
    """Create a status-only health and authenticated inference app, not a key manager.

    Injected clients remain caller-owned. Production must run the ASGI lifespan
    to create/close the private client; clients never control the upstream URL.
    """
    if settings.upstream_base_url.rstrip("/") != UPSTREAM_BASE_URL:
        raise ValueError("Only the fixed official upstream base URL is supported")
    if (type(settings.max_body_bytes) is not int or settings.max_body_bytes <= 0
            or type(settings.max_concurrency) is not int or settings.max_concurrency <= 0):
        raise ValueError("Request size and concurrency limits must be positive integers")
    timeout = float(settings.timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive and finite")
    body_limit, concurrency_limit = settings.max_body_bytes, settings.max_concurrency
    active = 0  # Modified without awaits, on the app event loop: no queued semaphore wait.
    verifications: set[asyncio.Task[bool]] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if client is not None:
            yield
        else:
            async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(timeout),
                                         trust_env=False) as owned:
                app.state.upstream_client = owned
                try:
                    yield
                finally:
                    app.state.upstream_client = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.upstream_client = client

    async def authenticate(request: Request) -> None:
        values = request.headers.getlist("authorization")
        match = re.fullmatch(r"Bearer ([^\s]+)", values[0], re.IGNORECASE) if len(values) == 1 else None
        valid = False
        if match:
            if len(verifications) >= concurrency_limit:
                raise _GatewayError(429, "gateway_busy", "Gateway concurrency limit reached.",
                                     error_type="rate_limit_error")

            async def verify() -> bool:
                try:
                    return await asyncio.to_thread(key_verifier, match[1])
                except Exception:
                    return False  # Never expose verifier exception text.

            task = asyncio.create_task(verify())
            verifications.add(task)  # Strong reference until the real worker finishes.
            task.add_done_callback(verifications.discard)
            try:
                async with asyncio.timeout(timeout):
                    # Cancellation of a waiting HTTP request cannot stop a worker
                    # thread or free its admission slot while it is still running.
                    valid = await asyncio.shield(task)
            except Exception:
                valid = False
        if not valid:
            raise _GatewayError(401, "invalid_api_key", "A valid gateway Bearer key is required.",
                                 error_type="authentication_error")

    async def proxy(request: Request, *, models: bool) -> JSONResponse | StreamingResponse:
        nonlocal active
        response = None
        handed_off = False
        acquired = False
        released = False

        async def close() -> None:
            nonlocal active, released
            if released:
                return
            released = True
            try:
                if response is not None:
                    await response.aclose()
            finally:
                if acquired:
                    active -= 1

        try:
            await authenticate(request)  # No request body or credentials before this point.
            if active >= concurrency_limit:
                raise _GatewayError(429, "gateway_busy", "Gateway concurrency limit reached.",
                                     error_type="rate_limit_error")
            active += 1
            acquired = True
            deadline = asyncio.get_running_loop().time() + timeout
            async with asyncio.timeout_at(deadline):
                payload = None
                if not models:
                    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if media_type != "application/json":
                        raise _GatewayError(415, "unsupported_media_type", "Content-Type must be application/json.")
                    raw = await _read_body(request, body_limit)
                    try:
                        payload = _validate_payload(_load_json(raw))
                    except (ValueError, UnicodeError, RecursionError):
                        raise _GatewayError(400, "invalid_json", "Request body is not valid JSON.") from None
                async def open_response() -> None:
                    nonlocal response
                    try:
                        token = await credentials.get_access_token()
                        if not isinstance(token, str) or not token or re.search(r"\s", token):
                            raise RuntimeError("Invalid credentials")
                    except Exception:
                        raise _GatewayError(503, "credentials_unavailable",
                                             "Upstream credentials are unavailable; sign in locally.",
                                             error_type="authentication_error") from None
                    upstream = app.state.upstream_client
                    if upstream is None:
                        raise _GatewayError(503, "gateway_unavailable", "Gateway lifecycle is not running.",
                                             error_type="server_error")
                    # Fresh request: no injected client defaults or downstream headers.
                    headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "identity",
                               "Accept": "application/json" if models else "text/event-stream"}
                    content = None
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                        content = json.dumps(payload, allow_nan=False).encode("utf-8")
                    upstream_request = httpx.Request(
                        "GET" if models else "POST",
                        UPSTREAM_BASE_URL + ("/models" if models else "/responses"),
                        headers=headers, content=content,
                        extensions={"timeout": httpx.Timeout(timeout).as_dict()},
                    )
                    response = await upstream.send(upstream_request, stream=True, auth=None,
                                                   follow_redirects=False)

                await _until_disconnect(request, open_response())
                assert response is not None
                if response.status_code != 200:
                    # Read zero error-body bytes: token-echoing bodies are never needed.
                    return _upstream_error(response.status_code).response(
                        _retry_after(response) if response.status_code == 429 else None)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    # The request expressly requires identity encoding. Reject a
                    # compressed response before decoding any possible zip bomb.
                    raise _invalid_upstream()
                if models:
                    try:
                        data = _load_json(await _until_disconnect(
                            request, _read_upstream(response, body_limit)))
                    except (ValueError, UnicodeError, RecursionError):
                        raise _invalid_upstream() from None
                    return JSONResponse(_model_catalog(data))
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != "text/event-stream":
                    raise _invalid_upstream()
                result = _ClosingStreamingResponse(_stream_response(response, deadline), close)
                handed_off = True
                return result
        except _GatewayError as error:
            return error.response({"WWW-Authenticate": "Bearer"} if error.status == 401 else None)
        except (TimeoutError, httpx.TimeoutException):
            return _timeout_error().response()
        except httpx.HTTPError:
            return _upstream_error(502).response()
        finally:
            if not handed_off:
                with anyio.CancelScope(shield=True):
                    await close()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/responses", response_model=None)
    async def responses(request: Request) -> JSONResponse | StreamingResponse:
        return await proxy(request, models=False)

    @app.get("/v1/models", response_model=None)
    async def models(request: Request) -> JSONResponse | StreamingResponse:
        return await proxy(request, models=True)

    return app
