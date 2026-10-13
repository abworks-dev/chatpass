"""Pure client request adapters for the stateless Responses upstream.

No transport or credential handling belongs here. Compatibility mode ignores only
explicitly enumerated inert options; stateful or lossy features fail closed.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class AdapterError(Exception):
    """A client-safe error: messages and parameter names never echo client data."""

    def __init__(self, code: str, message: str, param: str | None = None) -> None:
        self.code = code
        self.message = message
        self.param = param
        super().__init__(message)


@dataclass
class AdaptedRequest:
    payload: dict[str, Any]
    stream: bool
    include_usage: bool = False
    ignored_fields: tuple[str, ...] = ()


_INERT = (
    "max_output_tokens", "max_tokens", "max_completion_tokens", "temperature",
    "top_p", "top_logprobs", "logprobs", "frequency_penalty", "presence_penalty",
    "seed", "metadata", "user", "safety_identifier", "prompt_cache_retention",
    "stream_options", "service_tier", "n",
)
_SEMANTIC = (
    "previous_response_id", "conversation", "background", "moderation", "multi_agent",
    "prompt", "max_tool_calls", "truncation", "functions", "function_call", "audio",
    "modalities", "prediction", "stop", "web_search_options",
)


def _invalid(param: str) -> None:
    code = {
        "request": "invalid_json", "model": "invalid_model", "input": "invalid_input",
        "stream": "invalid_stream", "tools": "invalid_tools",
    }.get(param, "invalid_request_error")
    raise AdapterError(code, "The request has an invalid field shape.", param)


def _unsupported(param: str) -> None:
    raise AdapterError(
        "unsupported_capability", "The requested capability is not supported.", param
    )


def _string(value: Any, param: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _invalid(param)


def _object(payload: Any, *, default_stream: bool) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, dict) or any(not isinstance(k, str) for k in payload):
        _invalid("request")
    result = deepcopy(payload)
    _string(result.get("model"), "model", nonempty=True)
    stream = result.get("stream", default_stream)
    if not isinstance(stream, bool):
        _invalid("stream")
    if "store" in result and not isinstance(result["store"], bool):
        _invalid("store")
    for field in _SEMANTIC:
        if field in result:
            _unsupported(field)
    for field in ("parallel_tool_calls",):
        if field in result and not isinstance(result[field], bool):
            _invalid(field)
    if "reasoning" in result and not isinstance(result["reasoning"], dict):
        _invalid("reasoning")
    return result, stream


def _compat(result: dict[str, Any], strict: bool) -> tuple[str, ...]:
    ignored = []
    for field in _INERT:
        if field in result:
            if strict:
                _unsupported(field)
            ignored.append(field)
            del result[field]
    if result.get("store") is True:
        if strict:
            _unsupported("store")
        ignored.append("store")
    result["store"] = False
    result["stream"] = True
    return tuple(ignored)


def _usage(result: dict[str, Any]) -> bool:
    if "stream_options" not in result:
        return False
    options = result["stream_options"]
    if not isinstance(options, dict):
        _invalid("stream_options")
    if any(key != "include_usage" for key in options):
        _unsupported("stream_options")
    if "include_usage" in options and not isinstance(options["include_usage"], bool):
        _invalid("stream_options")
    return options.get("include_usage", False)


def _format(value: Any, param: str) -> None:
    if not isinstance(value, dict):
        _invalid(param)
    kind = value.get("type")
    if kind in ("text", "json_object"):
        if set(value) != {"type"}:
            _unsupported(param)
    elif kind == "json_schema":
        _string(value.get("name"), param, nonempty=True)
        if not isinstance(value.get("schema"), dict):
            _invalid(param)
        if "strict" in value and not isinstance(value["strict"], bool):
            _invalid(param)
        if "description" in value:
            _string(value["description"], param)
        if set(value) - {"type", "name", "schema", "strict", "description"}:
            _unsupported(param)
    else:
        _unsupported(param)


def _tool(tool: Any, *, chat: bool) -> dict[str, Any]:
    if not isinstance(tool, dict):
        _invalid("tools")
    kind = tool.get("type")
    if kind != "function" and (chat or kind != "custom"):
        _unsupported("tools")
    if chat:
        if set(tool) - {"type", "function"}:
            _unsupported("tools")
        function = tool.get("function")
        if not isinstance(function, dict):
            _invalid("tools")
        if "type" in function:
            _unsupported("tools")
        result = {"type": "function", **function}
    else:
        result = tool
    _string(result.get("name"), "tools", nonempty=True)
    if "description" in result:
        _string(result["description"], "tools")
    if "parameters" in result and not isinstance(result["parameters"], dict):
        _invalid("tools")
    if "strict" in result and result["strict"] is None:
        del result["strict"]
    if "strict" in result and not isinstance(result["strict"], bool):
        _invalid("tools")
    if chat and set(result) - {"type", "name", "description", "parameters", "strict"}:
        _unsupported("tools")
    if kind == "custom" and "format" in result:
        if not isinstance(result["format"], dict):
            _invalid("tools")
    return result


def _tools(result: dict[str, Any], *, chat: bool) -> None:
    if "tools" in result:
        if not isinstance(result["tools"], list):
            _invalid("tools")
        result["tools"] = [_tool(tool, chat=chat) for tool in result["tools"]]
    if "tool_choice" not in result:
        return
    choice = result["tool_choice"]
    if isinstance(choice, str):
        if choice not in ("auto", "none", "required"):
            _invalid("tool_choice")
        return
    if not isinstance(choice, dict):
        _invalid("tool_choice")
    if chat:
        if choice.get("type") != "function":
            _unsupported("tool_choice")
        function = choice.get("function")
        if not isinstance(function, dict) or set(function) != {"name"}:
            _invalid("tool_choice")
        if set(choice) != {"type", "function"}:
            _unsupported("tool_choice")
        _string(function.get("name"), "tool_choice", nonempty=True)
        result["tool_choice"] = {"type": "function", "name": function["name"]}
    else:
        if choice.get("type") not in ("function", "custom"):
            _unsupported("tool_choice")
        _string(choice.get("name"), "tool_choice", nonempty=True)


def _responses_content(content: Any, *, assistant: bool) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        _invalid("input")
    for part in content:
        if not isinstance(part, dict):
            _invalid("input")
        kind = part.get("type")
        if kind in ("input_text", "output_text"):
            _string(part.get("text"), "input")
            if kind == "output_text" and not assistant:
                _invalid("input")
        elif kind == "input_image":
            if not any(key in part for key in ("image_url", "file_id")):
                _invalid("input")
            for key in ("image_url", "file_id"):
                if key in part:
                    _string(part[key], "input", nonempty=True)
            if "detail" in part and part["detail"] not in ("auto", "low", "high"):
                _invalid("input")
        elif kind == "input_file":
            if not any(key in part for key in ("file_id", "file_data", "file_url")):
                _invalid("input")
            for key in ("file_id", "file_data", "file_url", "filename"):
                if key in part:
                    _string(part[key], "input", nonempty=True)
        else:
            _unsupported("input")


def _responses_input(value: Any) -> Any:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list):
        _invalid("input")
    for item in value:
        if not isinstance(item, dict):
            _invalid("input")
        if "id" in item:
            _string(item["id"], "input", nonempty=True)
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role")
            if role not in ("system", "developer", "user", "assistant"):
                _unsupported("input")
            if "content" not in item:
                _invalid("input")
            _responses_content(item["content"], assistant=role == "assistant")
            if role == "system":
                item["role"] = "developer"
        elif kind in ("function_call", "custom_tool_call"):
            _string(item.get("call_id"), "input", nonempty=True)
            _string(item.get("name"), "input", nonempty=True)
            _string(item.get("arguments" if kind == "function_call" else "input"), "input")
        elif kind in ("function_call_output", "custom_tool_call_output"):
            _string(item.get("call_id"), "input", nonempty=True)
            _responses_content(item.get("output"), assistant=False)
        elif kind == "reasoning":
            if "summary" in item:
                if not isinstance(item["summary"], list):
                    _invalid("input")
                for summary in item["summary"]:
                    if not isinstance(summary, dict) or summary.get("type") != "summary_text":
                        _invalid("input")
                    _string(summary.get("text"), "input")
            if "encrypted_content" in item:
                if item["encrypted_content"] is None:
                    del item["encrypted_content"]
                else:
                    _string(item["encrypted_content"], "input")
        else:
            _unsupported("input")
    return value


def normalize_responses(payload: Any, *, strict: bool = False) -> AdaptedRequest:
    """Normalize Responses without mutating input; unknown top-level fields survive."""
    result, stream = _object(payload, default_stream=True)
    if "input" not in result:
        _invalid("input")
    result["input"] = _responses_input(result["input"])
    _tools(result, chat=False)
    if "text" in result:
        text = result["text"]
        if not isinstance(text, dict):
            _invalid("text")
        if "format" in text:
            _format(text["format"], "text")
    include_usage = _usage(result)
    ignored = _compat(result, strict)
    return AdaptedRequest(result, stream, include_usage, ignored)


def _chat_content(value: Any, *, role: str) -> list[dict[str, Any]]:
    text_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(value, str):
        return [{"type": text_type, "text": value}]
    if not isinstance(value, list):
        _invalid("messages")
    result = []
    for part in value:
        if not isinstance(part, dict):
            _invalid("messages")
        kind = part.get("type")
        if kind == "text":
            if set(part) != {"type", "text"}:
                _unsupported("messages")
            _string(part.get("text"), "messages")
            result.append({"type": text_type, "text": part["text"]})
        elif kind == "image_url":
            if role != "user":
                _unsupported("messages")
            image = part.get("image_url")
            if not isinstance(image, dict):
                _invalid("messages")
            if set(part) != {"type", "image_url"} or set(image) - {"url", "detail"}:
                _unsupported("messages")
            _string(image.get("url"), "messages", nonempty=True)
            converted = {"type": "input_image", "image_url": image["url"]}
            if "detail" in image:
                if image["detail"] not in ("auto", "low", "high"):
                    _invalid("messages")
                converted["detail"] = image["detail"]
            result.append(converted)
        else:
            _unsupported("messages")
    return result


def _chat_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        _invalid("messages")
    result = []
    for message in messages:
        if not isinstance(message, dict):
            _invalid("messages")
        role = message.get("role")
        if role not in ("system", "developer", "user", "assistant", "tool"):
            _unsupported("messages")
        allowed = {"role", "content", "tool_calls", "tool_call_id"}
        if role == "assistant":
            allowed.update({"refusal", "audio", "function_call", "annotations"})
            for optional in ("refusal", "audio", "function_call", "annotations"):
                if message.get(optional) is not None:
                    _unsupported("messages")
        if role == "tool":
            allowed.add("name")
            if "name" in message:
                _string(message["name"], "messages", nonempty=True)
        if set(message) - allowed:
            _unsupported("messages")
        if role == "tool":
            if "tool_calls" in message:
                _invalid("messages")
            _string(message.get("tool_call_id"), "messages", nonempty=True)
            _string(message.get("content"), "messages")
            result.append({
                "type": "function_call_output", "call_id": message["tool_call_id"],
                "output": message["content"],
            })
            continue
        if "tool_call_id" in message or (role != "assistant" and "tool_calls" in message):
            _invalid("messages")
        content = message.get("content")
        calls = message.get("tool_calls", [])
        if calls is None and role == "assistant":
            calls = []
        if not isinstance(calls, list):
            _invalid("messages")
        if content is not None:
            result.append({
                "role": "developer" if role == "system" else role,
                "content": _chat_content(content, role=role),
            })
        elif role != "assistant" or not calls:
            _invalid("messages")
        for call in calls:
            if not isinstance(call, dict):
                _invalid("messages")
            if call.get("type") != "function":
                _unsupported("messages")
            if set(call) != {"id", "type", "function"}:
                _invalid("messages")
            function = call.get("function")
            if not isinstance(function, dict) or set(function) != {"name", "arguments"}:
                _invalid("messages")
            _string(call.get("id"), "messages", nonempty=True)
            _string(function.get("name"), "messages", nonempty=True)
            _string(function.get("arguments"), "messages")
            result.append({
                "type": "function_call", "call_id": call["id"],
                "name": function["name"], "arguments": function["arguments"],
            })
    return result


def normalize_chat(payload: Any, *, strict: bool = False) -> AdaptedRequest:
    """Convert a documented Chat subset to explicit stateless Responses history."""
    result, stream = _object(payload, default_stream=False)
    allowed = {
        "model", "messages", "stream", "store", "tools", "tool_choice", "response_format",
        "parallel_tool_calls", "reasoning", "reasoning_effort", *_INERT,
    }
    if set(result) - allowed:
        _unsupported("request")
    if "reasoning_effort" in result:
        effort = result.pop("reasoning_effort")
        _string(effort, "reasoning_effort", nonempty=True)
        reasoning = result.setdefault("reasoning", {})
        if "effort" in reasoning and reasoning["effort"] != effort:
            _invalid("reasoning_effort")
        reasoning["effort"] = effort
    if "n" in result:
        n = result["n"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            _invalid("n")
        if n != 1:
            _unsupported("n")
    result["input"] = _chat_messages(result.pop("messages", None))
    _tools(result, chat=True)
    if "response_format" in result:
        response_format = result.pop("response_format")
        if not isinstance(response_format, dict):
            _invalid("response_format")
        if response_format.get("type") == "json_schema":
            if set(response_format) != {"type", "json_schema"}:
                _invalid("response_format")
            schema = response_format.get("json_schema")
            if not isinstance(schema, dict):
                _invalid("response_format")
            if "type" in schema:
                _invalid("response_format")
            response_format = {"type": "json_schema", **schema}
        _format(response_format, "response_format")
        result["text"] = {"format": response_format}
    include_usage = _usage(result)
    ignored = _compat(result, strict)
    return AdaptedRequest(result, stream, include_usage, ignored)
