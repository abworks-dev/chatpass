import asyncio
import time

import pytest
from test_storage import MemoryKeyring

from chatpass.credentials import Credentials
from chatpass.oauth import OAuthError
from chatpass.storage import StateStore, StorageError


def make_store(path):
    store = StateStore(path, backend=MemoryKeyring())
    with store.transaction() as state:
        state["registration"] = {"client_id": "issued"}
        state["credentials"] = {
            "access_token": "old",
            "refresh_token": "rotating-old",
            "client_id": "issued",
            "subject": "person",
            "scope": "scope",
            "expires_at": 0,
        }
    return store


class FakeOAuth:
    def __init__(self):
        self.calls = 0
        self.fail_validation = False
        self.fail_refresh = False

    def refresh(self, client_id, token):
        self.calls += 1
        assert token == "rotating-old"
        if self.fail_refresh:
            raise OAuthError("upstream_unavailable")
        return {"access_token": "new", "refresh_token": "rotating-new"}

    def validate_tokens(self, body, client_id, **kwargs):
        if self.fail_validation:
            raise OAuthError("identity_verification_unavailable")
        return {
            **body,
            "client_id": client_id,
            "subject": "person",
            "scope": "scope",
            "expires_at": time.time() + 3600,
        }


@pytest.mark.asyncio
async def test_concurrent_refresh_single_use(tmp_path):
    store = make_store(tmp_path)
    oauth = FakeOAuth()
    credentials = Credentials(store, oauth)
    result = await asyncio.gather(*(credentials.get_access_token() for _ in range(8)))
    assert result == ["new"] * 8
    assert oauth.calls == 1
    assert store.read()["credentials"]["refresh_token"] == "rotating-new"


@pytest.mark.asyncio
async def test_rotation_checkpoint_survives_validation_failure(tmp_path):
    store = make_store(tmp_path)
    oauth = FakeOAuth()
    oauth.fail_validation = True
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError):
        await credentials.get_access_token()
    assert store.read()["pending_tokens"]["body"]["refresh_token"] == "rotating-new"
    oauth.fail_validation = False
    assert await credentials.get_access_token() == "new"
    assert oauth.calls == 1
    assert "pending_tokens" not in store.read()


@pytest.mark.asyncio
async def test_ambiguous_refresh_never_replays_old_token(tmp_path):
    store = make_store(tmp_path)
    oauth = FakeOAuth()
    oauth.fail_refresh = True
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError):
        await credentials.get_access_token()
    with pytest.raises(OAuthError, match="login_required"):
        await credentials.get_access_token()
    assert oauth.calls == 1


def test_checkpoint_requires_active_transaction(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(StorageError):
        store.checkpoint({})
    with pytest.raises(ValueError):
        with store.transaction() as state:
            state["marker"] = True
            store.checkpoint(state)
            state["not_committed"] = True
            raise ValueError("failure after checkpoint")
    assert store.read()["marker"] is True
    assert "not_committed" not in store.read()
