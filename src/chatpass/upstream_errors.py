"""Allowlisted public SIWC diagnostics; never forward remote message/detail text."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

SIWC_STATUS = {
    "server_is_overloaded": 503,
    "subscription_sharing_user_not_eligible": 403,
    "subscription_sharing_usage_limit_exceeded": 429,
    "subscription_sharing_usage_unavailable": 503,
    "subscription_sharing_unsupported_capability": 400,
    "subscription_sharing_route_not_supported": 403,
    "subscription_sharing_invalid_user": 401,
    "subscription_sharing_user_unavailable": 503,
    "chatpass_v2_scope_not_authorized": 403,
    "chatpass_v2_invalid_authorization_context": 403,
}
MAX_ERROR_BYTES = 16384
ERROR_READ_SECONDS = 0.25
PARAMS = frozenset({"model", "input", "tools", "tool_choice", "stream", "store",
                    "previous_response_id", "reasoning", "text", "max_output_tokens",
                    "temperature", "top_p", "scope", "resource"})
_REQUEST_ID = re.compile(r"req_[A-Za-z0-9]{24,64}")


def request_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


async def read_diagnostics(response: httpx.Response) -> dict:
    """Optional bounded metadata read; failure must not replace the HTTP status.

    Caller owns the response close and request deadline. Never decompress errors,
    retry a request, log a body, or wait indefinitely for diagnostic metadata.
    """
    identifier = request_id(response.headers.get("x-request-id"))
    fallback = {"request_id": identifier} if identifier else {}
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        return fallback
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/problem+json"}:
        return fallback
    try:
        async with asyncio.timeout(ERROR_READ_SECONDS):
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_ERROR_BYTES:
                    return fallback
                raw.extend(chunk)
            payload = json.loads(raw.decode("utf-8"))
            return diagnostics(payload, header_request_id=identifier)
    except (TimeoutError, httpx.HTTPError, httpx.StreamError, ValueError, UnicodeError, RecursionError):
        return fallback


def diagnostics(payload: Any, *, header_request_id: Any = None) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    nested = payload.get("response")
    envelope = nested if isinstance(nested, dict) else payload
    error = envelope.get("error")
    if not isinstance(error, dict):
        error = envelope if envelope.get("type") == "error" else {}
    code = error.get("code")
    result = {}
    if isinstance(code, str) and code in SIWC_STATUS:
        result["code"] = code
        param = error.get("param")
        if isinstance(param, str) and param in PARAMS:
            result["param"] = param
    identifier = request_id(header_request_id) or request_id(payload.get("request_id"))
    if identifier:
        result["request_id"] = identifier
    return result
