"""Isolated local-console security and lifecycle tests; no real login/keyring."""

import asyncio
import hashlib
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from chatpass.console import create_console_app
from chatpass.oauth import OAuthError
from chatpass.storage import StateStore, StorageError

ORIGIN = "http://127.0.0.1:19840"
BOOTSTRAP = "test-bootstrap-capability"
SECRET = "not-for-browser-status"


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[service, name] = value


class Credentials:
    def __init__(self):
        self.calls = 0

    async def get_access_token(self):
        self.calls += 1
        return SECRET


def settings(**overrides):
    values = dict(host="127.0.0.1", port=19840, upstream_base_url="https://api.openai.com/v1",
                  timeout_seconds=1.0, max_body_bytes=4096, max_concurrency=2)
    return SimpleNamespace(**(values | overrides))


@pytest.fixture
async def setup(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    credentials = Credentials()
    upstream_calls = []

    def handler(request):
        upstream_calls.append(request)
        return httpx.Response(200, json={"models": [
            {"visibility": "list", "slug": "model-id", "display_name": "<script>remote</script>"},
            {"visibility": "hidden", "slug": "hidden", "display_name": "Hidden"},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_console_app(settings(), store, credentials, client=upstream,
                                 bootstrap_token=BOOTSTRAP)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
                yield SimpleNamespace(app=app, store=store, credentials=credentials, client=client,
                                      upstream=upstream, calls=upstream_calls)


async def authorize(client):
    response = await client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                 headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-ChatPass-CSRF": response.json()["csrf_token"]}


async def test_bootstrap_cookie_single_use_and_no_privileged_json(setup):
    response = await setup.client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                       headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert set(response.json()) == {"csrf_token"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/admin" in cookie and "Max-Age=28800" in cookie
    session = setup.app.state.console_state.session
    assert session not in response.text and BOOTSTRAP not in response.text
    assert setup.app.state.console_bootstrap_token is None
    replay = await setup.client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                     headers={"Origin": ORIGIN})
    assert replay.status_code == 401
    current = await setup.client.get("/admin/api/session")
    assert current.json() == response.json()


async def test_bootstrap_and_session_expiration(setup):
    state = setup.app.state.console_state
    state.bootstrap_expires = time.monotonic() - 1
    expired = await setup.client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                      headers={"Origin": ORIGIN})
    assert expired.status_code == 401
    state.bootstrap_expires = time.monotonic() + 1
    await authorize(setup.client)
    state.session_expires = time.monotonic() - 1
    assert (await setup.client.get("/admin/api/status")).status_code == 401


@pytest.mark.parametrize("host", ["evil.example:19840", "localhost:19840", "127.0.0.1.evil:19840",
                                  "2130706433:19840", "127.0.0.1:19841", "127.0.0.1."])
@pytest.mark.parametrize("path", ["/", "/assets/console.js", "/admin/api/status", "/v1/models"])
async def test_rebinding_hosts_rejected_everywhere(setup, host, path):
    response = await setup.client.get(path, headers={"Host": host})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"
    assert setup.credentials.calls == 0


async def test_duplicate_host_and_origin_rejected(setup):
    response = await setup.client.get("/", headers=[("Host", "127.0.0.1:19840"),
                                                   ("Host", "127.0.0.1:19840")])
    assert response.status_code == 403
    response = await setup.client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                       headers=[("Origin", ORIGIN), ("Origin", ORIGIN)])
    assert response.status_code == 403


@pytest.mark.parametrize("peer", ["192.0.2.1", "unknown"])
async def test_nonloopback_peers_rejected(setup, peer):
    transport = httpx.ASGITransport(app=setup.app, client=(peer, 123))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        assert (await client.get("/")).status_code == 403
        assert (await client.get("/admin/api/status")).status_code == 403


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "https://127.0.0.1:19840",
                                    "http://127.0.0.1:19840/"])
async def test_bootstrap_requires_exact_origin(setup, origin):
    headers = {} if origin is None else {"Origin": origin}
    assert (await setup.client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                    headers=headers)).status_code == 403


