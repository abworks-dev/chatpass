import json

import pytest
from test_bridges import decode, frame, frames, message, response, tool
from test_compatibility import CHAT, RESPONSE, completed, success
from test_gateway import AUTH, PAYLOAD, downstream, make_app

from chatpass.bridges import BridgeError, chat_completion, chat_stream, collect_response


@pytest.mark.parametrize("item", [message("hello"), tool(),
    {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
    {"type": "custom_tool_call", "call_id": "c", "name": "execute", "input": "code"}])
async def test_collect_recovers_completed_items(item):
    result = await collect_response(frames(
        frame("response.created", response=response()),
        frame("response.output_item.done", output_index=0, item=item),
        frame("response.completed", response=response())))
    assert result["output"] == [item]


async def test_stream_recovers_function_without_duplicate_arguments():
    chunks = [chunk async for chunk in chat_stream(frames(
        frame("response.created", response=response()),
        frame("response.output_item.added", output_index=0, item=tool(arguments="")),
        frame("response.function_call_arguments.delta", output_index=0, delta="{"),
        frame("response.output_item.done", output_index=0, item=tool()),
        frame("response.completed", response=response())), model="m")]
    calls = [call for event in decode(chunks) for choice in event["choices"]
             for call in choice["delta"].get("tool_calls", [])]
    assert "".join(call["function"].get("arguments", "") for call in calls) == "{}"
    assert decode(chunks)[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1] == b"data: [DONE]\n\n"


async def test_done_only_tool_becomes_chat_completion():
    result = await collect_response(frames(
        frame("response.output_item.done", output_index=0, item=tool()),
        frame("response.completed", response=response())))
    assert chat_completion(result, model="m")["choices"][0]["message"]["tool_calls"][0][
        "function"]["arguments"] == "{}"


@pytest.mark.parametrize("ending", [[],
    [frame("response.failed", response=response(status="failed"))],
    [frame("response.incomplete", response=response(status="incomplete"))]])
async def test_done_item_does_not_prove_success(ending):
    with pytest.raises(BridgeError):
        await collect_response(frames(
            frame("response.output_item.done", output_index=0, item=tool()), *ending))


@pytest.mark.parametrize("events", [
    [frame("response.output_item.done", output_index=1, item=tool())],
    [frame("response.output_item.done", output_index=True, item=tool())],
    [frame("response.output_item.done", output_index=0, item=tool()),
     frame("response.output_item.done", output_index=0, item=tool(name="other"))],
    [frame("response.created", response=response(id="other"))],
])
async def test_invalid_reconstruction_fails_closed(events):
    with pytest.raises(BridgeError):
        await collect_response(frames(*events, frame("response.completed", response=response())))


@pytest.mark.parametrize("path,payload", [
    ("/v1/responses", PAYLOAD | {"stream": False}),
    ("/v1/chat/completions", CHAT),
    ("/v1/chat/completions", CHAT | {"stream": True}),
])
async def test_gateway_empty_terminal_tool_roundtrip(path, payload):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return success([
            frame("response.created", response=RESPONSE | {"output": [], "status": "in_progress"}),
            frame("response.output_item.done", output_index=0, item=tool()),
            completed(RESPONSE | {"output": []}),
        ])
    app, upstream = make_app(handler)
    async with upstream, downstream(app) as client:
        result = await client.post(path, json=payload, headers=AUTH)
        assert result.status_code == 200
        if path == "/v1/responses":
            assert result.json()["output"] == [tool()]
            followup = payload | {"input": [tool(), {"type": "function_call_output",
                                                   "call_id": "call_a", "output": "ok"}]}
        elif payload.get("stream"):
            assert result.content.endswith(b"data: [DONE]\n\n")
            events = [json.loads(line[6:]) for line in result.text.splitlines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
            assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"
            return
        else:
            assistant = result.json()["choices"][0]["message"]
            assert assistant["tool_calls"][0]["id"] == "call_a"
            followup = payload | {"messages": [*CHAT["messages"], assistant,
                {"role": "tool", "tool_call_id": "call_a", "content": "ok"}]}
        second = await client.post(path, json=followup, headers=AUTH)
        assert second.status_code == 200
        assert captured[-1]["input"][-1] == {
            "type": "function_call_output", "call_id": "call_a", "output": "ok"}


async def test_added_without_completed_item_cannot_be_empty_success():
    with pytest.raises(BridgeError):
        await collect_response(frames(
            frame("response.output_item.added", output_index=0, item=tool(arguments="")),
            frame("response.completed", response=response())))


@pytest.mark.parametrize("field", ["id", "call_id"])
@pytest.mark.parametrize("empty_terminal", [False, True])
async def test_duplicate_output_identity_is_rejected(field, empty_terminal):
    first = tool() | {"id": "item1"}
    second = tool("call_b") | {"id": "item2"}
    second[field] = first[field]
    with pytest.raises(BridgeError):
        await collect_response(frames(
            frame("response.output_item.done", output_index=0, item=first),
            frame("response.output_item.done", output_index=1, item=second),
            frame("response.completed", response=response(
                [] if empty_terminal else [first, second]))))
    with pytest.raises(BridgeError):
        chat_completion(response([first, second]), model="m")


async def test_completed_item_order_and_identical_repeat_are_stable():
    first = tool() | {"id": "fc1", "status": None}
    second = message("hello") | {"id": "msg1"}
    result = await collect_response(frames(
        frame("response.output_item.done", output_index=1, item=second),
        frame("response.output_item.done", output_index=0, item=first),
        frame("response.output_item.done", output_index=0, item=first),
        frame("response.completed", response=response())))
    assert result["output"] == [first, second]


async def test_reconstruction_retains_existing_stream_size_limit(monkeypatch):
    import chatpass.bridges as bridges
    monkeypatch.setattr(bridges, "_MAX_BYTES", 100)
    with pytest.raises(BridgeError):
        await collect_response(frames(
            frame("response.output_item.done", output_index=0, item=message("x" * 200)),
            frame("response.completed", response=response())))


@pytest.mark.parametrize("bad", [[], {}, None, True])
async def test_nonstring_stream_discriminants_are_safe_errors(bad):
    with pytest.raises(BridgeError):
        await collect_response(frames(frame(bad), frame("response.completed", response=response())))
    with pytest.raises(BridgeError):
        await collect_response(frames(frame("response.completed",
            response=response([{"type": bad}]))))


async def test_nonempty_terminal_conflict_is_not_overwritten():
    with pytest.raises(BridgeError):
        await collect_response(frames(
            frame("response.output_item.done", output_index=0, item=tool()),
            frame("response.completed", response=response([tool(arguments="[]")]))))
