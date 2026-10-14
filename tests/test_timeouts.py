"""Sub2API-inspired timeout phase separation with synthetic upstreams only."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from test_gateway import AUTH, PAYLOAD, TrackedStream, downstream, make_app, settings
from test_siwc_errors import call

from chatpass.cli import main
from chatpass.config import Settings

COMPLETED = b'data: {"type":"response.completed","response":{"id":"resp_test","status":"completed","output":[]}}\n\n'
HEADERLESS_COMPLETED = b'data: {"type":"response.completed","response":{"id":"resp_test","object":"response","status":"completed","output":[]}}\n\n'


def phase_settings(**overrides):
    return settings(**({
        "timeout_seconds": 1.0,
        "connect_timeout_seconds": 0.2,
        "response_header_timeout_seconds": 0.8,
        "stream_idle_timeout_seconds": 0.5,
        "stream_total_timeout_seconds": 2.0,
    } | overrides))


class TimedStream(TrackedStream):
    def __init__(self, chunks=(), *, delay=0.04, stall=False):
        super().__init__(chunks)
        self.delay = delay
        self.stall = stall
        self.reads = 0

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            self.reads += 1
            yield chunk
        if self.stall:
            await asyncio.Event().wait()


def errors(response, streaming):
    if not streaming:
        return [response.json()["error"]]
    return [json.loads(line[6:])["error"] for line in response.text.splitlines()
            if line.startswith("data: ") and '"error"' in line]


def test_explicit_default_budgets():
    config = Settings()
    assert config.timeout_seconds == 120
    assert config.connect_timeout_seconds == 10
    assert config.response_header_timeout_seconds == 600
    assert config.stream_idle_timeout_seconds == 180
    assert config.stream_total_timeout_seconds == 1800
    assert Settings(stream_total_timeout_seconds=0).stream_idle_timeout_seconds == 180


@pytest.mark.parametrize("field", [
    "connect_timeout_seconds", "response_header_timeout_seconds",
    "stream_idle_timeout_seconds", "stream_total_timeout_seconds",
])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "180", 86401])
def test_invalid_phase_configuration(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})
    with pytest.raises(ValueError):
        make_app(lambda _: None, config=phase_settings(**{field: value}))


@pytest.mark.parametrize("field", [
    "connect_timeout_seconds", "response_header_timeout_seconds", "stream_idle_timeout_seconds",
])
def test_only_total_may_be_disabled(field):
    with pytest.raises(ValueError):
        Settings(**{field: 0})
    with pytest.raises(ValueError):
        make_app(lambda _: None, config=phase_settings(**{field: 0}))


@pytest.mark.parametrize("command", ["serve", "web"])
def test_cli_wires_timeout_flags_without_starting_service(tmp_path, monkeypatch, command):
    import sys

    from test_storage import MemoryKeyring

    from chatpass.storage import StateStore

    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    seen = []

    def build(config, *args):
        seen.append(config)
        return SimpleNamespace(state=SimpleNamespace(console_bootstrap_token="test-bootstrap"))

    class Server:
        def __init__(self, config):
            pass

        def run(self):
            pass

    monkeypatch.setattr("chatpass.gateway.create_app", build)
    monkeypatch.setitem(sys.modules, "chatpass.console", SimpleNamespace(create_console_app=build))
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.Server", Server)
    options = ["--no-browser"] if command == "web" else []
    assert main(["--data-dir", str(tmp_path), command, *options,
                 "--preparation-timeout", "60", "--connect-timeout", "15",
                 "--response-header-timeout", "300", "--stream-idle-timeout", "240",
                 "--stream-total-timeout", "0"]) == 0
    assert len(seen) == 1
    config = seen[0]
    assert (config.timeout_seconds, config.connect_timeout_seconds,
            config.response_header_timeout_seconds, config.stream_idle_timeout_seconds,
            config.stream_total_timeout_seconds) == (60, 15, 300, 240, 0)


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("streaming", [True, False])
async def test_active_stream_outlives_preparation_budget(path, streaming):
    stream = TimedStream([b": ping\n\n"] * 6 + [COMPLETED])
    app, upstream = make_app(lambda _: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=stream),
        config=phase_settings(timeout_seconds=0.08))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(call(client, path, stream=streaming), 3)
    assert response.status_code == 200 and "upstream_timeout" not in response.text
    assert stream.reads == 7 and stream.closed
    if streaming:
        assert ("[DONE]" if "chat" in path else "response.completed") in response.text


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("streaming", [True, False])
async def test_idle_timeout_reports_phase_and_releases_slot(path, streaming, caplog):
    stream = TimedStream([b": ping\n\n"], stall=True)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=stream if len(calls) == 1 else TrackedStream([COMPLETED]))

    caplog.set_level("WARNING", logger="chatpass.gateway")
    app, upstream = make_app(handler, config=phase_settings(
        max_concurrency=1, stream_idle_timeout_seconds=0.1))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(call(client, path, stream=streaming), 3)
        assert len(calls) == 1 and stream.closed
        retry = await call(client, path, stream=streaming)
    assert response.status_code == (200 if streaming else 504)
    error = errors(response, streaming)[0]
    assert error["code"] == "upstream_timeout" and "未收到数据" in error["message"]
    assert error["timeout_phase"] == "stream_idle"
    assert "[DONE]" not in response.text
    assert "timeout_phase=stream_idle" in caplog.text
    assert response.headers["X-ChatPass-Request-ID"] in caplog.text
    assert "response.completed" not in response.text
    assert retry.status_code == 200 and len(calls) == 2


@pytest.mark.parametrize("headerless", [True, False])
async def test_fragmented_bytes_reset_idle_without_complete_frames(headerless):
    completed = HEADERLESS_COMPLETED if headerless else COMPLETED
    chunks = [completed[i:i + 12] for i in range(0, len(completed), 12)]
    stream = TimedStream(chunks, delay=0.025)
    app, upstream = make_app(lambda _: httpx.Response(
        200, headers={} if headerless else {"content-type": "text/event-stream"}, stream=stream),
        config=phase_settings(timeout_seconds=0.08, stream_idle_timeout_seconds=0.15))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert response.status_code == 200 and response.content == completed
    assert stream.closed


@pytest.mark.parametrize("headerless", [True, False])
async def test_heartbeat_cannot_escape_total_timeout(headerless, caplog):
    stream = TimedStream([b": ping\n\n"] * 30 + [COMPLETED], delay=0.025)
    caplog.set_level("WARNING", logger="chatpass.gateway")
    app, upstream = make_app(lambda _: httpx.Response(
        200, headers={} if headerless else {"content-type": "text/event-stream"}, stream=stream),
        config=phase_settings(stream_total_timeout_seconds=0.2))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert response.status_code == (504 if headerless else 200)
    error = errors(response, not headerless)[0]
    assert error["code"] == "upstream_timeout" and "总时限" in error["message"]
    assert "timeout_phase=stream_total" in caplog.text and stream.closed
    assert 0 < stream.reads < 31 and "response.completed" not in response.text


async def test_disabled_total_still_enforces_idle():
    stream = TimedStream([b": ping\n\n"] * 4, stall=True)
    app, upstream = make_app(lambda _: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=stream),
        config=phase_settings(timeout_seconds=0.08, stream_idle_timeout_seconds=0.1,
                              stream_total_timeout_seconds=0))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert "stream_idle" in response.text and "stream_total" not in response.text
    assert stream.reads == 4 and stream.closed


async def test_header_budget_separate_from_preparation_and_transport_limits():
    seen = []

    async def handler(request):
        seen.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=TrackedStream([COMPLETED]))

    app, upstream = make_app(handler, config=phase_settings(timeout_seconds=0.08))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert response.status_code == 200 and response.content == COMPLETED
    assert seen[0].extensions["timeout"] == {
        "connect": 0.2, "pool": 0.2, "write": 0.2, "read": 0.8}


@pytest.mark.parametrize("phase,error", [
    ("connect", httpx.ConnectTimeout), ("pool", httpx.PoolTimeout),
    ("write", httpx.WriteTimeout), ("response_headers", httpx.ReadTimeout),
])
async def test_http_timeout_phase_is_safe_and_never_replayed(phase, error, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("PRIVATE_EXCEPTION_TOKEN")

    caplog.set_level("WARNING", logger="chatpass.gateway")
    app, upstream = make_app(handler, config=phase_settings())
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 504 and response.json()["error"]["timeout_phase"] == phase
    assert len(calls) == 1 and f"timeout_phase={phase}" in caplog.text
    assert "PRIVATE_" not in response.text and "PRIVATE_" not in caplog.text
    assert response.headers["X-ChatPass-Request-ID"] in caplog.text


async def test_header_deadline_is_bounded_and_cancels_upstream(caplog):
    cancelled = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    caplog.set_level("WARNING", logger="chatpass.gateway")
    app, upstream = make_app(handler, config=phase_settings(response_header_timeout_seconds=0.1))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert response.status_code == 504 and cancelled.is_set()
    assert response.json()["error"]["timeout_phase"] == "response_headers"
    assert "timeout_phase=response_headers" in caplog.text


@pytest.mark.parametrize("headerless", [True, False])
async def test_short_header_budget_does_not_shorten_body_read_budget(headerless):
    """Emulate transport read enforcement that MockTransport alone omits."""
    seen = []

    class EnforcedReadStream(TrackedStream):
        def __init__(self, read_timeout):
            super().__init__([HEADERLESS_COMPLETED if headerless else COMPLETED])
            self.read_timeout = read_timeout

        async def __aiter__(self):
            self.started.set()
            try:
                async with asyncio.timeout(self.read_timeout):
                    await asyncio.sleep(0.18)
            except TimeoutError:
                raise httpx.ReadTimeout("Synthetic transport read expired") from None
            yield self.chunks[0]

    def handler(request):
        stream = EnforcedReadStream(request.extensions["timeout"]["read"])
        seen.append(stream)
        return httpx.Response(200, headers={} if headerless else {
            "content-type": "text/event-stream"}, stream=stream)

    app, upstream = make_app(handler, config=phase_settings(
        response_header_timeout_seconds=0.08, stream_idle_timeout_seconds=0.5))
    async with upstream, downstream(app) as client:
        response = await asyncio.wait_for(client.post("/v1/responses", json=PAYLOAD, headers=AUTH), 3)
    assert response.status_code == 200 and "upstream_timeout" not in response.text
    assert len(seen) == 1 and seen[0].read_timeout == 0.5 and seen[0].closed
    assert "response.completed" in response.text


async def test_legacy_settings_preserve_original_transport_budget():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=TrackedStream([COMPLETED]))

    app, upstream = make_app(handler, config=settings(timeout_seconds=30))
    async with upstream, downstream(app) as client:
        response = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    assert seen == [{"connect": 30, "pool": 30, "write": 30, "read": 30}]


@pytest.mark.parametrize("phase", ["PRIVATE_PHASE", {}, [], None, True])
def test_bridge_timeout_phase_rejects_non_enum_metadata(phase):
    from chatpass.bridges import BridgeError, parse_event

    frame = ("data: " + json.dumps({"error": {"code": "upstream_timeout",
             "message": "Safe synthetic message", "timeout_phase": phase}}) + "\n\n").encode()
    with pytest.raises(BridgeError) as caught:
        parse_event(frame)
    assert caught.value.timeout_phase is None