async def test_admin_bearer_is_not_management_authority(setup):
    key = setup.store.initialize_gateway_key()
    for path in ("/admin/api/session", "/admin/api/status", "/admin/api/models"):
        assert (await setup.client.get(path, headers={"Authorization": "Bearer " + key})).status_code == 401
    assert setup.credentials.calls == 0


async def test_cross_origin_get_and_csrf_controls(setup):
    headers = await authorize(setup.client)
    assert (await setup.client.get("/admin/api/status", headers={"Origin": "http://evil.example"})).status_code == 403
    for bad in ({}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-ChatPass-CSRF": "wrong"},
                {"X-ChatPass-CSRF": headers["X-ChatPass-CSRF"]}):
        response = await setup.client.post("/admin/api/key", json={"action": "init"}, headers=bad)
        assert response.status_code == 403
    duplicated = [(key, value) for key, value in headers.items()] + [
        ("X-ChatPass-CSRF", headers["X-ChatPass-CSRF"])]
    assert (await setup.client.post("/admin/api/key", json={"action": "init"},
                                    headers=duplicated)).status_code == 403
    assert not setup.store.path.exists()


@pytest.mark.parametrize("body,status", [
    (b'{"token":"x","token":"test-bootstrap-capability"}', 400),
    (b'{"token":"test-bootstrap-capability","nested":{"x":1,"x":2}}', 400),
    (b'{"token":NaN}', 400), (b'{"token":1e999}', 400), (b'[]', 400), (b'\xff', 400),
    (b'{"token":"' + b'x' * 4096 + b'"}', 413),
    ('{"token":"\u00e9"}'.encode(), 401), (b'{"token":"\\ud800"}', 401),
])
async def test_bootstrap_json_bounded_and_unambiguous(setup, body, status):
    response = await setup.client.post("/admin/api/session", content=body,
                                       headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert response.status_code == status
    assert setup.app.state.console_state.session is None


async def test_chunked_body_limit_ignores_forged_content_length(setup):
    async def chunks():
        yield b'{"token":"'
        yield b'x' * 4096
        yield b'"}'

    response = await setup.client.post("/admin/api/session", content=chunks(), headers={
        "Origin": ORIGIN, "Content-Type": "application/json", "Content-Length": "1"})
    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/admin/api/key", "/admin/api/login"])
async def test_every_unsafe_api_body_bounded(setup, path):
    headers = await authorize(setup.client)
    response = await setup.client.post(path, content=b" " * 4097,
                                       headers=headers | {"Content-Type": "application/json"})
    assert response.status_code == 413
    assert setup.app.state.console_state.login_task is None
    assert not setup.store.path.exists()


async def test_duplicate_session_cookies_cannot_select_authority(setup):
    await authorize(setup.client)
    session = setup.app.state.console_state.session
    response = await setup.client.get("/admin/api/status", headers={
        "Cookie": f"chatpass_admin=wrong; chatpass_admin={session}"})
    assert response.status_code == 401
    response = await setup.client.get("/admin/api/status", headers=[
        ("Cookie", "chatpass_admin=" + session), ("Cookie", "chatpass_admin=" + session)])
    assert response.status_code == 401


async def test_storage_key_worker_also_holds_shared_admission(setup, monkeypatch):
    headers = await authorize(setup.client)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = setup.store.initialize_gateway_key
    calls = []

    def blocked():
        calls.append(1)
        loop.call_soon_threadsafe(entered.set)
        release.wait(3)
        return original()

    monkeypatch.setattr(setup.store, "initialize_gateway_key", blocked)
    first = asyncio.create_task(setup.client.post("/admin/api/key", json={"action": "init"}, headers=headers))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await setup.client.get("/admin/api/status")).status_code == 429
        assert (await setup.client.post("/admin/api/key", json={"action": "rotate"}, headers=headers)).status_code == 429
        assert calls == [1]
    finally:
        tasks = list(setup.app.state.console_state.storage_tasks)
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    # A cancelled key response still completes one canonical issuance, not rotation.
    assert setup.store.read()["gateway_key_hash"]
    assert (await setup.client.post("/admin/api/key", json={"action": "init"}, headers=headers)).status_code == 409


