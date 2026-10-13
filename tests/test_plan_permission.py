"""Verified identity-only recovery never creates an inference/refresh grant."""
import time

import jwt
import pytest
from test_credentials import FakeOAuth, make_store
from test_oauth import ISSUER, issuer_client
from test_storage import MemoryKeyring

from chatpass.console import credential_status
from chatpass.credentials import Credentials
from chatpass.oauth import SCOPES, OAuthError
from chatpass.storage import StateStore


def signed_body(private, *, subject="person", scope="openid profile email", nonce="nonce"):
    now = int(time.time())
    identity = jwt.encode({"iss": ISSUER, "aud": "issued", "sub": subject,
        "iat": now, "exp": now + 3600, "nonce": nonce}, private,
        algorithm="RS256", headers={"kid": "test"})
    return {"scope": scope, "id_token": identity, "access_token": "never-store-access",
            "refresh_token": "never-store-refresh", "token_type": "Bearer", "expires_in": 3600}


def test_valid_identity_missing_plan_retained_without_tokens(tmp_path, monkeypatch):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    with store.transaction() as state:
        state["registration"] = {"client_id": "issued", "host_id": "urn:uuid:local"}
    oauth, private = issuer_client()
    monkeypatch.setattr(oauth, "refresh", lambda *_: pytest.fail("identity-only must never refresh"))
    credentials = Credentials(store, oauth)
    body = signed_body(private)
    with pytest.raises(OAuthError, match="scope_not_authorized") as error:
        credentials.accept_login(body, "issued", "nonce")
    assert body["id_token"] not in str(error.value)
    state = store.read()
    assert state["signed_in_identity"] == {"client_id": "issued", "subject": "person", "id_token": body["id_token"]}
    assert "never-store-access" not in str(state) and "never-store-refresh" not in str(state)
    assert "credentials" not in state and "pending_tokens" not in state and "login_required" not in state
    assert state["registration"]["subject"] == "person"
    assert body["id_token"].encode() not in store.path.read_bytes()
    assert credential_status(state)["account_signed_in"] is True
    assert credential_status(state)["plan_use_enabled"] is False
    with pytest.raises(OAuthError, match="scope_not_authorized"):
        credentials._get_access_token()
    credentials.accept_login(signed_body(private, scope=SCOPES), "issued", "nonce")
    assert "signed_in_identity" not in store.read()
    assert credential_status(store.read())["plan_use_enabled"] is True


@pytest.mark.parametrize("change", ["wrong_nonce", "different_person", "tampered"])
def test_invalid_identity_without_plan_never_retained(tmp_path, change):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    oauth, private = issuer_client()
    body = signed_body(private, nonce="wrong" if change == "wrong_nonce" else "nonce")
    if change == "tampered":
        parts = body["id_token"].split(".")
        parts[2] = "a" * len(parts[2])
        body["id_token"] = ".".join(parts)
    elif change == "different_person":
        with store.transaction() as state:
            state["registration"] = {"client_id": "issued", "subject": "other"}
    with pytest.raises(OAuthError):
        Credentials(store, oauth).accept_login(body, "issued", "nonce")
    assert "signed_in_identity" not in store.read() and "credentials" not in store.read()


def test_missing_plan_replacement_preserves_existing_active_grant(tmp_path):
    store = make_store(tmp_path)
    oauth, private = issuer_client()
    before = store.read()
    with pytest.raises(OAuthError, match="scope_not_authorized"):
        Credentials(store, oauth).accept_login(signed_body(private), "issued", "nonce")
    assert store.read() == before


def test_identity_only_failed_replacement_and_logout(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    oauth, private = issuer_client()
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError, match="scope_not_authorized"):
        credentials.accept_login(signed_body(private), "issued", "nonce")
    before = store.read()
    with pytest.raises(OAuthError, match="account_mismatch"):
        credentials.accept_login(signed_body(private, subject="other"), "issued", "nonce")
    assert store.read() == before
    result = credentials.logout()
    assert result["revocation_state"] == "unconfirmed"
    assert "signed_in_identity" not in store.read()


def test_earliest_refresh_wait_presentation_and_no_early_refresh(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["earliest_refresh_at"] = time.time() + 90
    oauth = FakeOAuth()
    with pytest.raises(OAuthError, match="refresh_not_yet_allowed"):
        Credentials(store, oauth)._get_access_token()
    assert oauth.calls == 0
    snapshot = credential_status(store.read())
    assert 89 <= snapshot["refresh_wait_seconds"] <= 90
    assert snapshot["account_signed_in"] and snapshot["plan_use_enabled"]
    assert "rotating-old" not in str(snapshot)
    assert credential_status(store.read(), now=time.time() + 100)["refresh_wait_seconds"] == 0


def test_pending_login_validation_bypasses_old_refresh_wait_without_refresh(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["earliest_refresh_at"] = time.time() + 3600
    oauth, private = issuer_client()
    original_verify = oauth.verify_identity
    def unavailable(*args, **kwargs):
        raise OAuthError("identity_verification_unavailable")
    monkeypatch.setattr(oauth, "verify_identity", unavailable)
    monkeypatch.setattr(oauth, "refresh", lambda *_: pytest.fail("candidate validation is not refresh"))
    credentials = Credentials(store, oauth)
    with pytest.raises(OAuthError, match="identity_verification_unavailable"):
        credentials.accept_login(signed_body(private, scope=SCOPES), "issued", "nonce")
    status = credential_status(store.read())
    assert status["pending_token_validation"] and status["access_token_expired"]
    assert status["refresh_wait_seconds"] > 0
    monkeypatch.setattr(oauth, "verify_identity", original_verify)
    assert credentials._get_access_token() == "never-store-access"
    assert "pending_tokens" not in store.read()


@pytest.mark.parametrize("value", [float("inf"), float("nan"), "secret", True])
def test_refresh_wait_status_does_not_echo_invalid_values(value):
    status = credential_status({"credentials": {"expires_at": 0, "earliest_refresh_at": value}}, now=100)
    assert status["refresh_wait_seconds"] == 0 and "secret" not in str(status)
