import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chatpass.oauth import LoginAttempt, OAuthClient, OAuthError, parse_callback

ISSUER = "https://auth.openai.com"


def issuer_client():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "test"

    def handler(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/api/accounts/authorize",
                    "token_endpoint": ISSUER + "/api/accounts/oauth/token",
                    "jwks_uri": ISSUER + "/.well-known/jwks.json",
                },
            )
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        raise AssertionError(str(request.url))

    return OAuthClient(httpx.Client(transport=httpx.MockTransport(handler))), private


def test_authorization_pkce_and_host_binding():
    client, _ = issuer_client()
    attempt = LoginAttempt.create("http://127.0.0.1:1234/auth/callback")
    params = parse_qs(urlparse(client.authorization_url(attempt, None, "urn:uuid:example")).query)
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["ext_agent_host_id"] == ["urn:uuid:example"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"] == [attempt.nonce]
    assert params["state"] == [attempt.state]
    assert params["code_challenge"] != [attempt.verifier]


def test_callback_valid_and_duplicate_rejected():
    attempt = LoginAttempt.create("http://127.0.0.1:1234/auth/callback")
    assert parse_callback(
        "state=" + attempt.state + "&code=abc&client_id=issued", attempt, None
    ) == ("abc", "issued")
    for query in [
        "state=wrong&code=abc&client_id=issued",
        "state=" + attempt.state + "&state=" + attempt.state + "&code=x&client_id=issued",
        "state=" + attempt.state + "&code=x&client_id=dynamic_agent_client",
    ]:
        with pytest.raises(OAuthError):
            parse_callback(query, attempt, None)


def test_signed_id_token_nonce_subject_and_scope():
    client, private = issuer_client()
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "issued",
        "sub": "person",
        "iat": now,
        "exp": now + 3600,
        "nonce": "nonce",
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test"})
    body = {
        "access_token": "access",
        "refresh_token": "refresh",
        "id_token": token,
        "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    result = client.validate_tokens(body, "issued", nonce="nonce")
    assert result["subject"] == "person"
    assert result["id_token"] == token
    with pytest.raises(OAuthError):
        client.validate_tokens(body, "issued", nonce="wrong")
    with pytest.raises(OAuthError):
        client.validate_tokens(body, "issued", nonce="nonce", subject="other")
    with pytest.raises(OAuthError):
        client.validate_tokens({**body, "expires_in": True}, "issued", nonce="nonce")
    with pytest.raises(OAuthError):
        client.validate_tokens({**body, "scope": "openid offline_access"}, "issued", nonce="nonce")


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://evil.example"},
        {"aud": "other"},
        {"exp": 1},
        {"iat": int(time.time()) + 3600},
        {"sub": ""},
        {"azp": "other"},
        {"aud": ["issued", "other"]},
        {"nonce": "wrong"},
    ],
)
def test_invalid_signed_claims_fail_closed(change):
    client, private = issuer_client()
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "issued",
        "sub": "person",
        "iat": now,
        "exp": now + 3600,
        "nonce": "nonce",
    }
    claims.update(change)
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test"})
    with pytest.raises(OAuthError):
        client.verify_identity(token, "issued", nonce="nonce")


@pytest.mark.parametrize("checkpointed", [False, True])
def test_future_id_token_has_actionable_error_without_relaxing_validation(checkpointed):
    client, private = issuer_client()
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": "issued", "sub": "person",
        "iat": now + 16, "exp": now + 3600, "nonce": "nonce",
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test"})
    receipt = {"received_at": now} if checkpointed else {}
    with pytest.raises(OAuthError) as failure:
        client.verify_identity(token, "issued", nonce="nonce", **receipt)
    assert failure.value.code == "id_token_not_yet_valid"
    # Only a corrected clock makes the same signed identity valid.
    verified = client.verify_identity(token, "issued", nonce="nonce", received_at=now + 16)
    assert verified["sub"] == "person"
    with pytest.raises(OAuthError, match="invalid_id_token"):
        client.verify_identity(token, "issued", nonce="wrong", received_at=now + 16)
    client.close()


@pytest.mark.parametrize("ahead,expected", [(5, None), (6, "id_token_not_yet_valid")])
def test_id_token_clock_tolerance_is_still_five_seconds(ahead, expected):
    client, private = issuer_client()
    now = int(time.time())
    token = jwt.encode({
        "iss": ISSUER, "aud": "issued", "sub": "person", "nonce": "nonce",
        "iat": now + ahead, "exp": now + 3600,
    }, private, algorithm="RS256", headers={"kid": "test"})
    if expected:
        with pytest.raises(OAuthError) as failure:
            client.verify_identity(token, "issued", nonce="nonce", received_at=now)
        assert failure.value.code == expected
    else:
        assert client.verify_identity(
            token, "issued", nonce="nonce", received_at=now
        )["sub"] == "person"
    client.close()


def test_token_header_injection_rejected():
    client, _ = issuer_client()
    body = {
        "access_token": "access\r\nInjected: value",
        "refresh_token": "refresh",
        "scope": "offline_access resource.invoke chatgpt.tokens.use.direct",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    with pytest.raises(OAuthError):
        client.validate_tokens(body, "issued", subject="person")


def test_discovery_cannot_redirect_credentials():
    body = {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": "https://attacker.example/token",
        "jwks_uri": ISSUER + "/keys",
    }
    client = OAuthClient(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    )
    with pytest.raises(OAuthError):
        client.discover()
