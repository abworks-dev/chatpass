"""HTTP and streaming public error semantics with synthetic upstreams only."""
import json

import httpx
import pytest
from test_gateway import Credentials, TrackedStream, downstream, make_app, settings

from chatpass.oauth import OAuthError
from chatpass.upstream_errors import SIWC_STATUS

IDENTIFIER = "req_" + "a" * 24
HEADERS = {"Authorization": "Bearer gateway-secret"}


async def call(client, path, *, stream=True):
    if path == "/v1/models":
        return await client.get(path, headers=HEADERS)
    body = {"model": "actual-model", "stream": stream}
    body.update({"messages": [{"role": "user", "content": "hello"}]} if "chat" in path else {"input": "hello"})
    return await client.post(path, headers=HEADERS, json=body)


@pytest.mark.parametrize("path", ["/v1/models", "/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("code", list(SIWC_STATUS))
async def test_http_status_safe_metadata_no_retry(path, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(SIWC_STATUS[code], headers={"x-request-id": IDENTIFIER},
            json={"error": {"code": code, "param": "model", "message": "Bearer upstream-secret",
                            "detail": "cp_secret", "type": "secret"}})
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        response = await call(client, path)
    assert response.status_code == SIWC_STATUS[code]
    error = response.json()["error"]
    assert error["code"] == code and error["param"] == "model"
    assert error["request_id"] == IDENTIFIER
    assert "secret" not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("code", ["subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"])
async def test_failure_frame_survives_bridges_without_false_success(path, streaming, code):
    raw = b"event: response.failed\ndata: " + json.dumps({"type": "response.failed", "response": {
        "error": {"code": code, "param": "model", "message": "upstream-secret"}}}).encode() + b"\n\n"
    tracked = TrackedStream([raw])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream", "x-request-id": IDENTIFIER}, stream=tracked)
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        response = await call(client, path, stream=streaming)
    assert response.status_code == (200 if streaming else SIWC_STATUS[code])
    assert code in response.text and IDENTIFIER in response.text
    payload = response.text.split("data: ", 1)[1].split("\n", 1)[0] if streaming else response.text
    assert json.loads(payload)["error"]["param"] == "model"
    assert "upstream-secret" not in response.text
    assert "[DONE]" not in response.text and "response.completed" not in response.text
    assert tracked.closed and len(calls) == 1


@pytest.mark.parametrize("path", ["/v1/models", "/v1/responses"])
@pytest.mark.parametrize("code", ["login_required", "pending_token_validation", "refresh_not_yet_allowed",
                                   "identity_verification_unavailable", "scope_not_authorized"])
async def test_credential_recovery_code_not_generic_or_retried(path, code):
    app, upstream = make_app(lambda _: pytest.fail("no upstream"), credentials=Credentials(error=OAuthError(code)))
    async with upstream, downstream(app) as client:
        response = await call(client, path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("path", ["/v1/models", "/v1/responses"])
@pytest.mark.parametrize("status", [429, 503])
async def test_known_http_error_survives_absolute_deadline(path, status):
    import asyncio

    tracked = TrackedStream([], gate=asyncio.Event())
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.03)  # Headers arrive with less than optional .25s left.
        return httpx.Response(status, headers={"content-type": "application/json",
            "retry-after": "7", "x-request-id": IDENTIFIER}, stream=tracked)
    app, upstream = make_app(handler, config=settings(timeout_seconds=0.1))
    async with upstream, downstream(app) as client:
        response = await call(client, path)
    assert response.status_code == status
    assert response.json()["error"]["request_id"] == IDENTIFIER
    assert response.headers.get("retry-after") == ("7" if status == 429 else None)
    assert tracked.closed and len(calls) == 1


@pytest.mark.parametrize("status", [429, 503])
async def test_management_models_preserves_known_status_at_deadline(tmp_path, status):
    import asyncio

    from test_console import BOOTSTRAP, ORIGIN, MemoryKeyring, authorize
    from test_console import settings as console_settings

    from chatpass.console import create_console_app
    from chatpass.storage import StateStore

    tracked = TrackedStream([], gate=asyncio.Event())
    async def handler(request):
        await asyncio.sleep(0.03)
        return httpx.Response(status, headers={"content-type": "application/json", "retry-after": "7"}, stream=tracked)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_console_app(console_settings(timeout_seconds=0.1),
            StateStore(tmp_path, backend=MemoryKeyring()), Credentials(),
            client=upstream, bootstrap_token=BOOTSTRAP)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
                await authorize(client)
                response = await client.get("/admin/api/models")
                assert response.status_code == status
                assert response.headers.get("retry-after") == ("7" if status == 429 else None)
    assert tracked.closed


@pytest.mark.parametrize("case", ["oversized", "malformed", "compressed", "slow"])
async def test_bad_optional_error_metadata_preserves503(case):
    import asyncio

    raw = b'{"error":{"code":"subscription_sharing_usage_unavailable","message":"secret"}}'
    if case == "oversized":
        raw = b"x" * 16385
    elif case == "malformed":
        raw = b'{"error":'
    tracked = TrackedStream([raw], gate=asyncio.Event() if case == "slow" else None)
    headers = {"content-type": "application/json"}
    if case == "compressed":
        headers["content-encoding"] = "gzip"
    app, upstream = make_app(lambda _: httpx.Response(503, headers=headers, stream=tracked),
                            config=settings(timeout_seconds=2))
    async with upstream, downstream(app) as client:
        response = await call(client, "/v1/responses")
    assert response.status_code == 503 and response.json()["error"]["code"] == "upstream_error"
    assert "secret" not in response.text and tracked.closed
    if case == "compressed":
        assert not tracked.started.is_set()
