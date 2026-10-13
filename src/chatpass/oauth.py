"""Public-client OIDC using independently implemented, verified wire contracts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
REQUIRED_SCOPES = {"offline_access", "resource.invoke", "chatgpt.tokens.use.direct"}
CLIENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class OAuthError(RuntimeError):
    def __init__(self, code: str = "authorization_failed", *, verified_identity: dict | None = None):
        self.code = code
        # Private recovery evidence, never included in exception text or public errors.
        self.verified_identity = verified_identity
        super().__init__("Authorization failed (" + code + ").")


@dataclass(frozen=True)
class LoginAttempt:
    redirect_uri: str
    state: str
    nonce: str
    verifier: str

    @classmethod
    def create(cls, uri: str) -> LoginAttempt:
        return cls(
            uri, secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        )


def valid_client_id(value) -> bool:
    return (
        isinstance(value, str)
        and value != "dynamic_agent_client"
        and bool(CLIENT_PATTERN.fullmatch(value))
    )


def parse_callback(query: str, attempt: LoginAttempt, saved_client: str | None) -> tuple[str, str]:
    try:
        params = parse_qs(query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        raise OAuthError("invalid_callback") from None
    states = params.get("state", [])
    if len(states) != 1 or not hmac.compare_digest(states[0], attempt.state):
        raise OAuthError("invalid_state")
    if "error" in params:
        raise OAuthError("consent_denied")
    codes = params.get("code", [])
    clients = params.get("client_id", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192 or len(clients) > 1:
        raise OAuthError("invalid_callback")
    issued = clients[0] if clients else saved_client
    if not valid_client_id(issued) or (saved_client is not None and issued != saved_client):
        raise OAuthError("registration_incomplete")
    return codes[0], issued


class OAuthClient:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        self._discovery = None

    def close(self):
        if self._owns_client:
            self.client.close()

    def _json(self, method: str, url: str, **kwargs) -> dict:
        try:
            with self.client.stream(method, url, **kwargs) as response:
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 1048576:
                        raise OAuthError("invalid_server_response")
                if not 200 <= response.status_code < 300:
                    # A status is safe; arbitrary remote descriptions are not.
                    if response.status_code in (400, 401):
                        try:
                            remote = json.loads(chunks).get("error")
                        except (ValueError, AttributeError):
                            remote = None
                        allowed = {
                            "invalid_grant",
                            "invalid_client",
                            "invalid_refresh_token",
                            "token_expired",
                            "refresh_token_expired",
                            "refresh_token_reused",
                            "refresh_token_invalidated",
                        }
                        if isinstance(remote, str) and remote in allowed:
                            raise OAuthError(remote)
                    raise OAuthError("upstream_http_" + str(response.status_code))
                result = json.loads(chunks)
                if not isinstance(result, dict):
                    raise OAuthError("invalid_server_response")
                return result
        except (httpx.HTTPError, ValueError):
            raise OAuthError("upstream_unavailable") from None

    def discover(self) -> dict:
        if self._discovery is None:
            data = self._json("GET", ISSUER + "/.well-known/openid-configuration")
            if data.get("issuer") != ISSUER:
                raise OAuthError("invalid_discovery")
            for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                value = data.get(name)
                if not isinstance(value, str):
                    raise OAuthError("invalid_discovery")
                parts = urlsplit(value)
                if (
                    parts.scheme != "https"
                    or parts.netloc != "auth.openai.com"
                    or parts.username
                    or parts.password
                    or parts.fragment
                ):
                    raise OAuthError("invalid_discovery")
            self._discovery = data
        return self._discovery

    def authorization_url(
        self, attempt: LoginAttempt, saved_client: str | None, host_id: str,
        *, id_token_hint: str | None = None,
    ) -> str:
        params = {
            "client_id": saved_client or "dynamic_agent_client",
            "response_type": "code",
            "redirect_uri": attempt.redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": attempt.state,
            "nonce": attempt.nonce,
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(attempt.verifier.encode()).digest()
            )
            .decode()
            .rstrip("="),
            "ext_agent_host_id": host_id,
        }
        if saved_client is None:
            params["agent_name_hint"] = "ChatPass"
        elif isinstance(id_token_hint, str) and 0 < len(id_token_hint) <= 65536:
            params["id_token_hint"] = id_token_hint
        return self.discover()["authorization_endpoint"] + "?" + urlencode(params)

    def exchange(self, attempt: LoginAttempt, code: str, client_id: str) -> dict:
        return self._json(
            "POST",
            self.discover()["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": attempt.verifier,
                "redirect_uri": attempt.redirect_uri,
                "resource": RESOURCE,
            },
            headers={"Accept": "application/json"},
        )

    def revoke(self, client_id: str, refresh_token: str) -> None:
        """One explicit revocation attempt; HTTP 200 may have an empty body."""
        endpoint = self.discover().get("revocation_endpoint")
        if not isinstance(endpoint, str):
            raise OAuthError("invalid_discovery")
        parts = urlsplit(endpoint)
        if (parts.scheme != "https" or parts.netloc != "auth.openai.com"
                or parts.username or parts.password or parts.fragment):
            raise OAuthError("invalid_discovery")
        try:
            with self.client.stream(
                "POST", endpoint, follow_redirects=False,
                data={"token": refresh_token, "token_type_hint": "refresh_token",
                      "client_id": client_id},
            ) as response:
                if response.status_code != 200:
                    raise OAuthError("revocation_unconfirmed")
                # No JSON or response body is required for revocation success.
        except httpx.HTTPError:
            raise OAuthError("revocation_unconfirmed") from None

    def refresh(self, client_id: str, refresh_token: str) -> dict:
        return self._json(
            "POST",
            self.discover()["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": RESOURCE,
            },
            headers={"Accept": "application/json"},
        )

    def verify_identity(
        self, token: str, client_id: str, *, nonce=None, subject=None, received_at=None
    ) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise OAuthError("invalid_id_token")
            keys = self._json("GET", self.discover()["jwks_uri"]).get("keys")
            if not isinstance(keys, list):
                raise OAuthError("identity_verification_unavailable")
            matching = [
                k
                for k in keys
                if isinstance(k, dict)
                and k.get("kid") == header["kid"]
                and k.get("kty") == "RSA"
                and k.get("use", "sig") == "sig"
            ]
            if len(matching) != 1:
                raise OAuthError("invalid_id_token")
            key = jwt.PyJWK.from_dict(matching[0], algorithm="RS256").key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=client_id,
                leeway=5,
                options={
                    "require": ["iss", "aud", "exp", "iat", "sub"],
                    "verify_exp": received_at is None,
                    "verify_iat": received_at is None,
                },
            )
            receipt = time.time() if received_at is None else received_at
            for field in ("exp", "iat"):
                if (
                    isinstance(claims[field], bool)
                    or not isinstance(claims[field], (int, float))
                    or not math.isfinite(claims[field])
                ):
                    raise OAuthError("invalid_id_token")
            if claims["exp"] < receipt - 5:
                raise OAuthError("invalid_id_token")
            if claims["iat"] > receipt + 5:
                raise OAuthError("id_token_not_yet_valid")
            if not isinstance(claims["sub"], str) or not claims["sub"].strip():
                raise OAuthError("invalid_id_token")
            aud = claims["aud"]
            if ("azp" in claims and claims["azp"] != client_id) or (
                isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id
            ):
                raise OAuthError("invalid_id_token")
            if nonce is not None and claims.get("nonce") != nonce:
                raise OAuthError("invalid_id_token")
            if subject is not None and claims["sub"] != subject:
                raise OAuthError("account_mismatch")
            return claims
        except jwt.ImmatureSignatureError:
            raise OAuthError("id_token_not_yet_valid") from None
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise OAuthError("invalid_id_token") from None
        except OAuthError as exc:
            if exc.code.startswith("upstream_"):
                raise OAuthError("identity_verification_unavailable") from None
            raise

    def validate_tokens(
        self,
        body: dict,
        client_id: str,
        *,
        nonce=None,
        subject=None,
        old_scope=None,
        received_at=None,
    ) -> dict:
        receipt = time.time() if received_at is None else received_at
        scope = body.get("scope", old_scope)
        identity = body.get("id_token")
        if identity is not None or nonce is not None:
            if not isinstance(identity, str) or not identity or len(identity) > 65536:
                raise OAuthError("invalid_id_token")
            subject = self.verify_identity(
                identity, client_id, nonce=nonce, subject=subject, received_at=received_at
            )["sub"]
        if subject is None:
            raise OAuthError("invalid_id_token")
        if not isinstance(scope, str) or not REQUIRED_SCOPES.issubset(scope.split()):
            recovery = ({"client_id": client_id, "subject": subject, "id_token": identity}
                        if nonce is not None and identity is not None else None)
            raise OAuthError("scope_not_authorized", verified_identity=recovery)
        for field in ("access_token", "refresh_token"):
            if (
                not isinstance(body.get(field), str)
                or not body[field]
                or len(body[field]) > 65536
                or any(ord(char) < 33 or ord(char) > 126 for char in body[field])
            ):
                raise OAuthError("invalid_token_response")
        expiry = body.get("expires_in")
        if (
            isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry)
            or not 0 < expiry <= 31536000
        ):
            raise OAuthError("invalid_token_response")
        if not isinstance(body.get("token_type"), str) or body["token_type"].lower() != "bearer":
            raise OAuthError("invalid_token_response")
        earliest = body.get("earliest_refresh_at", 0)
        if isinstance(earliest, str):
            try:
                from datetime import datetime

                earliest = datetime.fromisoformat(earliest.replace("Z", "+00:00")).timestamp()
            except ValueError:
                raise OAuthError("invalid_token_response") from None
        if (
            isinstance(earliest, bool)
            or not isinstance(earliest, (int, float))
            or not math.isfinite(earliest)
            or earliest < 0
        ):
            raise OAuthError("invalid_token_response")
        return {
            "client_id": client_id,
            "subject": subject,
            "scope": scope,
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": receipt + expiry,
            "earliest_refresh_at": earliest,
            **({"id_token": identity} if identity is not None else {}),
        }
