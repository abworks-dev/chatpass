"""Client compatibility integration tests against mock official upstream only."""

import asyncio
import json

import httpx
import pytest
from test_gateway import AUTH, PAYLOAD, TrackedStream, downstream, make_app, settings

CHAT = {"model": "real-model", "messages": [{"role": "user", "content": "hello"}]}
RESPONSE = {"id": "resp_mock", "object": "response", "status": "completed", "created_at": 42,
            "model": "real-model", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello"}]}],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}


def frame(payload):
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def completed(response=RESPONSE):
    return frame({"type": "response.completed", "response": response})


def success(chunks=None):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                         stream=TrackedStream(chunks or [completed()]))


async def test_default_compatibility_filters_dsh_tuning_and_preserves_history():
    captured = []
    app, upstream = make_app(lambda request: captured.append(json.loads(request.content)) or success())
    history = [{"role": "system", "content": "Be helpful"},
               {"type": "reasoning", "id": "reason1", "encrypted_content": "opaque"},
               {"type": "function_call", "call_id": "call1", "name": "lookup", "arguments": "{}"},
               {"type": "function_call_output", "call_id": "call1", "output": "ok"}]
    payload = PAYLOAD | {"input": history, "max_output_tokens": 2048, "temperature": 0.5,
                         "metadata": {"secret-field": "private"}}
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=payload, headers=AUTH)
    assert result.status_code == 200
    assert result.headers["x-chatpass-compatibility"] == "compatible"
    assert set(result.headers["x-chatpass-ignored-fields"].split(",")) == {
        "max_output_tokens", "temperature", "metadata"}
    assert "private" not in result.headers["x-chatpass-ignored-fields"]
    assert captured[0]["input"] == [{"role": "developer", "content": "Be helpful"}] + history[1:]
    assert captured[0]["store"] is False and captured[0]["stream"] is True
    assert not set(captured[0]) & {"max_output_tokens", "temperature", "metadata"}


@pytest.mark.parametrize("path,payload", [("/v1/responses", PAYLOAD), ("/v1/chat/completions", CHAT)])
@pytest.mark.parametrize("field", ["previous_response_id", "conversation", "background", "truncation"])
async def test_stateful_semantics_rejected_before_credentials(path, payload, field):
    from test_gateway import Credentials
    credentials = Credentials()
    app, upstream = make_app(lambda _: pytest.fail("must not forward"), credentials=credentials)
    async with upstream, downstream(app) as client:
        result = await client.post(path, json=payload | {field: "hidden-secret"}, headers=AUTH)
    assert result.status_code == 400
    assert result.json()["error"]["param"] == field
    assert "hidden-secret" not in result.text and credentials.calls == 0


@pytest.mark.parametrize("path,payload", [("/v1/responses", PAYLOAD), ("/v1/chat/completions", CHAT)])
async def test_strict_mode_rejects_inert_fields(path, payload):
    app, upstream = make_app(lambda _: pytest.fail("must not forward"),
                             config=settings(compatibility_mode="strict"))
    async with upstream, downstream(app) as client:
        result = await client.post(path, json=payload | {"temperature": 0.7}, headers=AUTH)
    assert result.status_code == 400 and result.json()["error"]["param"] == "temperature"


async def test_nonstream_responses_returns_authoritative_completed_object():
    captured = []
    stream = TrackedStream([frame({"type": "response.output_text.delta", "delta": "not-final"}),
                            completed()])
    app, upstream = make_app(lambda request: captured.append(json.loads(request.content)) or
                            httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD | {"stream": False}, headers=AUTH)
    assert result.status_code == 200 and result.json() == RESPONSE
    assert captured[0]["stream"] is True and stream.closed


async def test_chat_nonstream_tools_roundtrip_and_usage():
    captured = []
    tool = {"type": "function_call", "call_id": "call_next", "name": "lookup", "arguments": '{"q":"hi"}'}
    app, upstream = make_app(lambda request: captured.append(request) or
                            success([completed(RESPONSE | {"output": [tool]})]))
    payload = CHAT | {"messages": [{"role": "system", "content": "Use tools"},
                                   {"role": "assistant", "content": None, "tool_calls": [{
                                       "id": "call_old", "type": "function", "function": {
                                           "name": "lookup", "arguments": "{}"}}]},
                                   {"role": "tool", "tool_call_id": "call_old", "content": "ok"}],
                      "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {
                          "type": "object", "properties": {}}}}]}
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/chat/completions", json=payload, headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["choices"][0]["message"]["tool_calls"][0]["id"] == "call_next"
    assert body["usage"]["prompt_tokens"] == 5
    wire = json.loads(captured[0].content)
    assert captured[0].url == "https://api.openai.com/v1/responses"
    assert captured[0].headers["authorization"] == "Bearer upstream-secret"
    assert wire["input"][-1] == {"type": "function_call_output", "call_id": "call_old", "output": "ok"}
    assert wire["tools"][0]["name"] == "lookup"
    assert wire["store"] is False and wire["stream"] is True


