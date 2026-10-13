import socket
import threading
import time
import urllib.request

import pytest
from test_storage import MemoryKeyring

from chatpass.login import CallbackServer, login
from chatpass.oauth import LoginAttempt, OAuthError
from chatpass.storage import StateStore


def test_login_rejects_pending_before_browser_or_registration(tmp_path, monkeypatch):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    with store.transaction() as state:
        state["pending_tokens"] = {"body": {"refresh_token": "saved-successor"}}
    original = store.read()

    def unexpected(*args, **kwargs):
        pytest.fail("Pending validation must be rejected before contacting the issuer")

    monkeypatch.setattr("chatpass.login.OAuthClient", unexpected)
    with pytest.raises(OAuthError, match="pending_token_validation"):
        login(store)
    assert store.read() == original


def test_callback_exact_host_path_state_and_single_use():
    with CallbackServer() as server:
        attempt = LoginAttempt.create(server.redirect_uri)
        server.configure(attempt, None)
        thread = threading.Thread(target=lambda: server.wait(3))
        thread.start()
        try:
            for path in ["/favicon.ico", "/auth/callback?state=bad&code=x&client_id=issued"]:
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(server.origin + path)
            response = urllib.request.urlopen(
                server.redirect_uri + "?state=" + attempt.state + "&code=x&client_id=issued"
            )
            assert response.status == 200
        finally:
            thread.join(4)
        assert server.result == ("x", "issued")


def test_slow_headers_cannot_extend_absolute_login_deadline():
    with CallbackServer() as server:
        server.configure(LoginAttempt.create(server.redirect_uri), None)
        finished = threading.Event()
        errors = []

        def wait():
            try:
                server.wait(0.2)
            except OAuthError as exc:
                errors.append(exc.code)
            finally:
                finished.set()

        with socket.create_connection(("127.0.0.1", server.http.server_port)) as attacker:
            attacker.sendall(b"G")
            thread = threading.Thread(target=wait)
            thread.start()
            deadline = time.monotonic() + 0.6
            try:
                while not finished.wait(0.03) and time.monotonic() < deadline:
                    try:
                        attacker.sendall(b"x")
                    except OSError:
                        break
                assert finished.wait(0.05), "Drip headers exceeded absolute callback deadline"
                assert errors == ["login_timeout"]
            finally:
                attacker.close()
                thread.join(2)
            assert not thread.is_alive()


def test_callback_timeout_is_bounded():
    with CallbackServer() as server:
        server.configure(LoginAttempt.create(server.redirect_uri), None)
        with pytest.raises(OAuthError, match="login_timeout"):
            server.wait(0.01)
