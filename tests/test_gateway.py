"""Gateway contract tests; no official login or real-client compatibility claims."""

import asyncio
import importlib.util
import json
import threading
from types import SimpleNamespace

import httpx
import pytest


class Credentials:
    def __init__(self, token="upstream-secret", error=None):
        self.token = token
        self.error = error
        self.calls = 0

    async def get_access_token(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.token


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, error=None, gate=None):
        self.chunks = chunks
        self.error = error
        self.gate = gate
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            yield chunk
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def settings(**overrides):
    values = dict(upstream_base_url="https://api.openai.com/v1", max_body_bytes=1048576,
                  max_concurrency=8, timeout_seconds=120.0)
    return SimpleNamespace(**(values | overrides))


def factory():
    # A failing assertion, rather than collection-time ImportError, proves the missing API.
    assert importlib.util.find_spec("chatpass.gateway") is not None, "gateway API is missing"
    from chatpass.gateway import create_app
    return create_app


def make_app(handler, *, config=None, credentials=None, verifier=None):
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = factory()(config or settings(), credentials or Credentials(),
                    verifier or (lambda key: key == "gateway-secret"), client=upstream)
    return app, upstream


def downstream(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")


@pytest.mark.parametrize("path", ["/v1/models", "/v1/responses"])
async def test_credential_worker_capacity_survives_http_timeout(path):
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    class SlowCredentials:
        async def get_access_token(self):
            def work():
                calls.append(1)
                entered.set()
                gate.wait(timeout=5)
                return "upstream-secret"
            return await asyncio.to_thread(work)

    await asyncio.to_thread(lambda: None)  # Isolate credential lifetime from pool cold startup.
    app, upstream = make_app(lambda _: pytest.fail("must not reach upstream"),
                             config=settings(max_concurrency=1, timeout_seconds=0.1),
                             credentials=SlowCredentials())
    try:
        async with upstream, downstream(app) as client:
            request = client.get if path.endswith("models") else client.post
            kwargs = {"headers": AUTH}
            if path.endswith("responses"):
                kwargs["json"] = PAYLOAD
            first = await asyncio.wait_for(request(path, **kwargs), 1)
            assert entered.is_set() and first.status_code == 504
            second = await asyncio.wait_for(request(path, **kwargs), 1)
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "gateway_busy"
            assert calls == [1]
    finally:
        gate.set()
        for _ in range(50):
            if not getattr(app.state, "credential_tasks", set()):
                break
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("worker", ["credentials", "verifier"])
async def test_lifespan_waits_for_workers_after_http_timeout(worker):
    gate, entered = threading.Event(), threading.Event()

    def blocked(value):
        entered.set()
        gate.wait(timeout=5)
        return value

    class SlowCredentials:
        async def get_access_token(self):
            return await asyncio.to_thread(blocked, "upstream-secret")

    await asyncio.to_thread(lambda: None)
    app, upstream = make_app(lambda _: pytest.fail("must not reach upstream"),
                             config=settings(timeout_seconds=0.1),
                             credentials=SlowCredentials() if worker == "credentials" else Credentials(),
                             verifier=(lambda _: blocked(True)) if worker == "verifier" else None)
    lifecycle = app.router.lifespan_context(app)
    await lifecycle.__aenter__()
    closing = None
    try:
        async with downstream(app) as client:
            response = await client.get("/v1/models", headers=AUTH)
        assert entered.is_set() and response.status_code == (504 if worker == "credentials" else 401)
        closing = asyncio.create_task(lifecycle.__aexit__(None, None, None))
        await asyncio.sleep(0.02)
        assert not closing.done(), "ASGI shutdown must retain actual credential/keyring worker ownership"
    finally:
        gate.set()
        if closing is not None:
            await closing
        else:
            await lifecycle.__aexit__(None, None, None)
        await upstream.aclose()


AUTH = {"Authorization": "Bearer gateway-secret"}
PAYLOAD = {"model": "real-model", "input": "hello", "stream": True}
COMPLETED = b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


def success_stream():
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          stream=TrackedStream([COMPLETED]))


@pytest.mark.parametrize("first_outcome", ["running", "timeout", "cancel"])
async def test_verifier_workers_remain_bounded_until_thread_finishes(first_outcome):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []
    credentials = Credentials()
    upstream_calls = []

    def verifier(key):
        calls.append(key)
        loop.call_soon_threadsafe(entered.set)
        try:
            release.wait()
            return False
        finally:
            finished.set()

    app, upstream = make_app(lambda request: upstream_calls.append(request) or success_stream(),
                             config=settings(max_concurrency=1, timeout_seconds=0.05),
                             credentials=credentials, verifier=verifier)
    async with upstream, downstream(app) as client:
        first = asyncio.create_task(client.post("/v1/responses", json=PAYLOAD, headers=AUTH))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if first_outcome == "timeout":
                assert (await asyncio.wait_for(first, 1)).status_code == 401
            elif first_outcome == "cancel":
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            second = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "gateway_busy"
            assert calls == ["gateway-secret"]
            assert credentials.calls == 0
            assert upstream_calls == []
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 1)
        if first_outcome == "running":
            recovered = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
            assert recovered.status_code == 401
            assert calls == ["gateway-secret", "gateway-secret"]


