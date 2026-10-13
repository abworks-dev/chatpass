"""Error metadata allowlisting is independent from remote text and logging."""
import json

import httpx
import pytest
from test_gateway import TrackedStream

from chatpass.upstream_errors import (
    MAX_ERROR_BYTES,
    SIWC_STATUS,
    diagnostics,
    read_diagnostics,
    request_id,
)


@pytest.mark.parametrize("code", list(SIWC_STATUS))
@pytest.mark.parametrize("nested", [False, True])
def test_known_error_code_and_param(code, nested):
    payload = {"error": {"code": code, "param": "model", "message": "Bearer secret",
                         "detail": "cp_gateway-secret", "type": "token-secret"}}
    if nested:
        payload = {"type": "response.failed", "response": payload}
    result = diagnostics(payload)
    assert result == {"code": code, "param": "model"}
    assert "secret" not in str(result)


@pytest.mark.parametrize("value", ["cp_gateway-secret", "Bearer secret", "token_secret",
    {"code": "secret"}, None, 500])
def test_unallowlisted_codes_and_params_not_echoed(value):
    result = diagnostics({"error": {"code": value, "param": value, "message": "secret"}})
    assert result == {}
    result = diagnostics({"error": {"code": "subscription_sharing_usage_unavailable",
                                    "param": value}})
    assert result == {"code": "subscription_sharing_usage_unavailable"}


@pytest.mark.parametrize("value", ["req_secret", "req_" + "a" * 65,
    "req_" + "a" * 24 + "\n", "req_cp_gateway-secret", "Bearer secret", 500, None])
def test_request_id_fail_closed(value):
    assert request_id(value) is None
    assert diagnostics({"request_id": value}, header_request_id=value) == {}


def test_request_id_known_format_header_precedence():
    first, second = "req_" + "a" * 24, "req_" + "b" * 32
    assert diagnostics({"request_id": first}, header_request_id=second) == {"request_id": second}
    assert diagnostics({"request_id": first}, header_request_id="Bearer secret") == {"request_id": first}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "oversized", "malformed", "compressed", "slow"])
async def test_bounded_optional_error_read(case, monkeypatch):
    import asyncio

    raw = json.dumps({"error": {"code": "subscription_sharing_usage_unavailable",
                               "message": "Bearer never-forward"}}).encode()
    if case == "oversized":
        raw = b"x" * (MAX_ERROR_BYTES + 1)
    elif case == "malformed":
        raw = b'{"error":'
    gate = asyncio.Event() if case == "slow" else None
    stream = TrackedStream([raw], gate=gate)
    headers = {"content-type": "application/json", "x-request-id": "req_" + "a" * 24}
    if case == "compressed":
        headers["content-encoding"] = "gzip"
    monkeypatch.setattr("chatpass.upstream_errors.ERROR_READ_SECONDS", 0.01)
    response = httpx.Response(503, headers=headers, stream=stream)
    try:
        result = await read_diagnostics(response)
        assert result.get("code") == ("subscription_sharing_usage_unavailable" if case == "valid" else None)
        assert result["request_id"] == "req_" + "a" * 24
        assert "never-forward" not in str(result)
        if case == "compressed":
            assert not stream.started.is_set()
        assert response.status_code == 503
    finally:
        await response.aclose()
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["closed", "consumed", "transport", "non_json"])
async def test_diagnostic_read_failures_are_optional(case):
    stream = TrackedStream([b"{}"], error=httpx.ReadError("never-forward") if case == "transport" else None)
    response = httpx.Response(503, headers={"content-type": "text/plain" if case == "non_json" else "application/json",
                                          "x-request-id": "req_" + "b" * 24}, stream=stream)
    if case == "closed":
        await response.aclose()
    elif case == "consumed":
        async for _ in response.aiter_raw():
            pass
    try:
        assert await read_diagnostics(response) == {"request_id": "req_" + "b" * 24}
        if case == "non_json":
            assert not stream.started.is_set()
    finally:
        await response.aclose()


def test_direct_error_event_and_malformed_envelope():
    assert diagnostics({"type": "error", "code": "subscription_sharing_invalid_user"}) == {
        "code": "subscription_sharing_invalid_user"}
    for payload in (None, [], "secret", {"error": "secret"}, {"response": []}):
        assert diagnostics(payload) == {}
