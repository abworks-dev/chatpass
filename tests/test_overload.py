"""Observed upstream overload: explicit recovery, no replay, no secret echo."""
import json

import httpx
import pytest
from test_gateway import TrackedStream, downstream, make_app, settings
from test_siwc_errors import call

CODE = "server_is_overloaded"
MESSAGE = "上游服务繁忙，请稍后手动重试。"


def frame(payload):
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("shape", ["flat", "error", "response"])
async def test_overload_survives_protocols_and_manual_recovery(
    caplog, monkeypatch, path, streaming, shape,
):
    monkeypatch.setenv("CHATPASS_DIAG_UNKNOWN_CODE_ONCE", "1")
    caplog.set_level("WARNING", logger="chatpass.gateway")
    detail = {"code": CODE, "message": "PRIVATE_UPSTREAM_MESSAGE",
              "param": "PRIVATE_TOOL_ARGUMENTS"}
    payload = ({"type": "error", **detail} if shape == "flat" else
               {"type": "error", "error": detail} if shape == "error" else
               {"type": "response.failed", "response": {"error": detail}})
    # A large prelude is legal and must not mask the later small error frame.
    prelude = frame({"type": "response.created", "response": {
        "id": "resp_test", "status": "in_progress", "output": []}, "padding": "." * 176000})
    failed = TrackedStream([prelude, frame(payload)])
    completed = frame({"type": "response.completed", "response": {
        "id": "resp_test", "status": "completed", "output": []}})
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=failed if len(calls) == 1 else TrackedStream([completed]))

    app, upstream = make_app(handler, config=settings(max_concurrency=1))
    async with upstream, downstream(app) as client:
        response = await call(client, path, stream=streaming)
        assert len(calls) == 1 and failed.closed  # No automatic inference replay.
        assert response.status_code == (200 if streaming else 503)
        if streaming:
            errors = [json.loads(line.removeprefix("data: "))["error"]
                      for line in response.text.splitlines()
                      if line.startswith("data: ") and '"error"' in line]
            assert len(errors) == 1
            error = errors[0]
        else:
            error = response.json()["error"]
        assert error["code"] == CODE and error["message"] == MESSAGE
        assert error["param"] is None
        assert "PRIVATE_" not in response.text and "PRIVATE_" not in caplog.text
        assert "[DONE]" not in response.text and "response.completed" not in response.text
        assert '"finish_reason": "stop"' not in response.text
        assert f"upstream_code={CODE}" in caplog.text
        assert "one-shot code diagnostic" not in caplog.text
        retry = await call(client, path, stream=streaming)
        assert retry.status_code == 200 and len(calls) == 2
        if streaming:
            assert ("[DONE]" if "chat" in path else "response.completed") in retry.text
        else:
            assert ("choices" if "chat" in path else "output") in retry.json()