@pytest.mark.parametrize("number", [b"1e308", b"-1e308"])
async def test_request_preserves_finite_float_values(number):
    captured = []
    app, upstream = make_app(lambda request: captured.append(json.loads(request.content))
                             or success_stream())
    raw = b'{"model":"m","input":"hi","reasoning":{"effort":' + number + b'}}'
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", content=raw,
                                     headers=AUTH | {"Content-Type": "application/json"})
    assert response.status_code == 200
    assert captured[0]["reasoning"]["effort"] == float(number)


@pytest.mark.parametrize("number", [b"1e999", b"-1e999"])
async def test_request_rejects_float_overflow_before_credentials_and_upstream(number):
    seen = []
    credentials = Credentials()
    app, upstream = make_app(lambda request: seen.append(request) or success_stream(),
                             credentials=credentials)
    raw = b'{"model":"m","input":"hi","reasoning":{"effort":' + number + b'}}'
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with upstream, httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.post("/v1/responses", content=raw,
                                     headers=AUTH | {"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"
    assert credentials.calls == 0
    assert seen == []


async def test_models_reject_float_overflow_in_upstream_json():
    stream = TrackedStream([b'{"models":[],"extra":1e999}'])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        response = await client.get("/v1/models", headers=AUTH)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"
    assert stream.closed


async def test_sse_rejects_float_overflow_without_forwarding_frame():
    frame = b'event: response.completed\ndata: {"type":"response.completed","extra":1e999}\n\n'
    stream = TrackedStream([frame])
    app, upstream = make_app(lambda request: httpx.Response(
        200, headers={"Content-Type": "text/event-stream"}, stream=stream))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    assert b"invalid_upstream_stream" in response.content
    assert b"response.completed" not in response.content
    assert b"1e999" not in response.content
    assert stream.closed


async def test_stream_preserves_full_tool_history_and_replaces_auth():
    seen = []
    def handler(request):
        seen.append(request)
        return success_stream()
    app, upstream = make_app(handler)
    payload = PAYLOAD | {"store": True, "tools": [{"type": "function", "name": "lookup"}],
                        "input": [{"role": "user", "content": "lookup"},
                                  {"type": "function_call", "call_id": "call1",
                                   "name": "lookup", "arguments": "{}"},
                                  {"type": "function_call_output", "call_id": "call1",
                                   "output": "result"}], "instructions": "follow tools"}
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=payload,
                                     headers=AUTH | {"X-Arbitrary": "not-forwarded",
                                                     "OpenAI-Organization": "not-forwarded"})
    assert response.status_code == 200
    assert response.content == COMPLETED
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert json.loads(seen[0].content) == payload | {"store": False}
    assert seen[0].headers["authorization"] == "Bearer upstream-secret"
    assert "x-arbitrary" not in seen[0].headers
    assert "openai-organization" not in seen[0].headers


async def test_stream_forwards_exact_sse_bytes_and_closes_response():
    chunks = [b'event: response.output_item.added\ndata: {"type":"response.output_item.added",'
              b'"item":{"type":"function_call"}}\n\n',
              b': ping\n\n', b'event: response.completed\ndata: {"id":"resp_1"}\n\n']
    stream = TrackedStream(chunks)
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"},
                              stream=stream)
    app, upstream = make_app(handler)
    payload = PAYLOAD | {"input": [{"role": "developer", "content": "use tools"}],
                        "tools": [{"type": "function", "name": "lookup"}]}
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.content == b"".join(chunks)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert captured == [payload | {"store": False}]
    assert stream.closed


@pytest.mark.parametrize("auth", [None, "Basic gateway-secret", "Bearer bad", "Bearer", "Bearer a b"])
async def test_invalid_auth_does_not_read_body_or_access_credentials(auth):
    calls = []
    creds = Credentials()
    app, upstream = make_app(lambda request: calls.append(request), credentials=creds)
    consumed = False
    async def body():
        nonlocal consumed
        consumed = True
        raise AssertionError("unauthenticated request body was read")
        yield b"never"
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", content=body(),
                                     headers={"Authorization": auth} if auth else {})
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert not consumed and not calls and creds.calls == 0


