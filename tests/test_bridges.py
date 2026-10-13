import json
from collections.abc import AsyncIterator

import pytest

from chatpass.bridges import (
    BridgeError,
    chat_completion,
    chat_stream,
    collect_response,
    parse_event,
)


def frame(kind: str, **values) -> bytes:
    return b"data: " + json.dumps({"type": kind, **values}).encode() + b"\n\n"


async def frames(*events: bytes) -> AsyncIterator[bytes]:
    for event in events:
        yield event


def response(output=None, **values):
    return {"id": "resp_1", "created_at": 123, "status": "completed",
            "output": [] if output is None else output, **values}


def message(text):
    return {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


def tool(call_id="call_a", name="lookup", arguments="{}"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def decode(chunks):
    return [json.loads(chunk[6:]) for chunk in chunks if chunk != b"data: [DONE]\n\n"]


@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
def test_parse_multiline_comments(newline):
    event = newline.join([b": heartbeat", b"event: ignored", b'data: {"type":',
                          b'data: "response.created"}', b"", b""])
    assert parse_event(event) == {"type": "response.created"}
    assert parse_event(b": ping\n\n") is None
    assert parse_event(b"data: [DONE]\n\n") is None


@pytest.mark.parametrize("payload", [b"NaN", b"Infinity", b"1e999", b"[]", b"{", b"\xff"])
def test_parse_rejects_invalid_and_nonfinite(payload):
    with pytest.raises(BridgeError):
        parse_event(b"data: " + payload + b"\n\n")


def test_gateway_error_preserves_only_safe_code_message():
    with pytest.raises(BridgeError) as caught:
        parse_event(b'data: {"error":{"code":"upstream_timeout","message":"Timed out.",'
                    b'"extra":"discard"}}\n\n')
    assert caught.value.code == "upstream_timeout"
    assert caught.value.message == "Timed out."
    assert not hasattr(caught.value, "extra")


async def test_collect_authoritative_not_deltas():
    completed = response([message("snapshot")])
    actual = await collect_response(frames(
        frame("response.output_text.delta", delta="other"),
        frame("response.completed", response=completed)))
    assert actual == completed


@pytest.mark.parametrize("completed", [{}, response(output={}), response(id=""),
                                        response(status="incomplete")])
async def test_collect_rejects_malformed_completed(completed):
    with pytest.raises(BridgeError):
        await collect_response(frames(frame("response.completed", response=completed)))


async def test_collect_truncated():
    with pytest.raises(BridgeError, match="did not complete"):
        await collect_response(frames(frame("response.output_text.delta", delta="hello")))


def test_completion_text_tools_and_usage():
    usage = {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9,
             "input_tokens_details": {"cached_tokens": 2},
             "output_tokens_details": {"reasoning_tokens": 3}}
    result = chat_completion(response([message("a"), tool(), message("b"),
                                       tool("call_b", "second", '{"x":1}')], usage=usage),
                             model="requested-model")
    assert result["model"] == "requested-model"
    choice = result["choices"][0]
    assert choice["message"]["content"] == "ab"
    assert choice["finish_reason"] == "tool_calls"
    assert [t["id"] for t in choice["message"]["tool_calls"]] == ["call_a", "call_b"]
    assert result["usage"] == {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9,
                               "prompt_tokens_details": {"cached_tokens": 2},
                               "completion_tokens_details": {"reasoning_tokens": 3}}


@pytest.mark.parametrize("output", [[{"type": "custom_tool_call"}],
                                    [{"type": "message", "content": [{"type": "refusal"}]}],
                                    [{"type": "message", "content": [{"type": "output_audio"}]}]])
def test_unsupported_output(output):
    with pytest.raises(BridgeError) as caught:
        chat_completion(response(output), model="m")
    assert caught.value.code == "unsupported_response"


async def test_text_stream_and_usage_no_duplicate_snapshot():
    completed = response([message("hello")], usage={"input_tokens": 1, "output_tokens": 2,
                                                   "total_tokens": 3})
    chunks = [c async for c in chat_stream(frames(
        frame("response.created", response=response()),
        frame("response.output_text.delta", output_index=0, content_index=0, delta="hel"),
        frame("response.output_text.delta", output_index=0, content_index=0, delta="lo"),
        frame("response.completed", response=completed)), model="m", include_usage=True)]
    assert chunks[-1] == b"data: [DONE]\n\n"
    parsed = decode(chunks)
    assert {p["id"] for p in parsed} == {"resp_1"}
    assert {p["created"] for p in parsed} == {123}
    assert "".join(p["choices"][0]["delta"].get("content", "")
                   for p in parsed if p["choices"]) == "hello"
    assert parsed[-2]["choices"][0]["finish_reason"] == "stop"
    assert parsed[-1]["choices"] == []
    assert parsed[-1]["usage"]["total_tokens"] == 3


async def test_interleaved_tools_contiguous_indices_split_args():
    completed = response([message("hello"), tool(arguments='{"a":1}'), message("!"),
                          tool("call_b", "second", "[]")])
    chunks = [c async for c in chat_stream(frames(
        frame("response.created", response=response()),
        frame("response.output_item.added", output_index=1, item=tool(arguments="")),
        frame("response.output_item.added", output_index=3,
              item=tool("call_b", "second", "")),
        frame("response.function_call_arguments.delta", output_index=1, delta='{"a":'),
        frame("response.function_call_arguments.delta", output_index=3, delta="["),
        frame("response.function_call_arguments.delta", output_index=1, delta="1}"),
        frame("response.completed", response=completed)), model="m")]
    calls = [call for p in decode(chunks) for choice in p["choices"]
             for call in choice["delta"].get("tool_calls", [])]
    assert {c["index"] for c in calls} == {0, 1}
    assert [c["id"] for c in calls if "id" in c] == ["call_a", "call_b"]
    assert [c["function"]["name"] for c in calls if "name" in c["function"]] == [
        "lookup", "second"]
    assert "".join(c["function"].get("arguments", "") for c in calls if c["index"] == 0) == (
        '{"a":1}')
    assert "".join(c["function"].get("arguments", "") for c in calls if c["index"] == 1) == "[]"
    assert decode(chunks)[-1]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.parametrize("output", [[], [message("only completed")], [tool()]])
async def test_completed_only_and_empty(output):
    chunks = [c async for c in chat_stream(frames(frame("response.completed",
                                                       response=response(output))), model="m")]
    parsed = decode(chunks)
    assert parsed[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert not any(not p["choices"] for p in parsed)
    assert chunks[-1] == b"data: [DONE]\n\n"
    if output and output[0]["type"] == "message":
        assert parsed[1]["choices"][0]["delta"]["content"] == "only completed"


@pytest.mark.parametrize("ending", [
    [],
    [frame("response.completed", response={})],
    [b'data: {"error":{"code":"stream_interrupted","message":"Safe failure."}}\n\n'],
    [frame("response.failed", response={"error": "secret"})],
    [frame("response.function_call_arguments.delta", output_index=0, delta="oops")],
    [frame("response.completed", response=response([message("different")]))],
])
async def test_failure_has_no_done(ending):
    chunks = []
    with pytest.raises(BridgeError):
        async for chunk in chat_stream(frames(
            frame("response.created", response=response()),
            frame("response.output_text.delta", output_index=0, content_index=0, delta="hello"),
            *ending), model="m"):
            chunks.append(chunk)
    assert b"data: [DONE]\n\n" not in chunks


async def test_reasoning_metadata_not_visible():
    completed = response([{"type": "reasoning", "encrypted_content": "opaque"}, message("ok")])
    assert chat_completion(completed, model="m")["choices"][0]["message"]["content"] == "ok"
    chunks = [c async for c in chat_stream(frames(
        frame("response.created", response=response()),
        frame("response.output_item.added", output_index=0,
              item={"type": "reasoning", "encrypted_content": "opaque"}),
        frame("response.completed", response=completed)), model="m")]
    assert b"opaque" not in b"".join(chunks)
    assert chunks[-1] == b"data: [DONE]\n\n"


async def test_stream_identity_mismatch():
    with pytest.raises(BridgeError):
        async for _ in chat_stream(frames(
            frame("response.created", response=response()),
            frame("response.completed", response=response(id="different"))), model="m"):
            pass


@pytest.mark.parametrize("same_message", [False, True])
async def test_missing_earlier_suffix_after_later_text_fails_closed(same_message):
    if same_message:
        output = [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "ab"},
            {"type": "output_text", "text": "c"}]}]
        later = {"output_index": 0, "content_index": 1}
    else:
        output = [message("ab"), message("c")]
        later = {"output_index": 1, "content_index": 0}
    chunks = []
    with pytest.raises(BridgeError):
        async for chunk in chat_stream(frames(
            frame("response.created", response=response()),
            frame("response.output_text.delta", output_index=0, content_index=0, delta="a"),
            frame("response.output_text.delta", **later, delta="c"),
            frame("response.output_text.done", output_index=0, content_index=0, text="ab"),
            frame("response.completed", response=response(output))), model="m"):
            chunks.append(chunk)
    assert b"data: [DONE]\n\n" not in chunks
    assert not any(p["choices"][0]["finish_reason"] for p in decode(chunks))