async def test_chat_stream_translates_deltas_usage_and_success_terminal():
    chunks = [frame({"type": "response.created", "response": RESPONSE | {"status": "in_progress", "output": []}}),
              frame({"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
                     "delta": "Hello"}), completed()]
    app, upstream = make_app(lambda _: success(chunks))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/chat/completions", json=CHAT | {
            "stream": True, "stream_options": {"include_usage": True}}, headers=AUTH)
    assert result.status_code == 200 and result.content.endswith(b"data: [DONE]\n\n")
    events = [json.loads(line[6:]) for line in result.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    text = "".join(event["choices"][0]["delta"].get("content", "") for event in events if event["choices"])
    assert text == "Hello"
    assert events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[-1]["choices"] == [] and events[-1]["usage"]["total_tokens"] == 7


@pytest.mark.parametrize("path,payload", [("/v1/responses", PAYLOAD | {"stream": False}),
                                         ("/v1/chat/completions", CHAT),
                                         ("/v1/chat/completions", CHAT | {"stream": True})])
@pytest.mark.parametrize("broken", [b"data: not-json\n\n", frame({"type": "response.failed", "error": {
    "message": "upstream-secret"}}), frame({"type": "response.output_text.delta", "delta": "partial"})])
async def test_bridged_failure_never_claims_success_or_echoes_error(path, payload, broken):
    stream = TrackedStream([broken])
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream,
                             headers={"content-type": "text/event-stream"}))
    async with upstream, downstream(app) as client:
        result = await client.post(path, json=payload, headers=AUTH)
    assert stream.closed and b"upstream-secret" not in result.content
    assert b"[DONE]" not in result.content and b'"finish_reason": "stop"' not in result.content
    if payload.get("stream"):
        assert result.status_code == 200 and b"event: error" in result.content
    else:
        assert result.status_code == 502 and "error" in result.json()


@pytest.mark.parametrize("path,payload", [("/v1/responses", PAYLOAD | {"stream": False}),
                                         ("/v1/chat/completions", CHAT)])
async def test_nonstream_timeout_is_http_error_and_releases_slot(path, payload):
    stream = TrackedStream([b": ping\n\n"], gate=asyncio.Event())
    def handler(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    app, upstream = make_app(handler, config=settings(timeout_seconds=0.5, max_concurrency=1))
    async with upstream, downstream(app) as client:
        result = await asyncio.wait_for(client.post(path, json=payload, headers=AUTH), 2)
        assert result.status_code == 504 and result.json()["error"]["code"] == "upstream_timeout"
        assert stream.closed
        assert (await client.get("/v1/models", headers=AUTH)).status_code == 200


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prelude", ["explicit", "headerless", "headerless-heartbeat"])
async def test_chat_disconnect_closes_upstream_and_releases_admission(streaming, prelude):
    first = (b": heartbeat\n\n" if prelude == "headerless-heartbeat" else
             frame({"type": "response.created", "response": RESPONSE | {
                 "status": "in_progress", "output": []}}))
    stream = TrackedStream([first], gate=asyncio.Event())
    app, upstream = make_app(lambda request: httpx.Response(200, json={"models": []})
                            if request.url.path.endswith("models") else
                            httpx.Response(200, stream=stream, headers=(
                                {"content-type": "text/event-stream"} if prelude == "explicit" else {})),
                            config=settings(max_concurrency=1))
    disconnected = asyncio.Event()
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(CHAT | {"stream": streaming}).encode(),
                    "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        pass
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/chat/completions",
             "raw_path": b"/v1/chat/completions", "query_string": b"", "root_path": "",
             "headers": [(b"authorization", b"Bearer gateway-secret"), (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("gateway", 80)}
    async with upstream, downstream(app) as client:
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(stream.started.wait(), 2)
            assert (await client.get("/v1/models", headers=AUTH)).status_code == 429
            disconnected.set()
            await asyncio.wait_for(task, 2)
            assert stream.closed
            assert (await client.get("/v1/models", headers=AUTH)).status_code == 200
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_nonstream_completed_requires_full_object():
    app, upstream = make_app(lambda _: success([frame({"type": "response.completed"})]))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD | {"stream": False}, headers=AUTH)
    assert result.status_code == 502


@pytest.mark.parametrize("payload,status", [(CHAT, 401), (CHAT | {"stream": "yes"}, 400)])
async def test_chat_authentication_and_validation_precede_upstream(payload, status):
    app, upstream = make_app(lambda _: pytest.fail("must not forward"))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/chat/completions", json=payload,
                                   headers=AUTH if status == 400 else {})
    assert result.status_code == status