async def test_verifier_runs_off_event_loop_thread():
    loop_thread = threading.get_ident()
    threads = []
    def verify(key):
        threads.append(threading.get_ident())
        return key == "gateway-secret"
    app, upstream = make_app(lambda request: success_stream(),
                             verifier=verify)
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    assert threads and threads[0] != loop_thread


@pytest.mark.parametrize("body, content_type, status", [
    (b'{}', "text/plain", 415),
    (b'{}', "application/x-www-form-urlencoded", 415),
    (b'{', "application/json", 400),
    (b'[]', "application/json", 400),
    (b'{"model":"", "input":"hi"}', "application/json", 400),
    (b'{"model":"   ", "input":"hi"}', "application/json", 400),
    (b'{"model":42, "input":"hi"}', "application/json", 400),
    (b'{"model":"m"}', "application/json", 400),
    (b'{"model":"m", "input":"hi", "stream":1}', "application/json", 400),
    (b'{"model":"m", "input":"hi", "stream":"true"}', "application/json", 400),
    (b'{"model":"m", "previous_response_id":""}', "application/json", 400),
    (b'{"model":"m", "input":NaN}', "application/json", 400),
])
async def test_invalid_input_is_sanitized_without_upstream(body, content_type, status):
    calls = []
    app, upstream = make_app(lambda request: calls.append(request))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", content=body,
                                     headers=AUTH | {"Content-Type": content_type})
    assert response.status_code == status
    assert isinstance(response.json()["error"]["message"], str)
    assert "param" in response.json()["error"]
    assert not calls


async def test_body_limit_is_incremental_not_content_length():
    calls = []
    yielded = []
    async def body():
        for chunk in [b"x" * 16, b"x" * 16, b"must-not-read"]:
            yielded.append(chunk)
            yield chunk
    app, upstream = make_app(lambda request: calls.append(request),
                             config=settings(max_body_bytes=20))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", content=body(), headers=AUTH | {
            "Content-Type": "application/json", "Content-Length": "1"})
    assert response.status_code == 413
    assert len(yielded) == 2
    assert not calls


async def test_string_input_is_normalized_and_absent_stream_defaults_true():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return success_stream()
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", headers=AUTH,
                                     json={"model": "m", "input": "hello"})
    assert response.status_code == 200
    assert seen == [{"model": "m", "input": [{"role": "user", "content": "hello"}],
                     "store": False, "stream": True}]


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, 302])
async def test_upstream_errors_do_not_echo_tokens_or_follow_redirects(status):
    stream = TrackedStream([b'{"error":"upstream-secret gateway-secret"}'])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://evil.example/token",
                                               "retry-after": "7"}, stream=stream)
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == (status if status in (400, 401, 403, 429, 500, 503) else 502)
    assert "upstream-secret" not in response.text and "gateway-secret" not in response.text
    assert "error" in response.json()
    assert response.headers.get("retry-after") == ("7" if status == 429 else None)
    assert "location" not in response.headers
    assert len(calls) == 1 and stream.closed


@pytest.mark.parametrize("value", ["token-upstream-secret", "999999999999", "-1", "1\r\nx-token: secret",
                                    "Wed, 21 Oct 2015 07:28:00 GMT"])
async def test_retry_after_rejects_untrusted_values(value):
    app, upstream = make_app(lambda request: httpx.Response(429, headers={"retry-after": value}))
    async with upstream, downstream(app) as client:
        response = await client.get("/v1/models", headers=AUTH)
    assert response.status_code == 429
    assert "retry-after" not in response.headers


@pytest.mark.parametrize("error, expected", [
    (httpx.ReadTimeout("upstream-secret"), 504),
    (httpx.ConnectError("upstream-secret"), 502),
])
async def test_upstream_transport_failure_is_sanitized_no_retry(error, expected):
    calls = []
    def handler(request):
        calls.append(request)
        raise error
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == expected
    assert "upstream-secret" not in response.text
    assert len(calls) == 1


async def test_missing_credentials_is_explicit_sanitized_and_does_not_call_upstream():
    calls = []
    app, upstream = make_app(lambda request: calls.append(request),
                             credentials=Credentials(error=RuntimeError("upstream-secret")))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "credentials_unavailable"
    assert "upstream-secret" not in response.text
    assert not calls


async def test_models_are_real_upstream_ids_not_invented():
    model_data = {"models": [
        {"slug": "actual-model", "display_name": "Actual Model", "visibility": "list"},
        {"slug": "hidden-model", "display_name": "Hidden Model", "visibility": "hide"},
    ]}
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=model_data)
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        rejected = await client.get("/v1/models")
        response = await client.get("/v1/models", headers=AUTH)
    assert rejected.status_code == 401
    assert response.json() == {"object": "list", "data": [
        {"id": "actual-model", "object": "model", "owned_by": "openai", "name": "Actual Model"}
    ]}
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer upstream-secret"


