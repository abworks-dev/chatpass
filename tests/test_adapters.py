"""Unit coverage for pure, non-mutating compatibility normalization."""

from copy import deepcopy

import pytest

from chatpass.adapters import AdapterError, normalize_chat, normalize_responses


def test_defaults_and_nonmutation():
    source = {"model": "gpt-test", "input": "hello", "store": True, "temperature": 0.2}
    before = deepcopy(source)
    adapted = normalize_responses(source)
    assert adapted.stream is True
    assert adapted.payload == {
        "model": "gpt-test", "input": [{"role": "user", "content": "hello"}],
        "store": False, "stream": True,
    }
    assert adapted.ignored_fields == ("temperature", "store")
    assert source == before
    chat = normalize_chat({"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]})
    assert chat.stream is False
    assert chat.payload["stream"] is True


@pytest.mark.parametrize("field", [
    "max_output_tokens", "max_tokens", "max_completion_tokens", "temperature", "top_p",
    "top_logprobs", "logprobs", "frequency_penalty", "presence_penalty", "seed", "metadata",
    "user", "safety_identifier", "prompt_cache_retention", "service_tier", "n",
])
def test_inert_strict_and_compatible(field):
    source = {"model": "test", "input": "hi", field: None}
    assert field not in normalize_responses(source).payload
    with pytest.raises(AdapterError) as exc:
        normalize_responses(source, strict=True)
    assert exc.value.code == "unsupported_capability"
    assert exc.value.param == field


@pytest.mark.parametrize("field", [
    "previous_response_id", "conversation", "background", "moderation", "multi_agent",
    "prompt", "max_tool_calls", "truncation", "functions", "function_call", "audio",
    "modalities", "prediction", "stop", "web_search_options",
])
@pytest.mark.parametrize("value", [None, False, "secret-client-value"])
def test_semantic_features_fail_closed(field, value):
    with pytest.raises(AdapterError) as exc:
        normalize_responses({"model": "test", "input": "hi", field: value})
    assert exc.value.code == "unsupported_capability"
    assert "secret-client-value" not in str(exc.value)


def test_responses_history_and_unknown_fields_preserved():
    source = {
        "model": "test", "stream": False, "parallel_tool_calls": True,
        "reasoning": {"effort": "high"}, "future_upstream_option": {"keep": True},
        "tools": [{"type": "function", "name": "lookup", "parameters": {}},
                  {"type": "custom", "name": "shell", "format": {"type": "text"}}],
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": "rules"}]},
            {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "lookup",
             "arguments": "{}"},
            {"type": "function_call_output", "id": "fo1", "call_id": "c1", "output": "ok"},
            {"type": "custom_tool_call", "id": "ct1", "call_id": "c2", "name": "shell",
             "input": "pwd"},
            {"type": "custom_tool_call_output", "id": "co1", "call_id": "c2", "output": "/"},
        ],
    }
    before = deepcopy(source)
    adapted = normalize_responses(source, strict=True)
    assert adapted.stream is False
    assert adapted.payload["input"][0]["role"] == "developer"
    assert adapted.payload["input"][1:] == source["input"][1:]
    assert adapted.payload["tools"] == source["tools"]
    assert adapted.payload["future_upstream_option"] == {"keep": True}
    assert source == before


def test_responses_string_and_opaque_reasoning():
    adapted = normalize_responses({"model": "test", "input": "hello"})
    assert adapted.payload["input"] == [{"role": "user", "content": "hello"}]
    assert adapted.stream is True
    item = {"type": "reasoning", "id": "r1", "encrypted_content": "opaque"}
    source = {"model": "test", "input": [item]}
    assert normalize_responses(source).payload["input"] == [item]
    assert source["input"] == [item]


@pytest.mark.parametrize("source, code", [
    (None, "invalid_json"),
    ({"model": None, "input": "hi"}, "invalid_model"),
    ({"model": "test", "input": None}, "invalid_input"),
    ({"model": "test", "input": "hi", "stream": "yes"}, "invalid_stream"),
    ({"model": "test", "input": "hi", "tools": {}}, "invalid_tools"),
])
def test_legacy_validation_codes(source, code):
    with pytest.raises(AdapterError) as exc:
        normalize_responses(source)
    assert exc.value.code == code


def test_chat_tool_roundtrip():
    source = {
        "model": "test", "parallel_tool_calls": False,
        "tools": [{"type": "function", "function": {
            "name": "lookup", "description": "Search", "parameters": {"type": "object"},
            "strict": True,
        }}],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "developer", "content": "more rules"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "looking", "tool_calls": [
                {"id": "call-1", "type": "function", "function": {
                    "name": "lookup", "arguments": '{"q":"x"}',
                }},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "content": "answer"},
            {"role": "assistant", "content": "done"},
        ],
    }
    before = deepcopy(source)
    adapted = normalize_chat(source)
    assert adapted.payload["input"][0]["role"] == "developer"
    assert adapted.payload["input"][3]["content"] == [{"type": "output_text", "text": "looking"}]
    assert adapted.payload["input"][4] == {
        "type": "function_call", "call_id": "call-1", "name": "lookup",
        "arguments": '{"q":"x"}',
    }
    assert adapted.payload["input"][5] == {
        "type": "function_call_output", "call_id": "call-1", "output": "answer",
    }
    assert adapted.payload["tool_choice"] == {"type": "function", "name": "lookup"}
    assert adapted.payload["tools"][0]["strict"] is True
    assert adapted.payload["parallel_tool_calls"] is False
    assert source == before


