"""Pure Responses-to-Chat bridge for gateway-validated SSE (never raw upstream).

Supported output is assistant messages containing output_text and function_call
items. Reasoning items are nonvisible metadata: Chat cannot roundtrip opaque
reasoning; use native Responses for that. Refusals, custom tools and other
modalities fail closed. Only response.completed proves success; completed item
snapshots can restore an empty terminal output, never partial deltas or EOF.
Gateway error frames are already sanitized: preserve safe error metadata only;
timeout phases must belong to the fixed gateway phase enumeration.
All bridge-generated error messages are static and never include upstream data.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from typing import Any

_MAX_BYTES = 64 * 1048576


class BridgeError(Exception):
    """Safe public error; code/message may be copied into a gateway error."""

    def __init__(self, code: str = "invalid_upstream_response",
                 message: str = "Upstream returned an invalid response.", *,
                 param: str | None = None, request_id: str | None = None,
                 timeout_phase: str | None = None) -> None:
        self.code = code
        self.message = message
        self.param = param
        self.request_id = request_id
        self.timeout_phase = (timeout_phase if code == "upstream_timeout"
                              and isinstance(timeout_phase, str) and timeout_phase in (
                                  "request", "preparation", "connect", "pool", "write",
                                  "response_headers", "stream_idle", "stream_total",
                              ) else None)
        super().__init__(message)


def _unsupported() -> BridgeError:
    return BridgeError("unsupported_response", "Upstream response is not supported by this bridge.")


def _interrupted() -> BridgeError:
    return BridgeError("stream_interrupted", "Upstream stream did not complete successfully.")


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def parse_event(frame: bytes) -> dict[str, Any] | None:
    """Parse one complete SSE frame; comments and [DONE] are not completion."""
    try:
        if len(frame) > _MAX_BYTES:
            raise BridgeError()
        lines = frame.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        data = []
        for line in lines:
            field, separator, value = line.partition(":")
            if field == "data":
                data.append(value.removeprefix(" ") if separator else "")
        if not data:
            return None
        payload = "\n".join(data)
        if payload == "[DONE]":
            return None
        event = json.loads(payload, parse_constant=_constant, parse_float=_float)
        if not isinstance(event, dict):
            raise BridgeError()
        if "type" in event and not isinstance(event["type"], str):
            raise BridgeError()
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BridgeError() from exc
    if "error" in event:
        error = event["error"]
        if (isinstance(error, dict) and isinstance(error.get("code"), str)
                and isinstance(error.get("message"), str)):
            raise BridgeError(error["code"], error["message"], param=error.get("param"),
                              request_id=error.get("request_id"),
                              timeout_phase=error.get("timeout_phase"))
        raise BridgeError()
    return event


def _identity(response: dict[str, Any]) -> tuple[str, int]:
    identifier = response.get("id")
    created = response.get("created_at", 0)
    if (not isinstance(identifier, str) or not identifier.strip()
            or not isinstance(created, (int, float)) or isinstance(created, bool)
            or (isinstance(created, float) and not math.isfinite(created)) or created < 0):
        raise BridgeError()
    return identifier, int(created)


def _completed(event: dict[str, Any]) -> dict[str, Any]:
    response = event.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("output"), list):
        raise BridgeError()
    _identity(response)
    if response.get("status", "completed") != "completed":
        raise BridgeError()
    return response


def _unique_output(output: list[Any]) -> list[Any]:
    """Reject ambiguous output identities before callers can execute tools."""
    identifiers: set[str] = set()
    calls: set[str] = set()
    for item in output:
        if not isinstance(item, dict):
            raise BridgeError()
        identifier = item.get("id")
        if identifier is not None:
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise BridgeError()
            identifiers.add(identifier)
        if not isinstance(item.get("type"), str):
            raise BridgeError()
        if item["type"] in {"function_call", "custom_tool_call"}:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in calls:
                raise BridgeError()
            calls.add(call_id)
            name = item.get("name")
            value = item.get("arguments" if item["type"] == "function_call" else "input")
            if not isinstance(name, str) or not name or not isinstance(value, str):
                raise BridgeError()
    return output


class _CompletedItems:
    """Reconcile completed item snapshots; deltas never establish completion."""

    def __init__(self) -> None:
        self.identity: tuple[str, int] | None = None
        self.items: dict[int, dict[str, Any]] = {}
        self.added: dict[int, dict[str, Any]] = {}

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind in {"response.created", "response.in_progress"}:
            response = event.get("response")
            if not isinstance(response, dict):
                raise BridgeError()
            identity = _identity(response)
            if self.identity is not None and self.identity != identity:
                raise BridgeError()
            self.identity = identity
        elif kind == "response.output_item.added":
            index = _index(event, "output_index")
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise BridgeError()
            if index in self.added or index in self.items:
                raise BridgeError()
            self.added[index] = item
        elif kind == "response.output_item.done":
            index = _index(event, "output_index")
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise BridgeError()
            if item.get("status") not in (None, "completed"):
                raise BridgeError()
            added = self.added.get(index, {})
            if any(key in added and added[key] != item.get(key)
                   for key in ("id", "type", "call_id", "name", "namespace")):
                raise BridgeError()
            if index in self.items and self.items[index] != item:
                raise BridgeError()
            self.items[index] = item

    def complete(self, event: dict[str, Any]) -> dict[str, Any]:
        response = _completed(event)
        if self.identity is not None and self.identity != _identity(response):
            raise BridgeError()
        output = response["output"]
        if not output and self.added.keys() - self.items.keys():
            raise BridgeError()
        for index, item in self.added.items():
            if output:
                if index >= len(output) or not isinstance(output[index], dict):
                    raise BridgeError()
                if any(key in item and item[key] != output[index].get(key)
                       for key in ("id", "type", "call_id", "name", "namespace")):
                    raise BridgeError()
        if not output and self.items:
            if sorted(self.items) != list(range(len(self.items))):
                raise BridgeError()
            return {**response, "output": _unique_output(
                [self.items[i] for i in range(len(self.items))])}
        if self.items:
            output = list(output)
        for index, item in self.items.items():
            if index >= len(output) or not isinstance(output[index], dict):
                raise BridgeError()
            final = output[index]
            if any(key in final and final[key] != value for key, value in item.items()):
                raise BridgeError()
            output[index] = {**item, **final}
        return {**response, "output": _unique_output(output)}


async def collect_response(frames: AsyncIterator[bytes]) -> dict[str, Any]:
    """Collect completed snapshots, including items omitted by the terminal output."""
    completed_items = _CompletedItems()
    total = 0
    async for frame in frames:
        total += len(frame)
        if total > _MAX_BYTES:
            raise BridgeError()
        event = parse_event(frame)
        if event is None:
            continue
        completed_items.observe(event)
        if event.get("type") == "response.completed":
            return completed_items.complete(event)
        if event.get("type") in {"response.failed", "response.incomplete", "error"}:
            raise _interrupted()
    raise _interrupted()


def _outputs(response: dict[str, Any]) -> tuple[list[tuple[int, int, str]],
                                               list[tuple[int, dict[str, Any]]]]:
    output = response.get("output")
    if not isinstance(output, list):
        raise BridgeError()
    texts = []
    tools = []
    for index, item in enumerate(output):
        if not isinstance(item, dict):
            raise BridgeError()
        if item.get("type") == "message":
            if item.get("role", "assistant") != "assistant":
                raise _unsupported()
            content = item.get("content")
            if not isinstance(content, list):
                raise BridgeError()
            for part_index, part in enumerate(content):
                if not isinstance(part, dict):
                    raise BridgeError()
                if part.get("type") != "output_text":
                    raise _unsupported()
                if not isinstance(part.get("text"), str):
                    raise BridgeError()
                texts.append((index, part_index, part["text"]))
        elif item.get("type") == "reasoning":
            continue
        elif item.get("type") == "function_call":
            if item.get("namespace") is not None:
                raise _unsupported()
            if any(not isinstance(item.get(key), str) for key in ("call_id", "name", "arguments")):
                raise BridgeError()
            if not item["call_id"] or not item["name"]:
                raise BridgeError()
            tools.append((index, item))
        else:
            raise _unsupported()
    _unique_output(output)
    return texts, tools


def _usage(response: dict[str, Any]) -> dict[str, Any] | None:
    usage = response.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise BridgeError()
    result = {}
    for source, target in (("input_tokens", "prompt_tokens"),
                           ("output_tokens", "completion_tokens"),
                           ("total_tokens", "total_tokens")):
        value = usage.get(source)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BridgeError()
        result[target] = value
    for source, target, field in (
        ("input_tokens_details", "prompt_tokens_details", "cached_tokens"),
        ("output_tokens_details", "completion_tokens_details", "reasoning_tokens"),
    ):
        details = usage.get(source)
        if details is not None:
            if not isinstance(details, dict):
                raise BridgeError()
            if field in details:
                value = details[field]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise BridgeError()
                result[target] = {field: value}
    return result


def chat_completion(response: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Translate the documented text/function-call subset, rejecting data loss."""
    identifier, created = _identity(response)
    if response.get("status", "completed") != "completed":
        raise BridgeError()
    texts, tools = _outputs(response)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(t[2] for t in texts)}
    if tools:
        message["tool_calls"] = [
            {"id": item["call_id"], "type": "function",
             "function": {"name": item["name"], "arguments": item["arguments"]}}
            for _, item in tools
        ]
        if not texts:
            message["content"] = None
    result = {"id": identifier, "object": "chat.completion", "created": created, "model": model,
              "choices": [{"index": 0, "message": message,
                           "finish_reason": "tool_calls" if tools else "stop"}]}
    usage = _usage(response)
    if usage is not None:
        result["usage"] = usage
    return result