@pytest.mark.parametrize("value", [{}, [], {"models": {}}, {"models": ["m"]},
    {"models": [{}]}, {"models": [{"visibility": "list", "slug": "", "display_name": "M"}]},
    {"models": [{"visibility": "list", "slug": 123, "display_name": "M"}]},
    {"models": [{"visibility": "list", "slug": "M", "display_name": " "}]},
    {"models": [{"visibility": "list", "slug": "M", "display_name": "x" * 201}]},
    {"models": [{"visibility": "list", "slug": "x" * 201, "display_name": "M"}]},
])
async def test_malformed_model_shape_is_sanitized(value):
    app, upstream = make_app(lambda request: httpx.Response(200, json=value))
    async with upstream, downstream(app) as client:
        response = await client.get("/v1/models", headers=AUTH)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


@pytest.mark.parametrize("path", ["/v1/models"])
async def test_upstream_success_body_limit_and_closure(path):
    stream = TrackedStream([b"x" * 64, b"x" * 64])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream),
                             config=settings(max_body_bytes=80))
    async with upstream, downstream(app) as client:
        response = (await client.get(path, headers=AUTH) if path.endswith("models") else
                    await client.post(path, json=PAYLOAD, headers=AUTH))
    assert response.status_code == 502
    assert stream.closed


async def test_requested_stream_requires_event_stream_and_closes_bad_response():
    stream = TrackedStream([b"upstream-secret"])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "application/json"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD | {"stream": True}, headers=AUTH)
    assert response.status_code == 502
    assert "upstream-secret" not in response.text
    assert stream.closed


async def test_midstream_timeout_emits_sanitized_error_not_completion_and_closes():
    stream = TrackedStream([b'data: {"delta":"hi"}\n\n'],
                           error=httpx.ReadTimeout("upstream-secret"))
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD | {"stream": True}, headers=AUTH)
    assert response.status_code == 200
    assert response.content.startswith(b'data: {"delta":"hi"}\n\n')
    assert b"event: error" in response.content
    assert b"upstream-secret" not in response.content
    assert b"response.completed" not in response.content
    assert stream.closed


async def test_concurrency_rejects_immediately_and_releases_nonstream_slot():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"models": []})
    app, upstream = make_app(handler, config=settings(max_concurrency=1))
    async with upstream, downstream(app) as client:
        first = asyncio.create_task(client.get("/v1/models", headers=AUTH))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            second = await asyncio.wait_for(client.get("/v1/models", headers=AUTH), 1)
            assert second.status_code == 429
            assert len(calls) == 1
        finally:
            release.set()
            await first
        third = await client.get("/v1/models", headers=AUTH)
    assert third.status_code == 200


