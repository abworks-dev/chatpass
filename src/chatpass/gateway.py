"""Authenticated, bounded gateway for the verified direct-sharing HTTP preview.

The upstream stays streaming-only; client protocols are normalized at the edge.
Multi-turn state is explicit history, never a server-side previous_response_id.
Credential lifecycle belongs to the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, Protocol, TypeVar

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from .adapters import AdapterError, normalize_chat, normalize_responses
from .bridges import BridgeError, chat_completion, chat_stream, collect_response
from .oauth import OAuthError
from .upstream_errors import SIWC_STATUS, read_diagnostics
from .upstream_errors import diagnostics as safe_diagnostics

UPSTREAM_BASE_URL = "https://api.openai.com/v1"
MAX_SSE_FRAME_BYTES = 1048576
MAX_STREAM_BYTES = 64 * 1048576
MAX_RETRY_AFTER_SECONDS = 86400
_FRAME_END = re.compile(rb"(?:\r\n|\r(?!\n)|\n){2}")
_ERROR_TERMINALS = frozenset({"response.failed", "response.incomplete", "error"})
_LOGGER = logging.getLogger(__name__)
_DIAGNOSTIC_EVENTS = _ERROR_TERMINALS | frozenset({
    "response.created", "response.in_progress", "response.completed",
    "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done",
    "response.function_call_arguments.delta", "response.function_call_arguments.done",
    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
})
_DIAGNOSTIC_CODES = frozenset({
    "server_error", "server_is_overloaded", "rate_limit_exceeded", "insufficient_quota",
    "invalid_api_key", "invalid_request_error", "model_not_found",
    "context_length_exceeded", "content_filter", "max_output_tokens",
})


class _StreamDiagnostics:
    """Fixed-label metadata; unknown codes require explicit one-shot local consent."""

    def __init__(self, unknown_code_reporter: Callable[[str, str], None] | None = None) -> None:
        self._unknown_code_reporter = unknown_code_reporter
        self._unknown_code: str | None = None
        self.request_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.last_event = "none"
        self.upstream_code = "none"
        self.frames = 0
        self.bytes = 0

    def observe(self, frame: bytes, payload: dict[str, Any] | None, event: str | None) -> None:
        self.frames += 1
        self.bytes += len(frame)
        # Consume the canonical parser's validated object, never reparse the stream
        # or retain arbitrary upstream strings.
        if payload is None:
            return
        kind = payload.get("type") or event
        self._unknown_code = None
        self.last_event = kind if kind in _DIAGNOSTIC_EVENTS else "other"
        nested = payload.get("response")
        source = nested if isinstance(nested, dict) else payload
        error = source.get("error")
        if error is None:
            error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if code is None and kind == "error":
            code = payload.get("code")
        error_code = code  # One-shot consent covers only code, not incomplete reason text.
        details = source.get("incomplete_details")
        if code is None and isinstance(details, dict):
            code = details.get("reason")
        # A malformed or malicious metadata value is a fixed label too.
        if code is not None:
            self.upstream_code = code if isinstance(code, str) and code in _DIAGNOSTIC_CODES else "other"
            if (self.upstream_code == "other" and error_code is not None
                    and (kind in _ERROR_TERMINALS or error is not None)
                    and self._unknown_code_reporter is not None):
                # Narrow, bounded identifier syntax: no controls, whitespace, URLs,
                # objects or free-form text. Invalid values never escape as text.
                self._unknown_code = (
                    code if isinstance(code, str) and
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", code, flags=re.ASCII)
                    else "invalid_or_oversized_code"
                )

    def failure(self, category: str) -> None:
        if (category == "upstream_stream_failed" and self._unknown_code is not None
                and self._unknown_code_reporter is not None):
            self._unknown_code_reporter(self.request_id, self._unknown_code)
            self._unknown_code = None
        _LOGGER.warning(
            "ChatPass stream failure request_id=%s category=%s elapsed_ms=%d "
            "last_event=%s upstream_code=%s frames=%d bytes=%d",
            self.request_id, category, (time.monotonic() - self.started) * 1000,
            self.last_event, self.upstream_code, self.frames, self.bytes,
        )


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
        self.headers: dict[str, str] = {}
        self.error = {"message": message, "type": error_type, "param": param, "code": code}

    def response(self, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"error": self.error}, status_code=self.status,
                            headers=self.headers | (headers or {}))

    def sse(self) -> bytes:
        return b"event: error\ndata: " + json.dumps({"error": self.error}).encode() + b"\n\n"


def _upstream_error(status: int, metadata: dict | None = None) -> _GatewayError:
    descriptions = {
        400: ("upstream_invalid_request", "Upstream rejected the request."),
        401: ("upstream_authentication_failed", "Upstream authorization failed."),
        403: ("upstream_access_denied", "Upstream denied account eligibility or access."),
        429: ("upstream_rate_limit", "Upstream quota or rate limit exceeded."),
    }
    code, message = descriptions.get(status, ("upstream_error", "Upstream request failed."))
    metadata = safe_diagnostics({"error": metadata or {},
                                 "request_id": (metadata or {}).get("request_id")})
    if metadata.get("code") == "server_is_overloaded":
        message = "上游服务繁忙，请稍后手动重试。"
    result = _GatewayError(status if status in {400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504} else 502,
                           metadata.get("code", code), message, param=metadata.get("param"),
                           error_type="upstream_error")
    if metadata.get("request_id"):
        result.error["request_id"] = metadata["request_id"]
    return result


def _known_http_error(response: httpx.Response) -> _GatewayError:
    error = _upstream_error(response.status_code, safe_diagnostics(
        {}, header_request_id=response.headers.get("x-request-id")))
    if response.status_code == 429:
        error.headers = _retry_after(response)
    return error


def _credential_error(exc: OAuthError) -> _GatewayError:
    allowed = {"login_required", "pending_token_validation", "identity_verification_unavailable",
               "refresh_not_yet_allowed", "scope_not_authorized", "account_mismatch",
               "invalid_grant", "invalid_client", "invalid_refresh_token", "refresh_token_expired",
               "refresh_token_reused", "refresh_token_invalidated", "token_expired"}
    code = exc.code if exc.code in allowed else "credentials_unavailable"
    return _GatewayError(503, code, "Local authorization needs attention; check management status.",
                         error_type="authentication_error")


def _invalid_upstream(*, header: str | None = None, value: str = "") -> _GatewayError:
    message = "Upstream returned an invalid response."
    if header == "content-encoding":
        # Fixed labels only: arbitrary upstream header/body values may contain secrets.
        label = value if value in {"gzip", "br", "deflate", "zstd"} else "other"
        message = f"Upstream returned unsupported content encoding ({label}); expected identity."
    elif header == "content-type":
        label = value if value in {"application/json", "text/html", "text/plain"} else (
            "missing" if not value else "other"
        )
        message = f"Upstream returned unexpected content type ({label}); expected text/event-stream."
    return _GatewayError(502, "invalid_upstream_response", message,
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


def _frame_error(frame: bytes, header_request_id: str | None = None) -> _GatewayError:
    metadata = safe_diagnostics({}, header_request_id=header_request_id)
    if len(frame) <= 16384:
        try:
            text = frame.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            data = "\n".join(line.partition(":")[2].removeprefix(" ")
                             for line in text.split("\n") if line.partition(":")[0] == "data")
            metadata = safe_diagnostics(_load_json(data.encode()), header_request_id=header_request_id)
        except (ValueError, UnicodeError, RecursionError):
            pass
    if "code" in metadata:
        return _upstream_error(SIWC_STATUS[metadata["code"]], metadata)
    error = _stream_error("upstream_stream_failed")
    if "request_id" in metadata:
        error.error["request_id"] = metadata["request_id"]
    return error


def _bridge_error(error: BridgeError) -> _GatewayError:
    if error.code in SIWC_STATUS:
        return _upstream_error(SIWC_STATUS[error.code], {"code": error.code, "param": error.param,
                                                       "request_id": error.request_id})
    result = _GatewayError(504 if error.code == "upstream_timeout" else 502,
                           error.code, error.message, error_type="upstream_error")
    result.error.update(safe_diagnostics({"request_id": error.request_id}))
    return result


async def _chat_stream_response(frames: AsyncIterator[bytes], *, model: str,
                                include_usage: bool) -> AsyncIterator[bytes]:
    try:
        async for chunk in chat_stream(frames, model=model, include_usage=include_usage):
            yield chunk
    except BridgeError as error:
        yield _bridge_error(error).sse()  # No [DONE] or success finish on failure.
    finally:
        await frames.aclose()


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


def _event_kind(frame: bytes, *, require_response_envelope: bool = False,
                diagnostics: _StreamDiagnostics | None = None) -> str | None:
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
            if diagnostics is not None:
                diagnostics.observe(frame, None, event)
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
        if require_response_envelope:
            # A missing media type requires positive protocol evidence, not a
            # response.* prefix or an event-only name with arbitrary JSON.
            valid_statuses = {
                "response.created": {"queued", "in_progress"},
                "response.in_progress": {"in_progress"},
                "response.completed": {"completed"},
            }
            if (kind not in valid_statuses or not isinstance(nested, dict) or
                    nested.get("object") != "response" or
                    not isinstance(nested.get("id"), str) or not nested["id"].strip() or
                    not isinstance(nested.get("status"), str) or
                    nested["status"] not in valid_statuses[kind] or
                    not isinstance(nested.get("output"), list)):
                raise ValueError("Missing Responses SSE envelope")
        if diagnostics is not None:
            diagnostics.observe(frame, payload, event)
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


async def _probe_headerless_stream(response: httpx.Response) -> AsyncIterator[bytes]:
    """Require a bounded Responses SSE prelude before trusting absent media metadata."""
    chunks = response.aiter_bytes()
    frames = _SSEFrames()
    prefix: list[bytes] = []
    size = 0
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > MAX_SSE_FRAME_BYTES:
                raise _invalid_upstream(header="content-type", value="")
            prefix.append(chunk)
            async for frame in frames.feed(chunk):
                kind = _event_kind(frame, require_response_envelope=True)
                if kind is None:
                    continue  # Comments alone never establish a Responses stream.
                if not kind.startswith("response."):
                    raise _invalid_upstream(header="content-type", value="")
                return _replay_chunks(prefix, chunks)
        # EOF can resolve a trailing CR, but cannot establish success by itself.
        async for frame in frames.feed(b"", final=True):
            kind = _event_kind(frame, require_response_envelope=True)
            if kind is not None and kind.startswith("response."):
                return _replay_chunks(prefix, chunks)
        raise _invalid_upstream(header="content-type", value="")
    except BaseException:
        await chunks.aclose()
        raise


async def _replay_chunks(prefix: list[bytes], chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        for chunk in prefix:
            yield chunk
        async for chunk in chunks:
            yield chunk
    finally:
        await chunks.aclose()


async def _stream_response(response: httpx.Response, deadline: float,
                           chunks: AsyncIterator[bytes] | None = None, *,
                           diagnostics: _StreamDiagnostics | None = None) -> AsyncIterator[bytes]:
    frames = _SSEFrames()
    diagnostics = diagnostics or _StreamDiagnostics()

    async def read_frames() -> AsyncIterator[bytes]:
        total = 0
        async for chunk in chunks if chunks is not None else response.aiter_bytes():
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
                kind = _event_kind(frame, diagnostics=diagnostics)
                if kind == "error":
                    diagnostics.failure("upstream_stream_failed")
                    yield _frame_error(frame, response.headers.get("x-request-id")).sse()
                    return
                yield frame
                if kind == "response.completed":
                    return  # The upstream, not this gateway, declared completion.
            diagnostics.failure("eof_without_completion")
            yield _stream_error().sse()  # EOF without upstream completion is never success.
    except (TimeoutError, httpx.TimeoutException):
        diagnostics.failure("upstream_timeout")
        yield _timeout_error().sse()
    except _GatewayError as error:
        diagnostics.failure(error.error["code"])
        yield error.sse()
    except httpx.HTTPError:
        diagnostics.failure("transport_error")
        yield _stream_error().sse()
    finally:
        if chunks is not None:
            await chunks.aclose()


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


async def fetch_model_catalog(credentials: Credentials, client: httpx.AsyncClient | None,
                              *, timeout: float, body_limit: int,
                              on_error_headers: Callable[[_GatewayError], None] | None = None) -> dict[str, Any]:
    """The single upstream model protocol owner, shared by API and local console."""
    response = None
    try:
        async with asyncio.timeout(timeout):
            try:
                token = await credentials.get_access_token()
                if not isinstance(token, str) or not token or re.search(r"\s", token):
                    raise ValueError
            except _GatewayError:
                raise
            except OAuthError as exc:
                raise _credential_error(exc) from None
            except Exception:
                raise _GatewayError(503, "credentials_unavailable",
                                    "Upstream credentials are unavailable; sign in locally.",
                                    error_type="authentication_error") from None
            if client is None:
                raise _GatewayError(503, "gateway_unavailable", "Gateway lifecycle is not running.",
                                    error_type="server_error")
            request = httpx.Request(
                "GET", UPSTREAM_BASE_URL + "/models",
                headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "identity",
                         "Accept": "application/json"},
                extensions={"timeout": httpx.Timeout(timeout).as_dict()},
            )
            response = await client.send(request, stream=True, auth=None, follow_redirects=False)
            if response.status_code != 200:
                if on_error_headers is not None:
                    on_error_headers(_known_http_error(response))
                error = _upstream_error(response.status_code, await read_diagnostics(response))
                if response.status_code == 429:
                    error.headers = _retry_after(response)
                raise error
            encoding = response.headers.get("content-encoding", "identity").lower()
            if encoding != "identity":
                raise _invalid_upstream(header="content-encoding", value=encoding)
            try:
                return _model_catalog(_load_json(await _read_upstream(response, body_limit)))
            except (ValueError, UnicodeError, RecursionError):
                raise _invalid_upstream() from None
    except (TimeoutError, httpx.TimeoutException):
        if response is not None and response.status_code != 200:
            raise _known_http_error(response) from None
        raise _timeout_error() from None
    except httpx.HTTPError:
        raise _upstream_error(502) from None
    finally:
        if response is not None:
            with anyio.CancelScope(shield=True):
                await response.aclose()


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
    compatibility_mode = getattr(settings, "compatibility_mode", "compatible")
    if compatibility_mode not in ("compatible", "strict"):
        raise ValueError("Compatibility mode must be compatible or strict")
    timeout = float(settings.timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive and finite")
    body_limit, concurrency_limit = settings.max_body_bytes, settings.max_concurrency
    active = 0  # Modified without awaits, on the app event loop: no queued semaphore wait.
    closing = False
    verifications: set[asyncio.Task[bool]] = set()
    credential_tasks: set[asyncio.Task[str]] = set()
    # Snapshot an explicit local opt-in at app creation; one capture per app.
    # Shared across concurrent requests; claim the budget before any log call.
    unknown_code_armed = os.environ.get("CHATPASS_DIAG_UNKNOWN_CODE_ONCE") == "1"

    def report_unknown_code(request_id: str, code: str) -> None:
        nonlocal unknown_code_armed
        if not unknown_code_armed:
            return
        unknown_code_armed = False
        _LOGGER.warning(
            "ChatPass one-shot code diagnostic request_id=%s upstream_code=%s capture=consumed",
            request_id, code,
        )

    class AdmittedCredentials:
        async def get_access_token(self) -> str:
            if closing:
                raise _GatewayError(503, "gateway_unavailable", "Gateway is shutting down.")
            if len(credential_tasks) >= concurrency_limit:
                raise _GatewayError(429, "gateway_busy", "Gateway concurrency limit reached.",
                                    error_type="rate_limit_error")
            task = asyncio.create_task(credentials.get_access_token())
            credential_tasks.add(task)

            def finished(done: asyncio.Task[str]) -> None:
                credential_tasks.discard(done)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
            # A timed-out HTTP waiter cannot terminate its keyring/refresh thread.
            return await asyncio.shield(task)

    admitted_credentials = AdmittedCredentials()

    async def drain_workers() -> None:
        nonlocal closing
        closing = True
        # HTTP timeout/cancellation cannot stop OS-keyring or refresh threads.
        # Join their real owners before callers may dispose OAuth resources.
        with anyio.CancelScope(shield=True):
            tasks = list(verifications) + list(credential_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if client is not None:
            try:
                yield
            finally:
                await drain_workers()
        else:
            async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(timeout),
                                         trust_env=False) as owned:
                app.state.upstream_client = owned
                try:
                    yield
                finally:
                    await drain_workers()
                    app.state.upstream_client = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.upstream_client = client
    app.state.credential_tasks = credential_tasks

    async def authenticate(request: Request) -> None:
        if closing:
            raise _GatewayError(503, "gateway_unavailable", "Gateway is shutting down.")
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

    async def proxy(request: Request, *, models: bool,
                    chat: bool = False) -> JSONResponse | StreamingResponse:
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
            diagnostics = _StreamDiagnostics(report_unknown_code if unknown_code_armed else None)
            deadline = asyncio.get_running_loop().time() + timeout
            if models:
                # The model helper owns its deadline and its response snapshot.
                # A second enclosing timer would cancel its optional error read
                # before it can preserve an already-known HTTP status.
                catalog = await _until_disconnect(request, fetch_model_catalog(
                    admitted_credentials, app.state.upstream_client, timeout=timeout,
                    body_limit=body_limit))
                return JSONResponse(catalog)
            async with asyncio.timeout_at(deadline):
                payload = None
                if not models:
                    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if media_type != "application/json":
                        raise _GatewayError(415, "unsupported_media_type", "Content-Type must be application/json.")
                    raw = await _read_body(request, body_limit)
                    try:
                        normalize = normalize_chat if chat else normalize_responses
                        adapted = normalize(_load_json(raw), strict=compatibility_mode == "strict")
                        payload = adapted.payload
                    except (ValueError, UnicodeError, RecursionError):
                        raise _GatewayError(400, "invalid_json", "Request body is not valid JSON.") from None
                async def open_response() -> None:
                    nonlocal response
                    try:
                        token = await admitted_credentials.get_access_token()
                        if not isinstance(token, str) or not token or re.search(r"\s", token):
                            raise RuntimeError("Invalid credentials")
                    except _GatewayError:
                        raise
                    except OAuthError as exc:
                        raise _credential_error(exc) from None
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
                               "Accept": "text/event-stream"}
                    content = None
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                        content = json.dumps(payload, allow_nan=False).encode("utf-8")
                    upstream_request = httpx.Request(
                        "POST", UPSTREAM_BASE_URL + "/responses",
                        headers=headers, content=content,
                        extensions={"timeout": httpx.Timeout(timeout).as_dict()},
                    )
                    response = await upstream.send(upstream_request, stream=True, auth=None,
                                                   follow_redirects=False)

                await _until_disconnect(request, open_response())
                assert response is not None
                if response.status_code != 200:
                    # Optional bounded metadata only; never forward remote text.
                    return _upstream_error(response.status_code, await read_diagnostics(response)).response(
                        _retry_after(response) if response.status_code == 429 else None)
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding != "identity":
                    # The request expressly requires identity encoding. Reject a
                    # compressed response before decoding any possible zip bomb.
                    raise _invalid_upstream(header="content-encoding", value=encoding)
                content_type = response.headers.get("content-type")
                media_type = (content_type or "").split(";", 1)[0].strip().lower()
                if content_type is not None and media_type != "text/event-stream":
                    raise _invalid_upstream(header="content-type", value=media_type)
                chunks = None
                if content_type is None:
                    chunks = await _until_disconnect(request, _probe_headerless_stream(response))
                frames = _stream_response(response, deadline, chunks, diagnostics=diagnostics)
                headers = {"X-ChatPass-Compatibility": compatibility_mode,
                           "X-ChatPass-Request-ID": diagnostics.request_id}
                if adapted.ignored_fields:
                    headers["X-ChatPass-Ignored-Fields"] = ",".join(adapted.ignored_fields)
                if not adapted.stream:
                    async def collect() -> dict[str, Any]:
                        try:
                            return await collect_response(frames)
                        finally:
                            await frames.aclose()
                    completed = await _until_disconnect(request, collect())
                    result_body = chat_completion(completed, model=payload["model"]) if chat else completed
                    return JSONResponse(result_body, headers=headers)
                stream = (_chat_stream_response(frames, model=payload["model"],
                                                include_usage=adapted.include_usage) if chat else frames)
                result = _ClosingStreamingResponse(stream, close)
                result.headers.update(headers)
                handed_off = True
                return result
        except AdapterError as error:
            return _GatewayError(400, error.code, error.message, param=error.param).response()
        except BridgeError as error:
            return _bridge_error(error).response()
        except _GatewayError as error:
            return error.response({"WWW-Authenticate": "Bearer"} if error.status == 401 else None)
        except (TimeoutError, httpx.TimeoutException):
            if response is not None and response.status_code != 200:
                return _known_http_error(response).response()
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

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        return await proxy(request, models=False, chat=True)

    @app.get("/v1/models", response_model=None)
    async def models(request: Request) -> JSONResponse | StreamingResponse:
        return await proxy(request, models=True)

    return app
