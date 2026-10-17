from copy import deepcopy

import pytest
from test_bridges import response, tool

from chatpass.adapters import AdapterError, normalize_chat, normalize_responses
from chatpass.bridges import BridgeError, chat_completion

NAMESPACE = {"type": "namespace", "name": "workspace", "description": "Local tools",
             "tools": [{"type": "function", "name": "lookup", "parameters": None,
                        "description": None, "strict": None},
                       {"type": "custom", "name": "execute", "format": {"type": "text"}}]}


@pytest.mark.parametrize("strict", [False, True])
async def test_namespaced_tools_and_additional_tools_preserve_native_history(strict):
    source = {"model": "m", "input": [
        {"type": "additional_tools", "role": "developer", "id": None, "tools": [NAMESPACE]},
        {"type": "function_call", "call_id": "c", "name": "lookup",
         "namespace": "workspace", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "namespace": "workspace",
         "name": "lookup", "output": "ok"},
    ], "tools": [NAMESPACE], "tool_choice": {
        "type": "function", "name": "lookup", "namespace": "workspace"},
        "reasoning": {"effort": "high", "summary": "auto"},
        "text": {"verbosity": "low"}, "include": ["reasoning.encrypted_content"]}
    original = deepcopy(source)
    result = normalize_responses(source, strict=strict)
    assert source == original
    assert result.payload["input"][1:] == source["input"][1:]
    assert result.payload["tools"][0]["tools"][0]["parameters"] is None
    assert "strict" not in result.payload["tools"][0]["tools"][0]
    for field in ("tool_choice", "reasoning", "text", "include"):
        assert result.payload[field] == source[field]
    assert not result.ignored_fields
    assert result.payload["store"] is False and result.payload["stream"] is True


@pytest.mark.parametrize("kind", ["web_search", "web_search_preview",
                                  "web_search_preview_2025_03_11"])
def test_conditional_web_search_forwarded_without_local_execution(kind):
    declared = {"type": kind, "search_context_size": "low"}
    source = {"model": "m", "input": [{"type": "web_search_call", "id": "w1",
        "status": "completed", "action": {"type": "search", "query": "example"}}],
        "tools": [declared], "tool_choice": {"type": kind}}
    result = normalize_responses(source, strict=True)
    assert result.payload["tools"] == [declared]
    assert result.payload["input"] == source["input"]


@pytest.mark.parametrize("kind", ["tool_search", "programmatic_tool_calling", "file_search",
    "code_interpreter", "computer_use_preview", "image_generation", "mcp"])
@pytest.mark.parametrize("where", ["top", "namespace", "additional"])
def test_unsupported_hosted_tools_remain_blocked(kind, where):
    declaration = {"type": kind, "name": "unsupported"}
    source = {"model": "m", "input": "hi"}
    if where == "top":
        source["tools"] = [declaration]
    elif where == "namespace":
        source["tools"] = [NAMESPACE | {"tools": [declaration]}]
    else:
        source["input"] = [{"type": "additional_tools", "role": "developer",
                            "tools": [declaration]}]
    with pytest.raises(AdapterError):
        normalize_responses(source)


@pytest.mark.parametrize("item", [
    {"type": "additional_tools", "role": "user", "tools": []},
    {"type": "additional_tools", "role": "developer", "tools": {}},
    {"type": "additional_tools", "role": "developer", "tools": [None]},
    {"type": "function_call", "call_id": "c", "name": "lookup", "arguments": "{}",
     "namespace": 1},
])
def test_malformed_tool_history_is_rejected(item):
    with pytest.raises(AdapterError):
        normalize_responses({"model": "m", "input": [item]})


@pytest.mark.parametrize("bad", [[], {}, None, True])
@pytest.mark.parametrize("field", ["tools", "tool_choice", "namespace_child"])
def test_nonstring_discriminants_are_client_errors(bad, field):
    source = {"model": "m", "input": "hi"}
    if field == "tools":
        source["tools"] = [{"type": bad}]
    elif field == "tool_choice":
        source["tool_choice"] = {"type": bad}
    else:
        source["tools"] = [NAMESPACE | {"tools": [{"type": bad}]}]
    with pytest.raises(AdapterError):
        normalize_responses(source)


def test_chat_does_not_silently_drop_namespace():
    with pytest.raises(BridgeError):
        chat_completion(response([tool() | {"namespace": "workspace"}]), model="m")
    with pytest.raises(AdapterError):
        normalize_chat({"model": "m", "messages": [{"role": "user", "content": "hi"}],
                        "tools": [NAMESPACE]})