def test_multimodal_and_formats():
    source = {
        "model": "test", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "low"}},
        ]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "result", "schema": {"type": "object"}, "strict": True,
        }},
    }
    adapted = normalize_chat(source)
    assert adapted.payload["input"][0]["content"][1] == {
        "type": "input_image", "image_url": "data:image/png;base64,abc", "detail": "low",
    }
    assert adapted.payload["text"]["format"] == {
        "type": "json_schema", "name": "result", "schema": {"type": "object"}, "strict": True,
    }
    source["response_format"] = {"type": "json_object"}
    assert normalize_chat(source).payload["text"] == {"format": {"type": "json_object"}}


def test_usage_and_store():
    source = {"model": "test", "input": "hi", "stream_options": {"include_usage": True}}
    adapted = normalize_responses(source)
    assert adapted.include_usage is True
    assert adapted.ignored_fields == ("stream_options",)
    with pytest.raises(AdapterError):
        normalize_responses(source, strict=True)
    assert normalize_responses({"model": "test", "input": "hi", "store": False}, strict=True)
    with pytest.raises(AdapterError):
        normalize_responses({"model": "test", "input": "hi", "store": True}, strict=True)


@pytest.mark.parametrize("patch", [
    {"n": 2}, {"n": True}, {"n": 1.0}, {"functions": []}, {"function_call": "auto"},
    {"response_format": {"type": "json_schema", "json_schema": {"name": "x"}}},
    {"tools": [{"type": "web_search"}]}, {"unexpected-secret-field": "secret"},
    {"stream_options": {"include_usage": "yes"}},
    {"tool_choice": {"type": "function", "function": {"name": 42}}},
])
def test_chat_invalid_options(patch):
    with pytest.raises(AdapterError) as exc:
        normalize_chat({"model": "test", "messages": [{"role": "user", "content": "hi"}], **patch})
    assert "secret" not in str(exc.value)
    assert "secret" not in (exc.value.param or "")


@pytest.mark.parametrize("message", [
    {"role": "unknown", "content": "hi"},
    {"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]},
    {"role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]},
    {"role": "assistant", "content": "hi", "refusal": "Cannot answer"},
    {"role": "user", "content": "hi", "name": "someone"},
    {"role": "tool", "tool_call_id": 7, "content": "ok"},
    {"role": "assistant", "content": None},
    {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {
        "name": "x", "arguments": {},
    }}]},
])
def test_chat_invalid_messages(message):
    with pytest.raises(AdapterError):
        normalize_chat({"model": "test", "messages": [message]})


