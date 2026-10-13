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


def test_future_login_identity_does_not_become_usable_credentials(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())

    class FutureIdentity(FakeOAuth):
        def validate_tokens(self, body, client_id, **kwargs):
            raise OAuthError("id_token_not_yet_valid")

    credentials = Credentials(store, FutureIdentity())
    with pytest.raises(OAuthError, match="id_token_not_yet_valid"):
        credentials.accept_login(
            {"access_token": "unverified-access", "refresh_token": "unverified-refresh"},
            "issued", "nonce",
        )
    state = store.read()
    assert state["login_required"] is True
    assert "credentials" not in state
    assert "pending_tokens" not in state
    assert "unverified-access" not in str(state)
    assert "unverified-refresh" not in str(state)


@pytest.mark.parametrize("code", ["account_mismatch", "invalid_id_token", "insufficient_scope"])
@pytest.mark.parametrize("blocked", [False, True])
def test_failed_replacement_preserves_existing_account_state(tmp_path, code, blocked):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["expires_at"] = time.time() + 3600
        state["gateway_key_hash"] = "preserved-key"
        if blocked:
            state["refresh_in_progress"] = True
            state["login_required"] = True
    original = store.read()

    class InvalidCandidate(FakeOAuth):
        def validate_tokens(self, body, client_id, **kwargs):
            assert kwargs["subject"] == "person"
            raise OAuthError(code)

    credentials = Credentials(store, InvalidCandidate())
    with pytest.raises(OAuthError, match=code):
        credentials.accept_login(
            {"access_token": "candidate", "refresh_token": "candidate-refresh"},
            "issued", "nonce",
        )
    assert store.read() == original


@pytest.mark.parametrize("kind", ["login", "refresh", None])
def test_new_login_cannot_overwrite_pending_validation(tmp_path, kind):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["pending_tokens"] = {
            "body": {"access_token": "successor", "refresh_token": "successor-refresh"},
            "client_id": "issued", "received_at": time.time(), "subject": "person",
        }
        if kind is not None:
            state["pending_tokens"]["kind"] = kind
        state["refresh_in_progress"] = True
    original = store.read()
    credentials = Credentials(store, FakeOAuth())
    with pytest.raises(OAuthError, match="pending_token_validation"):
        credentials.accept_login({"access_token": "candidate"}, "issued", "nonce")
    assert store.read() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [3600, 15])
async def test_unverified_replacement_keeps_valid_old_access_usable(tmp_path, remaining):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["expires_at"] = time.time() + remaining
    oauth = FakeOAuth()
    oauth.fail_validation = True
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        credentials.accept_login(
            {"access_token": "candidate", "refresh_token": "candidate-refresh"},
            "issued", "nonce",
        )
    assert await credentials.get_access_token() == "old"
    assert store.read()["pending_tokens"]["body"]["refresh_token"] == "candidate-refresh"
    assert oauth.calls == 0
    oauth.fail_validation = False
    assert await credentials.get_access_token() == "candidate"
    assert "pending_tokens" not in store.read()
    assert oauth.calls == 0


@pytest.mark.asyncio
async def test_pending_login_never_uses_expired_old_access(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["expires_at"] = time.time() - 1
    oauth = FakeOAuth()
    oauth.fail_validation = True
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        credentials.accept_login({"access_token": "candidate"}, "issued", "nonce")
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        await credentials.get_access_token()
    assert oauth.calls == 0
    assert "pending_tokens" in store.read()


@pytest.mark.asyncio
async def test_invalid_legacy_login_candidate_does_not_disable_saved_account(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["expires_at"] = time.time() + 3600
        state["pending_tokens"] = {
            "body": {"access_token": "candidate"}, "client_id": "issued",
            "nonce": "legacy-nonce", "received_at": time.time(),
        }

    class InvalidCandidate(FakeOAuth):
        def validate_tokens(self, body, client_id, **kwargs):
            raise OAuthError("account_mismatch")

    credentials = Credentials(store, InvalidCandidate())
    assert await credentials.get_access_token() == "old"
    assert "pending_tokens" not in store.read()
    assert "login_required" not in store.read()


@pytest.mark.asyncio
async def test_initial_pending_login_recovers_without_refresh(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    oauth = FakeOAuth()
    oauth.fail_validation = True
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        credentials.accept_login(
            {"access_token": "candidate", "refresh_token": "candidate-refresh"},
            "issued", "nonce",
        )
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        await credentials.get_access_token()
    oauth.fail_validation = False
    assert await credentials.get_access_token() == "candidate"
    assert oauth.calls == 0


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
