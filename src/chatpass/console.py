"""Loopback-only management shell composed with the canonical inference gateway."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import gateway
from .login import login
from .oauth import OAuthError
from .storage import StateStore, StorageError

_BODY_LIMIT = 4096
_BOOTSTRAP_SECONDS = 300
_SESSION_SECONDS = 8 * 3600
_COOKIE = "chatpass_admin"
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_LOGIN_CODES = frozenset({
    "login_timeout", "login_already_running", "consent_denied", "registration_incomplete",
    "identity_verification_unavailable", "login_required", "authorization_failed",
})


def _error(status: int, code: str) -> gateway._GatewayError:
    return gateway._GatewayError(status, code, "Console request could not be completed.")


def _equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeError:
        return False


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


async def _json_body(request: Request, *, timeout: float, empty: bool = False) -> dict[str, Any]:
    try:
        async with asyncio.timeout(timeout):
            raw = await gateway._read_body(request, _BODY_LIMIT)
    except TimeoutError:
        raise _error(408, "request_timeout") from None
    if empty and not raw:
        return {}
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise _error(415, "unsupported_media_type")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=gateway._reject_constant,
                          parse_float=gateway._finite_float)
        if not isinstance(body, dict):
            raise ValueError("Expected object")
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise _error(400, "invalid_json") from None


class _ConsoleState:
    def __init__(self, token: str) -> None:
        self.started = time.monotonic()
        self.bootstrap = token
        self.bootstrap_expires = self.started + _BOOTSTRAP_SECONDS
        self.session: str | None = None
        self.session_expires = 0.0
        self.csrf: str | None = None
        self.login_state = "idle"
        self.login_code: str | None = None
        self.login_task: asyncio.Task[None] | None = None
        self.storage_tasks: set[asyncio.Task[Any]] = set()
        self.models_active = False
        self.models_task: asyncio.Task[JSONResponse] | None = None
        self.closing = False
        self.total = self.failed = self.active = 0

    def authenticated(self, request: Request) -> bool:
        # Reject duplicate cookies, rather than selecting an ambiguous authority.
        cookies = request.headers.getlist("cookie")
        values = [part.strip().partition("=")[2] for header in cookies
                  for part in header.split(";") if part.strip().partition("=")[0] == _COOKIE]
        return (len(values) == 1 and self.session is not None
                and time.monotonic() < self.session_expires
                and _equal(values[0], self.session))

    async def storage(self, operation: Callable[[], Any], timeout: float) -> Any:
        if self.closing or self.storage_tasks:
            raise _error(429, "console_busy")
        task = asyncio.create_task(asyncio.to_thread(operation))
        self.storage_tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self.storage_tasks.discard(done)
            if not done.cancelled():
                done.exception()  # Consume failures even if the HTTP waiter disappeared.

        task.add_done_callback(finished)
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(task)
        except TimeoutError:
            raise _error(503, "storage_unavailable") from None


class _ConsoleBoundary:
    """ASGI boundary: stream counters live until final close, not response headers."""

    def __init__(self, app: ASGIApp, *, state: _ConsoleState, host: str) -> None:
        self.app, self.state, self.host = app, state, host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        path = scope["path"]
        inference = path == "/v1" or path.startswith("/v1/")
        admin = path == "/admin/api" or path.startswith("/admin/api/")
        failed = False
        frames = None
        stream_completed = False
        response_started = False
        if inference:
            self.state.total += 1
            self.state.active += 1

        async def guarded_send(message: Message) -> None:
            nonlocal failed, frames, stream_completed, response_started
            if message["type"] == "http.response.start":
                response_started = True
                failed = message["status"] >= 400
                headers = list(message.get("headers", []))
                if inference:
                    if any(key.lower() == b"content-type" and b"text/event-stream" in value
                           for key, value in headers):
                        frames = gateway._SSEFrames()
                else:
                    names = {key.lower().encode() for key in _SECURITY_HEADERS}
                    headers = [(key, value) for key, value in headers if key.lower() not in names]
                    headers.extend((key.lower().encode(), value.encode())
                                   for key, value in _SECURITY_HEADERS.items())
                    message = dict(message, headers=headers)
            elif message["type"] == "http.response.body" and frames is not None:
                try:
                    async for frame in frames.feed(message.get("body", b""),
                                                   final=not message.get("more_body", False)):
                        kind = gateway._event_kind(frame)
                        if kind == "error":
                            failed = True
                        elif kind == "response.completed":
                            stream_completed = True
                except gateway._GatewayError:
                    failed = True
                    frames = None
            await send(message)

        try:
            if request.headers.getlist("host") != [self.host]:
                raise _error(403, "invalid_host")
            if not inference:
                try:
                    peer = scope.get("client")
                    local = peer is not None and ip_address(peer[0]).is_loopback
                except ValueError:
                    local = False
                if not local:
                    raise _error(403, "invalid_client")
            if admin:
                origins = request.headers.getlist("origin")
                expected = "http://" + self.host
                unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
                if (origins and origins != [expected]) or (unsafe and origins != [expected]):
                    raise _error(403, "invalid_origin")
                bootstrap = path == "/admin/api/session" and request.method == "POST"
                if not bootstrap:
                    if not self.state.authenticated(request):
                        raise _error(401, "admin_session_required")
                    if unsafe:
                        csrf = request.headers.getlist("x-chatpass-csrf")
                        if (len(csrf) != 1 or self.state.csrf is None
                                or not _equal(csrf[0], self.state.csrf)):
                            raise _error(403, "invalid_csrf")
            await self.app(scope, receive, guarded_send)
        except gateway._GatewayError as exc:
            await exc.response()(scope, receive, guarded_send)
        except Exception:
            failed = True
            if inference or response_started:
                raise
            await _error(500, "console_error").response()(scope, receive, guarded_send)
        except BaseException:
            failed = True
            raise
        finally:
            if inference:
                if frames is not None and not stream_completed:
                    failed = True
                self.state.active -= 1
                self.state.failed += int(failed)


def credential_status(state: dict[str, Any], *, now: float | None = None) -> dict[str, bool]:
    """Pure sanitized credential presentation shared with the CLI status command."""
    current = state.get("credentials", {})
    return {
        "gateway_key_configured": bool(state.get("gateway_key_hash")),
        "account_authorized": bool(current) and not state.get("login_required", False)
        and not state.get("refresh_in_progress", False),
        "access_token_expired": bool(current) and current.get("expires_at", 0)
        <= (time.time() if now is None else now),
        "pending_token_validation": "pending_tokens" in state,
        "login_required": bool(state.get("login_required") or state.get("refresh_in_progress")),
        "registration_configured": bool(state.get("registration", {}).get("client_id")),
    }


def _snapshot(store: StateStore) -> dict[str, bool]:
    return credential_status(store.read() if store.path.exists() else {})


def create_console_app(settings: gateway.GatewaySettings, store: StateStore,
                       credentials: gateway.Credentials, *, client: httpx.AsyncClient | None = None,
                       login_runner: Callable[..., None] | None = None,
                       bootstrap_token: str | None = None) -> FastAPI:
    """Compose the public static shell and cookie-authorized local management API."""
    address = ip_address(settings.host)
    if not address.is_loopback:
        raise ValueError("Console requires a literal loopback host")
    host = f"[{address}]:{settings.port}" if address.version == 6 else f"{address}:{settings.port}"
    state = _ConsoleState(bootstrap_token if bootstrap_token is not None else secrets.token_urlsafe(32))
    app = gateway.create_app(settings, credentials, store.verify_gateway_key, client=client)
    app.state.console_bootstrap_token = state.bootstrap
    app.state.console_state = state
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with original_lifespan(application):
            try:
                yield
            finally:
                state.closing = True
                # to_thread cancellation does not stop a login/browser/storage worker.
                # Keep ownership and wait through its existing bounded login deadline.
                with anyio.CancelScope(shield=True):
                    tasks = list(state.storage_tasks)
                    if state.login_task is not None:
                        tasks.append(state.login_task)
                    if state.models_task is not None:
                        tasks.append(state.models_task)
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                state.bootstrap = ""
                state.session = state.csrf = None
                app.state.console_bootstrap_token = None

    app.router.lifespan_context = lifespan
    app.add_middleware(_ConsoleBoundary, state=state, host=host)

    @app.exception_handler(gateway._GatewayError)
    async def console_error(request: Request, exc: gateway._GatewayError) -> JSONResponse:
        return exc.response()

    @app.post("/admin/api/session")
    async def exchange(request: Request) -> JSONResponse:
        body = await _json_body(request, timeout=settings.timeout_seconds)
        token = body.get("token")
        if (set(body) != {"token"} or not isinstance(token, str) or not state.bootstrap
                or time.monotonic() >= state.bootstrap_expires
                or not _equal(token, state.bootstrap)):
            raise _error(401, "invalid_bootstrap")
        state.bootstrap = ""
        app.state.console_bootstrap_token = None
        state.session, state.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        state.session_expires = time.monotonic() + _SESSION_SECONDS
        response = JSONResponse({"csrf_token": state.csrf})
        response.set_cookie(_COOKIE, state.session, max_age=_SESSION_SECONDS, path="/admin",
                            httponly=True, samesite="strict")
        return response

    @app.get("/admin/api/session")
    async def session() -> dict[str, str]:
        return {"csrf_token": state.csrf}

    @app.get("/admin/api/status")
    async def status() -> JSONResponse:
        try:
            result = await state.storage(lambda: _snapshot(store), settings.timeout_seconds)
        except gateway._GatewayError:
            raise
        except Exception:
            raise _error(503, "storage_unavailable") from None
        return JSONResponse(result | {
            "base_url": "http://" + host + "/v1",
            "uptime_seconds": max(0, int(time.monotonic() - state.started)),
            "requests_total": state.total, "requests_failed": state.failed,
            "requests_active": state.active,
            "login": {"state": state.login_state, "code": state.login_code},
        })

    @app.post("/admin/api/key")
    async def issue_key(request: Request) -> dict[str, str]:
        body = await _json_body(request, timeout=settings.timeout_seconds)
        action = body.get("action")
        if set(body) != {"action"} or action not in ("init", "rotate"):
            raise _error(400, "invalid_action")
        operation = store.initialize_gateway_key if action == "init" else store.rotate_gateway_key
        try:
            key = await state.storage(operation, settings.timeout_seconds)
        except StorageError as exc:
            if action == "init" and str(exc) == "A gateway key already exists. Use key rotate to replace it.":
                raise _error(409, "gateway_key_exists") from None
            raise _error(503, "storage_unavailable") from None
        except gateway._GatewayError:
            raise
        except Exception:
            raise _error(503, "storage_unavailable") from None
        return {"key": key}

    async def run_login() -> None:
        try:
            await asyncio.to_thread(login_runner or login, store, emit=lambda *_: None)
        except OAuthError as exc:
            state.login_state = "failed"
            state.login_code = exc.code if exc.code in _LOGIN_CODES else "authorization_failed"
        except StorageError:
            state.login_state, state.login_code = "failed", "storage_unavailable"
        except Exception:
            state.login_state, state.login_code = "failed", "authorization_failed"
        else:
            state.login_state, state.login_code = "succeeded", None

    @app.post("/admin/api/login")
    async def start_login(request: Request) -> JSONResponse:
        body = await _json_body(request, timeout=settings.timeout_seconds, empty=True)
        if body:
            raise _error(400, "invalid_json")
        if state.closing:
            raise _error(503, "console_unavailable")
        if state.login_task is not None and not state.login_task.done():
            raise _error(409, "login_already_running")
        state.login_state, state.login_code = "running", None
        state.login_task = asyncio.create_task(run_login())
        return JSONResponse({"state": "running"}, status_code=202)

    async def query_models() -> JSONResponse:
        credential_tasks: list[asyncio.Task[str]] = []

        class CatalogCredentials:
            async def get_access_token(self) -> str:
                # Retain canonical credential workers even when the helper's timeout
                # expires: cancelling to_thread cannot terminate its real thread.
                task = asyncio.create_task(credentials.get_access_token())
                credential_tasks.append(task)
                return await asyncio.shield(task)

        try:
            if app.state.upstream_client is None:
                raise _error(503, "gateway_unavailable")
            return JSONResponse(await gateway.fetch_model_catalog(
                CatalogCredentials(), app.state.upstream_client, timeout=settings.timeout_seconds,
                body_limit=settings.max_body_bytes))
        except gateway._GatewayError:
            raise
        except Exception:
            raise gateway._upstream_error(502) from None
        finally:
            with anyio.CancelScope(shield=True):
                if credential_tasks:
                    await asyncio.gather(*credential_tasks, return_exceptions=True)
            state.models_active = False

    @app.get("/admin/api/models")
    async def models() -> JSONResponse:
        if state.closing or state.models_active:
            raise _error(429, "console_busy")
        state.models_active = True
        task = asyncio.create_task(query_models())
        state.models_task = task
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                return await asyncio.shield(task)
        except TimeoutError:
            # The HTTP response has a deadline; ownership/admission persists until
            # the helper and its canonical credential worker actually terminate.
            raise gateway._timeout_error() from None

    static = Path(__file__).with_name("static")

    async def shell() -> FileResponse:
        return FileResponse(static / "index.html")

    app.add_api_route("/", shell, methods=["GET"], include_in_schema=False)
    app.add_api_route("/admin", shell, methods=["GET"], include_in_schema=False)
    app.add_api_route("/admin/", shell, methods=["GET"], include_in_schema=False)

    @app.get("/assets/{name:path}")
    async def asset(name: str) -> FileResponse:
        if name not in {"console.js", "console.css", "vendor/tabler.min.css", "vendor/LICENSE.tabler"}:
            raise _error(404, "not_found")
        return FileResponse(static / name)

    return app
