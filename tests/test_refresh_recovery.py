"""Synthetic refresh wire and durable signing-key publication recovery checks."""

import json
import time
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_storage import MemoryKeyring

from chatpass.credentials import Credentials
from chatpass.oauth import ISSUER, RESOURCE, SCOPES, OAuthClient, OAuthError
from chatpass.storage import StateStore


def _store(path, backend):
    store = StateStore(path, backend=backend)
    with store.transaction() as state:
        state["registration"] = {"client_id": "issued", "subject": "person"}
        state["credentials"] = {
            "client_id": "issued",
            "subject": "person",
            "scope": SCOPES,
            "access_token": "synthetic-old-access",
            "refresh_token": "synthetic-old-refresh",
            "expires_at": 0,
        }
    return store


def _assert_refresh_wire(request):
    assert request.method == "POST"
    assert request.url == ISSUER + "/api/accounts/oauth/token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "client_id": ["issued"],
        "refresh_token": ["synthetic-old-refresh"],
        "resource": [RESOURCE],
    }


def _discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/api/accounts/oauth/token",
        "jwks_uri": ISSUER + "/jwks",
    }


def test_missing_refresh_signing_key_survives_restart_without_token_replay(tmp_path):
    backend = MemoryKeyring()
    store = _store(tmp_path, backend)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "newly-published"
    now = int(time.time())
    identity = jwt.encode(
        {"iss": ISSUER, "aud": "issued", "sub": "person", "iat": now, "exp": now + 3600},
        private,
        algorithm="RS256",
        headers={"kid": jwk["kid"]},
    )
    published = False
    posts = []
    jwks_reads = []

    def handler(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=_discovery())
        if request.url.path == "/jwks":
            jwks_reads.append(published)
            return httpx.Response(200, json={"keys": [jwk] if published else []})
        _assert_refresh_wire(request)
        posts.append(request.content)
        assert len(posts) == 1, "Never replay the rotating predecessor"
        return httpx.Response(200, json={
            "access_token": "synthetic-successor-access",
            "refresh_token": "synthetic-successor-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": identity,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        credentials = Credentials(store, OAuthClient(transport))
        with pytest.raises(OAuthError, match="identity_verification_unavailable"):
            credentials._get_access_token()
    saved = store.read()
    assert saved["pending_tokens"]["kind"] == "refresh"
    assert saved["pending_tokens"]["body"]["refresh_token"] == "synthetic-successor-refresh"
    assert saved["refresh_in_progress"] is True
    assert not saved.get("login_required")
    assert saved["credentials"]["refresh_token"] == "synthetic-old-refresh"
    assert b"synthetic-successor-refresh" not in store.path.read_bytes()

    published = True
    reopened = StateStore(tmp_path, backend=backend)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        credentials = Credentials(reopened, OAuthClient(transport))
        assert credentials._get_access_token() == "synthetic-successor-access"
        assert credentials._get_access_token() == "synthetic-successor-access"
    recovered = reopened.read()
    assert recovered["credentials"]["refresh_token"] == "synthetic-successor-refresh"
    assert recovered["credentials"]["scope"] == SCOPES
    assert "pending_tokens" not in recovered
    assert not recovered.get("refresh_in_progress")
    assert not recovered.get("login_required")
    assert len(posts) == 1
    assert jwks_reads == [False, True]


def test_refresh_wire_accepts_omitted_scope_and_id_token(tmp_path):
    store = _store(tmp_path, MemoryKeyring())
    posts = []

    def handler(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=_discovery())
        _assert_refresh_wire(request)
        posts.append(request.content)
        return httpx.Response(200, json={
            "access_token": "synthetic-successor-access",
            "refresh_token": "synthetic-successor-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        assert Credentials(store, OAuthClient(transport))._get_access_token() == (
            "synthetic-successor-access"
        )
    saved = store.read()
    assert saved["credentials"]["scope"] == SCOPES
    assert saved["credentials"]["subject"] == "person"
    assert "id_token" not in saved["credentials"]
    assert len(posts) == 1
    assert "pending_tokens" not in saved
    assert not saved.get("refresh_in_progress")