async def test_key_init_and_rotation_are_explicit(setup):
    headers = await authorize(setup.client)
    first = await setup.client.post("/admin/api/key", json={"action": "init"}, headers=headers)
    assert first.status_code == 200
    key = first.json()["key"]
    assert setup.store.verify_gateway_key(key)
    conflict = await setup.client.post("/admin/api/key", json={"action": "init"}, headers=headers)
    assert conflict.status_code == 409
    assert setup.store.verify_gateway_key(key)
    rotated = await setup.client.post("/admin/api/key", json={"action": "rotate"}, headers=headers)
    assert rotated.status_code == 200
    assert rotated.json()["key"] != key
    assert not setup.store.verify_gateway_key(key)
    assert setup.store.verify_gateway_key(rotated.json()["key"])
    status = await setup.client.get("/admin/api/status")
    assert key not in status.text and rotated.json()["key"] not in status.text
    assert (await setup.client.post("/admin/api/key", json={"action": "delete"}, headers=headers)).status_code == 400


async def test_status_only_sanitized_actual_fields_and_admin_excluded_from_metrics(setup):
    await authorize(setup.client)
    with setup.store.transaction() as state:
        state.update(credentials={"access_token": SECRET, "subject": SECRET, "expires_at": 1},
                     registration={"client_id": SECRET}, pending_tokens={"body": SECRET},
                     login_required=True, gateway_key_hash=hashlib.sha256(SECRET.encode()).hexdigest())
    response = await setup.client.get("/admin/api/status")
    body = response.json()
    assert body["gateway_key_configured"] and body["registration_configured"]
    assert body["access_token_expired"] and body["pending_token_validation"]
    assert body["login_required"] and not body["account_authorized"]
    assert body["base_url"] == ORIGIN + "/v1"
    assert body["requests_total"] == body["requests_active"] == body["requests_failed"] == 0
    assert body["login"] == {"state": "idle", "code": None}
    assert SECRET not in response.text and str(setup.store.directory) not in response.text
    assert setup.credentials.calls == 0 and setup.calls == []
    failed = await setup.client.get("/v1/models")
    assert failed.status_code == 401
    body = (await setup.client.get("/admin/api/status")).json()
    assert body["requests_total"] == body["requests_failed"] == 1
    assert body["requests_active"] == 0


async def test_storage_failure_is_not_false_healthy_status(setup, monkeypatch):
    await authorize(setup.client)
    setup.store.initialize_gateway_key()

    def broken():
        raise RuntimeError(SECRET)

    monkeypatch.setattr(setup.store, "read", broken)
    response = await setup.client.get("/admin/api/status")
    assert response.status_code == 503 and SECRET not in response.text
    assert response.json()["error"]["code"] == "storage_unavailable"


async def test_management_security_headers_on_shell_assets_and_errors(setup):
    for path in ("/", "/assets/console.js", "/admin/api/status"):
        response = await setup.client.get(path)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "access-control-allow-origin" not in response.headers
        assert BOOTSTRAP not in response.text and SECRET not in response.text
        if path == "/":
            assert response.status_code == 200
    assert (await setup.client.get("/assets/state.enc")).status_code == 404


async def test_models_canonical_explicit_and_not_inference_metrics(setup):
    await authorize(setup.client)
    assert setup.calls == []
    response = await setup.client.get("/admin/api/models")
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": [{"id": "model-id", "object": "model",
        "owned_by": "openai", "name": "<script>remote</script>"}]}
    assert len(setup.calls) == 1
    request = setup.calls[0]
    assert str(request.url) == "https://api.openai.com/v1/models"
    assert request.headers["authorization"] == "Bearer " + SECRET
    assert SECRET not in response.text
    assert (await setup.client.get("/admin/api/status")).json()["requests_total"] == 0


@pytest.mark.parametrize("code,expected", [(401, "upstream_authentication_failed"),
                                           (403, "upstream_access_denied"),
                                           (429, "upstream_rate_limit"), (500, "upstream_error")])
async def test_models_errors_are_canonical_and_sanitized(setup, code, expected):
    await authorize(setup.client)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(code, text=SECRET, headers={"Retry-After": "3"})))
    async with upstream:
        setup.app.state.upstream_client = upstream
        response = await setup.client.get("/admin/api/models")
    assert response.status_code == code
    assert response.json()["error"]["code"] == expected
    assert SECRET not in response.text
    if code == 429:
        assert response.headers["retry-after"] == "3"