async def test_global_prefix_without_valid_suffix_order_fails_closed():
    # 'a' is a global prefix, but it came from the later part; per-part
    # suffix recovery would append 'ab' + 'c', producing 'aabc' not 'abac'.
    chunks = []
    with pytest.raises(BridgeError):
        async for chunk in chat_stream(frames(
            frame("response.created", response=response()),
            frame("response.output_text.delta", output_index=1, content_index=0, delta="a"),
            frame("response.completed", response=response([message("ab"), message("ac")]))),
            model="m"):
            chunks.append(chunk)
    assert b"data: [DONE]\n\n" not in chunks


async def test_cross_part_delta_order_fails_closed():
    chunks = []
    with pytest.raises(BridgeError):
        async for chunk in chat_stream(frames(
            frame("response.created", response=response()),
            frame("response.output_text.delta", output_index=1, content_index=0, delta="b"),
            frame("response.output_text.delta", output_index=0, content_index=0, delta="a"),
            frame("response.completed", response=response([message("a"), message("b")]))),
            model="m"):
            chunks.append(chunk)
    assert b"data: [DONE]\n\n" not in chunks


async def test_snapshot_missing_text_suffix():
    chunks = [c async for c in chat_stream(frames(
        frame("response.created", response=response()),
        frame("response.output_text.delta", output_index=0, content_index=0, delta="hel"),
        frame("response.completed", response=response([message("hello")]))), model="m")]
    assert [p["choices"][0]["delta"]["content"] for p in decode(chunks)
            if "content" in p["choices"][0]["delta"]] == ["hel", "lo"]