async def start_asgi_stream(app, *, fail_send=False):
    """Exercise live ASGI streaming: HTTPX ASGITransport intentionally buffers."""
    messages = []
    sent_body, disconnect = asyncio.Event(), asyncio.Event()
    body = json.dumps(PAYLOAD | {"stream": True}).encode()
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            sent_body.set()
            if fail_send:
                raise OSError("downstream disconnected")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/responses",
             "raw_path": b"/v1/responses", "query_string": b"", "root_path": "",
             "headers": [(b"authorization", b"Bearer gateway-secret"),
                         (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("gateway", 80)}
    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(sent_body.wait(), 1)
    return task, disconnect, messages


@pytest.mark.parametrize("closure", ["disconnect", "cancel", "send_error"])
async def test_stream_holds_slot_until_closure_and_always_closes_upstream(closure):
    gate = asyncio.Event()
    stream = TrackedStream([b'data: {"delta":"first"}\n\n'], gate=gate)
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    app, upstream = make_app(handler, config=settings(max_concurrency=1))
    async with upstream, downstream(app) as client:
        task, disconnect, messages = await start_asgi_stream(app, fail_send=closure == "send_error")
        try:
            if closure != "send_error":
                rejected = await client.get("/v1/models", headers=AUTH)
                assert rejected.status_code == 429 and not stream.closed
            if closure == "disconnect":
                disconnect.set()
            elif closure == "cancel":
                task.cancel()
            if closure == "send_error":
                with pytest.raises((OSError, ExceptionGroup)):
                    await asyncio.wait_for(task, 1)
            elif closure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            else:
                await asyncio.wait_for(task, 1)
            assert stream.closed
            assert (await client.get("/v1/models", headers=AUTH)).status_code == 200
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_health_has_no_secrets_or_public_management_docs():
    creds = Credentials(error=AssertionError("health must not touch credentials"))
    app, upstream = make_app(lambda request: None, credentials=creds)
    async with upstream, downstream(app) as client:
        response = await client.get("/health")
        assert response.json() == {"status": "ok"}
        assert "access-control-allow-origin" not in response.headers
        for path in ["/docs", "/openapi.json", "/credentials", "/keys"]:
            assert (await client.get(path)).status_code == 404
    assert creds.calls == 0


async def test_injected_client_lifespan_does_not_close_it():
    app, upstream = make_app(lambda request: httpx.Response(200, json={"models": []}))
    async with app.router.lifespan_context(app):
        assert not upstream.is_closed
    assert not upstream.is_closed
    await upstream.aclose()


async def test_owned_client_has_secure_bounded_settings_and_is_closed(monkeypatch):
    real_client = httpx.AsyncClient
    made = []
    def spy(**kwargs):
        made.append((kwargs, real_client(**kwargs)))
        return made[-1][1]
    monkeypatch.setattr(httpx, "AsyncClient", spy)
    app = factory()(settings(timeout_seconds=3), Credentials(), lambda key: True)
    async with app.router.lifespan_context(app):
        assert len(made) == 1
        kwargs, client = made[0]
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert client.timeout.read == 3 and client.timeout.connect == 3
        assert not client.is_closed
    assert client.is_closed


async def test_disconnect_before_headers_cancels_upstream_and_releases_slot():
    entered, canceled = asyncio.Event(), asyncio.Event()
    disconnect = asyncio.Event()
    async def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json={"models": []})
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    app, upstream = make_app(handler, config=settings(max_concurrency=1))
    body = json.dumps(PAYLOAD).encode()
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        pass
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/responses",
             "raw_path": b"/v1/responses", "query_string": b"", "root_path": "",
             "headers": [(b"authorization", b"Bearer gateway-secret"),
                         (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("gateway", 80)}
    async with upstream, downstream(app) as client:
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            disconnect.set()
            await asyncio.wait_for(canceled.wait(), 1)
            await asyncio.wait_for(task, 1)
            assert (await client.get("/v1/models", headers=AUTH)).status_code == 200
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_injected_client_defaults_cookies_and_auth_are_not_forwarded():
    seen = []
    def handler(request):
        seen.append(request)
        return success_stream()
    app, upstream = make_app(handler)
    upstream.headers["X-Injected"] = "secret"
    upstream.cookies.set("secret", "not-forwarded")
    upstream.auth = ("not", "forwarded")
    upstream.follow_redirects = True
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    assert "cookie" not in seen[0].headers and "x-injected" not in seen[0].headers
    assert seen[0].headers["authorization"] == "Bearer upstream-secret"
    assert seen[0].extensions["timeout"]["read"] == 120


@pytest.mark.parametrize("path", ["/v1/models", "/v1/responses"])
async def test_unexpected_compressed_success_is_rejected_without_decoding(path):
    stream = TrackedStream([b"not-really-gzip upstream-secret"])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream, headers={
        "content-encoding": "gzip", "content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = (await client.get(path, headers=AUTH) if path.endswith("models") else
                    await client.post(path, json=PAYLOAD, headers=AUTH))
    assert response.status_code == 502
    assert stream.closed
    assert not stream.started.is_set()


@pytest.mark.parametrize("data", [
    b'event: response.completed\ndata: {"type":"response.created"}\n\n',
    b'data: {"error":{"message":"upstream-secret"}}\n\n',
    b'data: {"type":"response.completed","response":{"error":"upstream-secret"}}\n\n',
    b'data: {"type":"response.completed","response":{"status":"failed"}}\n\n',
    b'data: {"type":"response.completed","response":{"status":"incomplete"}}\n\n',
])
async def test_conflicting_terminal_or_untyped_errors_do_not_echo_or_claim_completion(data):
    stream = TrackedStream([data])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert b"upstream-secret" not in response.content
    assert b"response.completed" not in response.content
    assert b"event: error" in response.content
    assert stream.closed


UNSUPPORTED_FIELDS = ["previous_response_id", "background", "conversation", "max_output_tokens",
                      "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt",
                      "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs",
                      "top_p", "truncation", "user"]


@pytest.mark.parametrize("field", UNSUPPORTED_FIELDS)
async def test_preview_unsupported_fields_are_explicitly_rejected_in_strict_mode(field):
    calls = []
    app, upstream = make_app(lambda request: calls.append(request),
                             config=settings(compatibility_mode="strict"))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD | {field: "value"}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_capability"
    assert response.json()["error"]["param"] == field
    assert not calls


@pytest.mark.parametrize("override", [
    {"tools": [{"type": "web_search_preview"}]}, {"tools": [{"type": "file_search"}]},
    {"tools": [{"type": "code_interpreter"}]}, {"tools": [{"type": "unknown"}]},
])
async def test_unsupported_hosted_tools_are_rejected(override):
    calls = []
    app, upstream = make_app(lambda request: calls.append(request))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD | override, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_capability"
    assert not calls


async def test_custom_tools_are_preserved():
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return success_stream()
    app, upstream = make_app(handler)
    tool = {"type": "custom", "name": "execute", "format": {"type": "text"}}
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD | {"tools": [tool]}, headers=AUTH)
    assert response.status_code == 200
    assert captured[0]["tools"] == [tool]