async def test_models_admission_survives_cancelled_http(setup):
    await authorize(setup.client)
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"models": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        setup.app.state.upstream_client = upstream
        first = asyncio.create_task(setup.client.get("/admin/api/models"))
        await asyncio.wait_for(entered.wait(), 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await setup.client.get("/admin/api/models")).status_code == 429
        release.set()
        await setup.app.state.console_state.models_task
        assert (await setup.client.get("/admin/api/models")).status_code == 200


@pytest.mark.parametrize("outcome", ["cancel", "timeout"])
async def test_storage_admission_survives_http_cancellation_and_timeout(setup, monkeypatch, outcome):
    await authorize(setup.client)
    setup.store.initialize_gateway_key()
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()
    calls = []
    original = setup.store.read

    def blocked():
        calls.append(1)
        loop.call_soon_threadsafe(entered.set)
        release.wait(3)
        return original()

    monkeypatch.setattr(setup.store, "read", blocked)
    first = asyncio.create_task(setup.client.get("/admin/api/status"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if outcome == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            assert (await first).status_code == 503
        assert (await setup.client.get("/admin/api/status")).status_code == 429
        assert calls == [1]
    finally:
        tasks = list(setup.app.state.console_state.storage_tasks)
        release.set()
        await asyncio.gather(first, *tasks, return_exceptions=True)


async def test_login_single_running_and_shutdown_joins_real_worker(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    loop = asyncio.get_running_loop()
    entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    calls = []

    def runner(actual_store, *, emit):
        assert actual_store is store
        calls.append(1)
        assert emit(SECRET) is None
        loop.call_soon_threadsafe(entered.set)
        try:
            release.wait(3)
        finally:
            finished.set()

    app = create_console_app(settings(), store, Credentials(), login_runner=runner,
                             bootstrap_token=BOOTSTRAP)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        headers = await authorize(client)
        first = await client.post("/admin/api/login", json={}, headers=headers)
        assert first.status_code == 202 and first.json() == {"state": "running"}
        await asyncio.wait_for(entered.wait(), 1)
        assert (await client.post("/admin/api/login", json={}, headers=headers)).status_code == 409
        assert (await client.get("/admin/api/status")).json()["login"]["state"] == "running"
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await asyncio.sleep(0)
            assert not shutdown.done() and not finished.is_set()
            assert calls == [1]
        finally:
            release.set()
            await asyncio.wait_for(shutdown, 2)
        assert finished.is_set()
        assert app.state.console_state.login_state == "succeeded"
        assert app.state.console_state.session is None
        assert app.state.upstream_client is None


@pytest.mark.parametrize("failure,code", [(OAuthError("login_timeout"), "login_timeout"),
                                         (OAuthError(SECRET), "authorization_failed"),
                                         (StorageError(SECRET), "storage_unavailable"),
                                         (RuntimeError(SECRET), "authorization_failed")])
async def test_login_failure_codes_are_bounded_and_sanitized(setup, monkeypatch, failure, code):
    def runner(store, *, emit):
        raise failure

    monkeypatch.setattr("chatpass.console.login", runner)
    headers = await authorize(setup.client)
    assert (await setup.client.post("/admin/api/login", headers=headers)).status_code == 202
    await setup.app.state.console_state.login_task
    response = await setup.client.get("/admin/api/status")
    assert response.json()["login"] == {"state": "failed", "code": code}
    assert SECRET not in response.text


async def test_metrics_follow_actual_asgi_stream_lifetime_and_error_terminal(setup):
    await authorize(setup.client)
    key = setup.store.initialize_gateway_key()
    entered, release = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: response.created\ndata: {"type":"response.created"}\n\n'
            entered.set()
            await release.wait()
            yield b'event: error\ndata: {"type":"error","error":{"message":"private"}}\n\n'

        async def aclose(self):
            pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Stream()))) as upstream:
        setup.app.state.upstream_client = upstream
        stream = asyncio.create_task(setup.client.post("/v1/responses", json={"model": "m", "input": "hi"},
                                                       headers={"Authorization": "Bearer " + key}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            status = (await setup.client.get("/admin/api/status")).json()
            assert status["requests_total"] == status["requests_active"] == 1
            assert status["requests_failed"] == 0
        finally:
            release.set()
            await stream
        status = (await setup.client.get("/admin/api/status")).json()
        assert status["requests_total"] == status["requests_failed"] == 1
        assert status["requests_active"] == 0


async def test_model_timeout_returns_while_real_credential_thread_keeps_slot(tmp_path):
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()
    calls = []

    class BlockedCredentials:
        async def get_access_token(self):
            def worker():
                calls.append(1)
                loop.call_soon_threadsafe(entered.set)
                release.wait(3)
                return SECRET
            return await asyncio.to_thread(worker)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"models": []}))) as upstream:
        app = create_console_app(settings(timeout_seconds=0.04),
                                 StateStore(tmp_path, backend=MemoryKeyring()),
                                 BlockedCredentials(), client=upstream, bootstrap_token=BOOTSTRAP)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
                await authorize(client)
                first = asyncio.create_task(client.get("/admin/api/models"))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    response = await asyncio.wait_for(first, 0.5)
                    assert response.status_code == 504
                    assert response.json()["error"]["code"] == "upstream_timeout"
                    assert (await client.get("/admin/api/models")).status_code == 429
                    assert calls == [1] and app.state.console_state.models_active
                finally:
                    release.set()
                    await asyncio.gather(app.state.console_state.models_task, return_exceptions=True)
                assert not app.state.console_state.models_active