@pytest.mark.parametrize("source", [
    None, [], {}, {"model": 1, "input": "hi"}, {"model": "x", "input": None},
    {"model": "x", "input": "hi", "stream": 1},
    {"model": "x", "input": "hi", "tools": {}},
    {"model": "x", "input": [{"type": "function_call", "call_id": 1, "name": "x", "arguments": ""}]},
    {"model": "x", "input": [{"type": "item_reference", "id": "old"}]},
    {"model": "x", "input": [{"role": "system", "content": [{"type": "input_text", "text": 7}]}]},
    {"model": "x", "input": "hi", "text": {"format": {"type": "json_schema", "name": "x"}}},
])
def test_responses_invalid_shapes(source):
    with pytest.raises(AdapterError):
        normalize_responses(source)


def test_chat_sdk_nulls_tool_name_and_reasoning_effort():
    source = {
        "model": "test", "reasoning_effort": "high", "reasoning": {"summary": "auto"},
        "messages": [
            {"role": "assistant", "content": "hi", "refusal": None, "tool_calls": None},
            {"role": "tool", "tool_call_id": "c1", "name": "lookup", "content": "ok"},
        ],
    }
    before = deepcopy(source)
    adapted = normalize_chat(source)
    assert adapted.payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoning_effort" not in adapted.payload
    assert adapted.payload["input"] == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
    ]
    assert source == before
    source["reasoning"]["effort"] = "low"
    with pytest.raises(AdapterError) as exc:
        normalize_chat(source)
    assert exc.value.param == "reasoning_effort"
    source["reasoning"]["effort"] = "high"
    assert normalize_chat(source).payload["reasoning"]["effort"] == "high"


@pytest.mark.parametrize("strict", [False, True])
def test_nullable_absent_sdk_fields(strict):
    source = {
        "model": "test", "messages": [{
            "role": "assistant", "content": "plain reply", "tool_calls": None,
            "refusal": None, "audio": None, "function_call": None, "annotations": None,
        }],
        "tools": [{"type": "function", "function": {"name": "lookup", "strict": None}}],
    }
    before = deepcopy(source)
    adapted = normalize_chat(source, strict=strict)
    assert adapted.payload["input"] == [{
        "role": "assistant", "content": [{"type": "output_text", "text": "plain reply"}],
    }]
    assert adapted.payload["tools"] == [{"type": "function", "name": "lookup"}]
    assert source == before
    native = {"model": "test", "input": [{
        "type": "reasoning", "id": "r1", "encrypted_content": None,
    }], "tools": [{"type": "function", "name": "lookup", "strict": None}]}
    native_before = deepcopy(native)
    result = normalize_responses(native, strict=strict).payload
    assert result["input"] == [{"type": "reasoning", "id": "r1"}]
    assert result["tools"] == [{"type": "function", "name": "lookup"}]
    assert native == native_before


@pytest.mark.parametrize("field", ["audio", "function_call", "annotations"])
@pytest.mark.parametrize("value", ["nonempty", {}, []])
def test_nonnull_sdk_features_rejected(field, value):
    with pytest.raises(AdapterError) as exc:
        normalize_chat({"model": "test", "messages": [{
            "role": "assistant", "content": "reply", field: value,
        }]})
    assert exc.value.code == "unsupported_capability"


def test_chat_n_one_and_stream_usage():
    source = {
        "model": "test", "messages": [{"role": "user", "content": "hi"}], "n": 1,
        "stream": True, "stream_options": {"include_usage": True},
    }
    adapted = normalize_chat(source)
    assert adapted.stream is True and adapted.include_usage is True
    assert adapted.ignored_fields == ("stream_options", "n")
    with pytest.raises(AdapterError):
        normalize_chat(source, strict=True)