SSE_LINE_ENDING_PAIRS = [
    (first, second) for first in (b"\n", b"\r", b"\r\n")
    for second in (b"\n", b"\r", b"\r\n") if (first, second) != (b"\r", b"\n")
]


@pytest.mark.parametrize("endings", SSE_LINE_ENDING_PAIRS)
async def test_sse_legal_mixed_endings_are_exact_at_every_chunk_boundary(endings):
    first, second = endings
    frame = (b'event: response.completed' + first
             + b'data: {"type":"response.completed"}' + first + second)
    variants = [[frame], [bytes([byte]) for byte in frame]]
    variants.extend([frame[:cut], frame[cut:]] for cut in range(1, len(frame)))
    for chunks in variants:
        stream = TrackedStream(chunks)
        app, upstream = make_app(lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "text/event-stream"}))
        async with upstream, downstream(app) as client:
            response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
        assert response.content == frame, (endings, chunks)
        assert stream.closed


async def test_sse_trailing_cr_waits_to_preserve_optional_lf_byte():
    from chatpass.gateway import _SSEFrames
    frames = _SSEFrames()
    prefix = b'data: {"type":"response.completed"}\r\r'
    assert [frame async for frame in frames.feed(prefix)] == []
    assert [frame async for frame in frames.feed(b"\n")] == [prefix + b"\n"]


