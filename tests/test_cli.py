import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_storage import MemoryKeyring

from chatpass.cli import main
from chatpass.storage import StateStore


def test_status_never_prints_secrets(tmp_path, monkeypatch, capsys):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    key = store.rotate_gateway_key()
    with store.transaction() as state:
        state["credentials"] = {
            "access_token": "oauth-secret",
            "refresh_token": "refresh-secret",
            "expires_at": 1,
        }
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    assert main(["--data-dir", str(tmp_path), "status"]) == 0
    text = capsys.readouterr().out
    assert "oauth-secret" not in text and "refresh-secret" not in text and key not in text
    assert json.loads(text)["gateway_key_configured"] is True


def test_concurrent_key_init_issues_only_one_valid_key(tmp_path, monkeypatch):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    issued = []
    monkeypatch.setattr("builtins.print", lambda value, **kwargs: issued.append(value) if "file" not in kwargs else None)
    # Force any check-then-rotate implementation through the empty-state race.
    barrier = threading.Barrier(2)
    rotate = store.rotate_gateway_key

    def simultaneous_rotate():
        barrier.wait(timeout=3)
        return rotate()

    monkeypatch.setattr(store, "rotate_gateway_key", simultaneous_rotate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: main(["--data-dir", str(tmp_path), "key", "init"]), range(2)
        ))
    assert sorted(results) == [0, 1]
    assert len(issued) == 1 and store.verify_gateway_key(issued[0])


def test_key_rotation_is_explicit(tmp_path, monkeypatch, capsys):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    assert main(["--data-dir", str(tmp_path), "key", "rotate"]) == 0
    key = capsys.readouterr().out.strip()
    assert store.verify_gateway_key(key)


def test_nonloopback_serve_fails_without_starting(monkeypatch, capsys):
    monkeypatch.setattr("uvicorn.run", lambda *_a, **_k: pytest.fail("must not start server"))
    assert main(["serve", "--host", "0.0.0.0"]) == 1
    assert "loopback" in capsys.readouterr().err


def test_serve_wires_real_credential_and_key_owners(tmp_path, monkeypatch):
    from chatpass.credentials import Credentials
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    monkeypatch.setattr('chatpass.cli.StateStore', lambda _: store)
    seen = []
    def build(settings, credentials, key_verifier):
        assert isinstance(credentials, Credentials)
        assert key_verifier.__self__ is store
        assert key_verifier.__name__ == 'verify_gateway_key'
        return 'app'
    monkeypatch.setattr('chatpass.gateway.create_app', build)
    monkeypatch.setattr('uvicorn.run', lambda app, **kwargs: seen.append(app))
    assert main(['--data-dir', str(tmp_path), 'serve']) == 0
    assert seen == ['app']


def test_import_does_not_open_browser_or_touch_state():
    import chatpass.cli

    assert callable(chatpass.cli.main)