async def test_admin_partial_body_has_absolute_deadline(tmp_path):
    app = create_console_app(settings(timeout_seconds=0.04),
                             StateStore(tmp_path, backend=MemoryKeyring()),
                             Credentials(), bootstrap_token=BOOTSTRAP)
    messages = []
    waiting = asyncio.Event()
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b'{"token":"', "more_body": True}
        await waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/admin/api/session",
             "raw_path": b"/admin/api/session", "query_string": b"", "root_path": "",
             "headers": [(b"host", b"127.0.0.1:19840"), (b"origin", ORIGIN.encode()),
                         (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 123), "server": ("127.0.0.1", 19840)}
    await asyncio.wait_for(app(scope, receive, send), 0.5)
    assert messages[0]["status"] == 408
    assert b"request_timeout" in messages[1]["body"]
    assert app.state.console_state.session is None


async def test_login_survives_cancellation_during_http_response(tmp_path):
    release = threading.Event()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    calls = []

    def runner(store, *, emit):
        calls.append(1)
        loop.call_soon_threadsafe(entered.set)
        release.wait(3)

    app = create_console_app(settings(), StateStore(tmp_path, backend=MemoryKeyring()),
                             Credentials(), login_runner=runner, bootstrap_token=BOOTSTRAP)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            headers = await authorize(client)
            session = app.state.console_state.session
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "http", "path": "/admin/api/login",
                     "raw_path": b"/admin/api/login", "query_string": b"", "root_path": "",
                     "headers": [(b"host", b"127.0.0.1:19840"), (b"origin", ORIGIN.encode()),
                                 (b"x-chatpass-csrf", headers["X-ChatPass-CSRF"].encode()),
                                 (b"cookie", ("chatpass_admin=" + session).encode())],
                     "client": ("127.0.0.1", 123), "server": ("127.0.0.1", 19840)}

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                if message["type"] == "http.response.start":
                    raise asyncio.CancelledError

            try:
                with pytest.raises(asyncio.CancelledError):
                    await app(scope, receive, send)
                await asyncio.wait_for(entered.wait(), 1)
                assert (await client.post("/admin/api/login", headers=headers)).status_code == 409
                assert calls == [1]
            finally:
                release.set()
                await app.state.console_state.login_task


async def test_ipv6_literal_origin_and_host(tmp_path):
    app = create_console_app(settings(host="::1"), StateStore(tmp_path, backend=MemoryKeyring()),
                             Credentials(), bootstrap_token=BOOTSTRAP)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("::1", 123)),
                                base_url="http://[::1]:19840") as client:
        response = await client.post("/admin/api/session", json={"token": BOOTSTRAP},
                                     headers={"Origin": "http://[::1]:19840"})
        assert response.status_code == 200
        assert (await client.get("/admin/api/status")).json()["base_url"] == "http://[::1]:19840/v1"