@pytest.mark.parametrize("path,payload", [("/v1/responses", PAYLOAD), ("/v1/chat/completions", CHAT)])
@pytest.mark.parametrize("streaming", [True, False])
async def test_headerless_valid_sse_is_probed_and_replayed(path, payload, streaming):
    body = b": heartbeat\n\n" + frame({"type": "response.created", "response": RESPONSE | {
        "status": "in_progress", "output": []}}) + completed()
    stream = TrackedStream([body[:5], body[5:19], body[19:]])
    calls = []
    app, upstream = make_app(lambda request: calls.append(request) or httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        result = await client.post(path, json=payload | {"stream": streaming}, headers=AUTH)
    assert result.status_code == 200
    assert stream.closed and len(calls) == 1
    if streaming:
        assert result.headers["content-type"].startswith("text/event-stream")
        if path == "/v1/responses":
            assert result.content == body
        else:
            assert b"[DONE]" in result.content and b"Hello" in result.content
    elif path == "/v1/responses":
        assert result.json() == RESPONSE
    else:
        assert result.json()["choices"][0]["message"]["content"] == "Hello"


@pytest.mark.parametrize("body", [b"", b'{"secret":"upstream-secret"}',
    b"<html>upstream-secret</html>", b": upstream-secret\n\n",
    frame({"type": "other", "secret": "upstream-secret"}),
    frame({"type": "error", "message": "upstream-secret"}),
    frame({"type": "response.arbitrary", "secret": "upstream-secret"}),
    b'event: response.created\ndata: {"secret":"upstream-secret"}\n\n',
    frame({"type": "response.completed"}),
    frame({"type": "response.completed", "response": RESPONSE | {"status": "failed"}})])
async def test_headerless_non_sse_fails_before_downstream_success(body):
    stream = TrackedStream([body])
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert result.status_code == 502
    assert "upstream-secret" not in result.text
    assert stream.closed


async def test_headerless_probe_is_bounded_and_does_not_retry():
    from chatpass.gateway import MAX_SSE_FRAME_BYTES
    stream = TrackedStream([b": heartbeat\n\n"] * (MAX_SSE_FRAME_BYTES // 13 + 1))
    calls = []
    app, upstream = make_app(lambda request: calls.append(request) or httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert result.status_code == 502
    assert stream.closed and len(calls) == 1


async def test_headerless_valid_prelude_does_not_hide_incomplete_stream():
    stream = TrackedStream([frame({"type": "response.created", "response": RESPONSE | {
        "status": "in_progress", "output": []}})])
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert result.status_code == 200
    assert b"event: error" in result.content and b"response.completed" not in result.content
    assert stream.closed


async def test_headerless_probe_timeout_closes_and_releases_capacity():
    stream = TrackedStream(gate=asyncio.Event())
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream),
                             config=settings(timeout_seconds=0.5, max_concurrency=1))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
        assert result.status_code == 504 and stream.closed
        again = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
        assert again.status_code != 429


@pytest.mark.parametrize("content_type", ["", "; charset=utf-8", "text/plain", "application/json"])
async def test_explicit_invalid_media_type_is_not_headerless(content_type):
    stream = TrackedStream([completed()])
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream,
                             headers={"content-type": content_type}))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert result.status_code == 502
    assert stream.closed and not stream.started.is_set()


async def test_headerless_completed_prelude_with_final_cr_is_replayed():
    body = completed().replace(b"\n", b"\r")
    stream = TrackedStream([body])
    app, upstream = make_app(lambda _: httpx.Response(200, stream=stream))
    async with upstream, downstream(app) as client:
        result = await client.post("/v1/responses", json=PAYLOAD, headers=AUTH)
    assert result.status_code == 200 and result.content == body
    assert stream.closed