@pytest.mark.parametrize("endings", SSE_LINE_ENDING_PAIRS)
async def test_sse_mixed_endings_interrupted_eof_still_reports_error(endings):
    frame = b'data: {"delta":"hi"}' + b"".join(endings)
    stream = TrackedStream([frame])
    app, upstream = make_app(lambda request: httpx.Response(
        200, stream=stream, headers={"Content-Type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.content.startswith(frame)
    assert b'"code": "stream_interrupted"' in response.content
    assert b"response.completed" not in response.content
    assert stream.closed


@pytest.mark.parametrize("ending", [b"", b"\r", b"\n", b"\r\n"])
async def test_sse_one_logical_newline_is_not_a_blank_line_at_eof(ending):
    frame = b'data: {"type":"response.completed"}' + ending
    stream = TrackedStream([bytes([byte]) for byte in frame])
    app, upstream = make_app(lambda request: httpx.Response(
        200, stream=stream, headers={"Content-Type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert b"response.completed" not in response.content
    assert b'"code": "stream_interrupted"' in response.content
    assert stream.closed


@pytest.mark.parametrize("chunks", [
    [b'data: {"delta":"hi"}\n\n'],
    [b'data: not-json\n\n'],
    [b'data: {"type":"response.completed"}'],
    [b'data: []\n\n'],
])
async def test_incomplete_or_malformed_sse_is_not_reported_as_completion(chunks):
    stream = TrackedStream(chunks)
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    assert b"event: error" in response.content
    assert b"response.completed" not in response.content
    assert stream.closed


async def test_sse_crlf_and_split_frames_preserve_original_bytes():
    data = b': heartbeat\r\n\r\nevent: response.completed\r\ndata: {"type":"response.completed"}\r\n\r\n'
    stream = TrackedStream([data[:13], data[13:24], data[24:-1], data[-1:]])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.content == data
    assert stream.closed


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error"])
async def test_upstream_terminal_errors_are_sanitized(terminal):
    frame = f'event: {terminal}\ndata: {{"type":"{terminal}","error":"upstream-secret"}}\n\n'.encode()
    stream = TrackedStream([frame])
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert b"upstream-secret" not in response.content
    assert b"event: error" in response.content
    assert b"response.completed" not in response.content
    assert stream.closed


@pytest.mark.parametrize("limit", ["frame", "total"])
async def test_stream_bytes_are_bounded_and_upstream_closed(limit, monkeypatch):
    # Limits are observable at the wire; small ceilings avoid huge test allocations.
    import chatpass.gateway as gateway
    monkeypatch.setattr(gateway, "MAX_SSE_FRAME_BYTES", 32 if limit == "frame" else 1024)
    monkeypatch.setattr(gateway, "MAX_STREAM_BYTES", 64 if limit == "total" else 1024)
    chunks = ([b"data: " + b"x" * 40] if limit == "frame" else [b": ping\n\n"] * 20)
    stream = TrackedStream(chunks)
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert b"event: error" in response.content
    assert stream.closed


async def test_absolute_stream_timeout_closes_slow_stream():
    stream = TrackedStream([b": ping\n\n"], gate=asyncio.Event())
    # The stream deadline starts after authentication and includes opening headers.
    # A 30ms fixture can correctly time out before SSE starts on loaded Windows.
    # Leave header/startup headroom; the unreleased stream gate still forces timeout.
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}),
                             config=settings(timeout_seconds=0.5))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 2)
    assert response.status_code == 200
    assert b"event: error" in response.content and b"response.completed" not in response.content
    assert stream.closed


@pytest.mark.parametrize("overrides", [{"upstream_base_url": "https://evil.example/v1"},
                                         {"upstream_base_url": "https://api.openai.com.evil/v1"},
                                         {"upstream_base_url": "http://api.openai.com/v1"},
                                         {"max_body_bytes": 0}, {"max_concurrency": 0},
                                         {"timeout_seconds": float("inf")},
                                         {"timeout_seconds": 0}])
def test_unsafe_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        factory()(settings(**overrides), Credentials(), lambda key: True)


@pytest.mark.parametrize("header,value,label", [
    ("content-encoding", "gzip", "content encoding (gzip)"),
    ("content-encoding", "br", "content encoding (br)"),
    ("content-encoding", "upstream-secret", "content encoding (other)"),
    ("content-type", "application/json", "content type (application/json)"),
    ("content-type", "text/html; charset=utf-8", "content type (text/html)"),
    ("content-type", "text/upstream-secret", "content type (other)"),
    ("content-type", "", "content type (missing)"),
])
async def test_invalid_upstream_headers_have_safe_specific_diagnostics(header, value, label):
    stream = TrackedStream([b"upstream-secret"])
    headers = {"content-type": "text/event-stream"}
    headers[header] = value
    app, upstream = make_app(lambda request: httpx.Response(200, stream=stream, headers=headers))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"
    assert label in response.json()["error"]["message"]
    assert "upstream-secret" not in response.text
    assert stream.closed and not stream.started.is_set()


@pytest.mark.parametrize("case,category,last_event,upstream_code", [
    ("eof", "eof_without_completion", "response.output_text.delta", "none"),
    ("failed", "upstream_stream_failed", "response.failed", "server_error"),
    ("incomplete", "upstream_stream_failed", "response.incomplete", "max_output_tokens"),
    ("unknown", "upstream_stream_failed", "other", "other"),
    ("malformed", "invalid_upstream_stream", "none", "none"),
    ("transport", "transport_error", "none", "none"),
    ("timeout", "upstream_timeout", "none", "none"),
])
async def test_safe_stream_failure_diagnostics(caplog, case, category, last_event, upstream_code):
    secret = "PRIVATE_BODY_TOKEN_TOOL_ARGUMENTS"
    events = {
        "eof": {"type": "response.output_text.delta", "delta": secret},
        "failed": {"type": "response.failed", "response": {
            "id": secret, "error": {"code": "server_error", "message": secret}}},
        "incomplete": {"type": "response.incomplete", "response": {
            "incomplete_details": {"reason": "max_output_tokens"}, "output": [secret]}},
        "unknown": {"type": secret, "error": {"code": secret, "message": secret}},
    }
    error = {"transport": httpx.ReadError(secret), "timeout": httpx.ReadTimeout(secret)}.get(case)
    if case in events:
        chunks = [b"data: " + json.dumps(events[case]).encode() + b"\n\n"]
    elif case == "malformed":
        chunks = [b"data: " + secret.encode() + b"\n\n"]
    else:
        chunks = [b": " + secret.encode() + b"\n\n"]
    stream = TrackedStream(chunks, error=error)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
        return success_stream()

    app, upstream = make_app(handler, config=settings(max_concurrency=1))
    caplog.set_level("WARNING", logger="chatpass.gateway")
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH | {
            "X-ChatPass-Request-ID": secret})
        retry = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    request_id = response.headers["X-ChatPass-Request-ID"]
    assert len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id)
    assert retry.headers["X-ChatPass-Request-ID"] != request_id
    assert retry.content == COMPLETED and len(calls) == 2 and stream.closed
    records = [r for r in caplog.records if r.name == "chatpass.gateway"]
    assert len(records) == 1
    record = records[0]
    assert record.exc_info is None
    message = record.getMessage()
    assert f"request_id={request_id}" in message
    assert f"category={category}" in message
    assert f"last_event={last_event}" in message
    assert f"upstream_code={upstream_code}" in message
    assert "elapsed_ms=" in message and "frames=" in message and "bytes=" in message
    assert secret not in caplog.text
    assert "upstream-secret" not in caplog.text and "gateway-secret" not in caplog.text
    assert b"event: error" in response.content and b"response.completed" not in response.content