def _sse(value: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b"\n\n"


def _index(event: dict[str, Any], key: str) -> int:
    value = event.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BridgeError()
    return value


async def chat_stream(frames: AsyncIterator[bytes], *, model: str,
                      include_usage: bool = False) -> AsyncIterator[bytes]:
    """Stream Chat chunks, reconcile terminal snapshots, and fail without DONE.

    Function deltas before their item addition are rejected. Output indices map
    to contiguous tool indices; the terminal snapshot must agree with all bytes
    already emitted. Memory and input are independently bounded to 64 MiB.
    """
    completed_items = _CompletedItems()
    identity: tuple[str, int] | None = None
    role_sent = False
    texts: dict[tuple[int, int], str] = {}
    emitted_text = ""
    tools: dict[int, dict[str, Any]] = {}
    call_ids: set[str] = set()
    total = 0
    buffered = 0

    def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
        if identity is None:
            raise BridgeError()
        return _sse({"id": identity[0], "object": "chat.completion.chunk", "created": identity[1],
                     "model": model, "choices": [{"index": 0, "delta": delta,
                                                   "finish_reason": finish}]})

    def remember(value: str) -> None:
        nonlocal buffered
        buffered += len(value.encode("utf-8"))
        if buffered > _MAX_BYTES:
            raise BridgeError()

    async for frame in frames:
        total += len(frame)
        if total > _MAX_BYTES:
            raise BridgeError()
        event = parse_event(frame)
        if event is None:
            continue
        completed_items.observe(event)
        kind = event.get("type")
        if kind in {"response.failed", "response.incomplete", "error"}:
            raise _interrupted()
        if kind == "response.created":
            response = event.get("response")
            if not isinstance(response, dict):
                raise BridgeError()
            new_identity = _identity(response)
            if identity is not None and identity != new_identity:
                raise BridgeError()
            identity = new_identity
            if not role_sent:
                yield chunk({"role": "assistant"})
                role_sent = True
        elif kind == "response.output_text.delta":
            if identity is None:
                raise BridgeError()
            key = (_index(event, "output_index"), _index(event, "content_index"))
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise BridgeError()
            # Both the per-part snapshot and globally emitted order retain text.
            remember(delta)
            remember(delta)
            texts[key] = texts.get(key, "") + delta
            emitted_text += delta
            yield chunk({"content": delta})
        elif kind == "response.output_item.added":
            index = _index(event, "output_index")
            item = event.get("item")
            if not isinstance(item, dict):
                raise BridgeError()
            if item.get("type") == "function_call":
                if item.get("namespace") is not None:
                    raise _unsupported()
                if identity is None or index in tools:
                    raise BridgeError()
                if any(not isinstance(item.get(k), str) or not item[k] for k in ("call_id", "name")):
                    raise BridgeError()
                if item["call_id"] in call_ids:
                    raise BridgeError()
                call_ids.add(item["call_id"])
                arguments = item.get("arguments", "")
                if not isinstance(arguments, str):
                    raise BridgeError()
                remember(arguments)
                tools[index] = {"index": len(tools), "id": item["call_id"],
                                "name": item["name"], "arguments": arguments}
                yield chunk({"tool_calls": [{"index": tools[index]["index"],
                                             "id": item["call_id"], "type": "function",
                                             "function": {"name": item["name"],
                                                          "arguments": arguments}}]})
            elif item.get("type") not in {"message", "reasoning"}:
                raise _unsupported()
        elif kind == "response.function_call_arguments.delta":
            index = _index(event, "output_index")
            delta = event.get("delta")
            if index not in tools or not isinstance(delta, str):
                raise BridgeError()
            remember(delta)
            tools[index]["arguments"] += delta
            yield chunk({"tool_calls": [{"index": tools[index]["index"],
                                         "function": {"arguments": delta}}]})
        elif kind == "response.completed":
            response = completed_items.complete(event)
            # Validate the entire snapshot before emitting missing content or success.
            completion = chat_completion(response, model=model)
            final_identity = _identity(response)
            if identity is not None and identity != final_identity:
                raise BridgeError()
            identity = final_identity
            final_texts, final_tools = _outputs(response)
            final_keys = {(i, c) for i, c, _ in final_texts}
            if set(texts) - final_keys or set(tools) - {i for i, _ in final_tools}:
                raise BridgeError()
            final_text = "".join(text for _, _, text in final_texts)
            if not final_text.startswith(emitted_text):
                # Per-part prefixes alone cannot prove concatenated Chat order.
                raise BridgeError()
            pending = []
            for i, c, text in final_texts:
                previous = texts.get((i, c), "")
                if not text.startswith(previous):
                    raise BridgeError()
                if text[len(previous):]:
                    pending.append({"content": text[len(previous):]})
            missing_text = "".join(delta["content"] for delta in pending)
            if emitted_text + missing_text != final_text:
                raise BridgeError()
            for tool_index, (index, item) in enumerate(final_tools):
                previous_tool = tools.get(index)
                if previous_tool is None:
                    # Existing streamed tools must precede terminal-only ones.
                    if tool_index < len(tools):
                        raise BridgeError()
                    pending.append({"tool_calls": [{"index": tool_index, "id": item["call_id"],
                                                     "type": "function", "function": {
                                                         "name": item["name"],
                                                         "arguments": item["arguments"]}}]})
                else:
                    if (previous_tool["index"] != tool_index
                            or previous_tool["id"] != item["call_id"]
                            or previous_tool["name"] != item["name"]
                            or not item["arguments"].startswith(previous_tool["arguments"])):
                        raise BridgeError()
                    suffix = item["arguments"][len(previous_tool["arguments"]):]
                    if suffix:
                        pending.append({"tool_calls": [{"index": tool_index,
                                                         "function": {"arguments": suffix}}]})
            if not role_sent:
                yield chunk({"role": "assistant"})
            for delta in pending:
                yield chunk(delta)
            yield chunk({}, completion["choices"][0]["finish_reason"])
            if include_usage and "usage" in completion:
                yield _sse({"id": identity[0], "object": "chat.completion.chunk",
                            "created": identity[1], "model": model, "choices": [],
                            "usage": completion["usage"]})
            yield b"data: [DONE]\n\n"
            return
    raise _interrupted()