@pytest.mark.parametrize("opt_in", [None, "0", "true", "1"])
@pytest.mark.parametrize("shape", ["error", "response", "flat"])
async def test_unknown_code_diagnostic_is_explicit_and_one_shot(monkeypatch, caplog, opt_in, shape):
    if opt_in is None:
        monkeypatch.delenv("CHATPASS_DIAG_UNKNOWN_CODE_ONCE", raising=False)
    else:
        monkeypatch.setenv("CHATPASS_DIAG_UNKNOWN_CODE_ONCE", opt_in)
    caplog.set_level("WARNING", logger="chatpass.gateway")
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return success_stream()
        code = "server_error" if count == 2 else "upstream_transient_test_failure"
        detail = {"code": code, "message": "PRIVATE_MESSAGE", "param": "PRIVATE_TOOL_PARAMETER"}
        if shape == "response":
            payload = {"type": "response.failed", "response": {"error": detail}}
        elif shape == "flat":
            payload = {"type": "error", **detail}
        else:
            payload = {"type": "error", "error": detail}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=TrackedStream([b"data: " + json.dumps(payload).encode() + b"\n\n"]))

    app, upstream = make_app(handler)
    # Environment changes after creation must not silently arm/rearm the app.
    monkeypatch.setenv("CHATPASS_DIAG_UNKNOWN_CODE_ONCE", "1")
    async with upstream, downstream(app) as client:
        assert (await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)).content == COMPLETED
        await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
        assert "one-shot code diagnostic" not in caplog.text
        responses = await asyncio.gather(*[
            client.post("/v1/responses", json=PAYLOAD, headers=AUTH) for _ in range(2)])
    captures = [r.getMessage() for r in caplog.records if "one-shot code diagnostic" in r.getMessage()]
    assert len(captures) == (1 if opt_in == "1" else 0)
    if captures:
        assert "upstream_code=upstream_transient_test_failure capture=consumed" in captures[0]
        assert any(r.headers["X-ChatPass-Request-ID"] in captures[0] for r in responses)
    else:
        assert "upstream_transient_test_failure" not in caplog.text
    for response in responses:
        assert b'"code": "upstream_stream_failed"' in response.content
        assert b"upstream_transient_test_failure" not in response.content
    assert "PRIVATE_" not in caplog.text and "gateway-secret" not in caplog.text
    assert "upstream-secret" not in caplog.text


@pytest.mark.parametrize("code", [
    "secret\nforged_log", "secret\rforged_log", "secret value", "https://private.example/token",
    "x" * 81, {"token": "PRIVATE_TOKEN"}, ["PRIVATE_TOKEN"], 123,
])
async def test_one_shot_code_diagnostic_rejects_unsafe_values(monkeypatch, caplog, code):
    monkeypatch.setenv("CHATPASS_DIAG_UNKNOWN_CODE_ONCE", "1")
    caplog.set_level("WARNING", logger="chatpass.gateway")
    payload = {"type": "error", "code": code, "message": "PRIVATE_MESSAGE"}
    app, upstream = make_app(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        stream=TrackedStream([b"data: " + json.dumps(payload).encode() + b"\n\n"])))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    captures = [r for r in caplog.records if "one-shot code diagnostic" in r.getMessage()]
    assert len(captures) == 1 and captures[0].exc_info is None
    assert "upstream_code=invalid_or_oversized_code capture=consumed" in captures[0].getMessage()
    assert "PRIVATE_" not in caplog.text and "secret" not in caplog.text
    assert b"event: error" in response.content


def test_one_shot_ignores_non_error_metadata_and_incomplete_reason():
    from chatpass.gateway import _event_kind, _StreamDiagnostics
    captured = []
    diagnostics = _StreamDiagnostics(lambda request_id, code: captured.append(code))
    for payload in [
        {"type": "response.created", "code": "private_metadata"},
        {"type": "response.incomplete", "response": {
            "incomplete_details": {"reason": "private_reason"}}},
        {"type": "error"},
    ]:
        _event_kind(b"data: " + json.dumps(payload).encode() + b"\n\n", diagnostics=diagnostics)
        diagnostics.failure("upstream_stream_failed")
    assert captured == []
