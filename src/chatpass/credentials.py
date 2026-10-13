"""Single-account credentials, refresh serialization and durable rotation checkpoints."""

from __future__ import annotations

import asyncio
import time

import portalocker

from .oauth import OAuthClient, OAuthError
from .storage import StateStore


class Credentials:
    def __init__(self, store: StateStore, oauth: OAuthClient | None = None):
        self.store = store
        self.oauth = oauth or OAuthClient()

    @staticmethod
    def _is_login_candidate(pending: dict) -> bool:
        # Legacy login quarantine entries carried a nonce but no explicit kind.
        return pending.get("kind") == "login" or (
            "kind" not in pending and "nonce" in pending
        )

    def _promote_pending(self, state: dict) -> None:
        pending = state["pending_tokens"]
        try:
            validated = self.oauth.validate_tokens(
                pending["body"],
                pending["client_id"],
                nonce=pending.get("nonce"),
                subject=pending.get("subject"),
                old_scope=pending.get("scope"),
                received_at=pending["received_at"],
            )
        except OAuthError as exc:
            if exc.code == "identity_verification_unavailable":
                # The replacement refresh token must survive temporary JWKS failures.
                raise
            state.pop("pending_tokens", None)
            if (self._is_login_candidate(pending) and exc.code == "scope_not_authorized"
                    and exc.verified_identity is not None and not state.get("credentials")
                    and not state.get("refresh_in_progress")):
                state["signed_in_identity"] = exc.verified_identity
                if isinstance(state.get("registration"), dict):
                    state["registration"]["subject"] = exc.verified_identity["subject"]
                state.pop("login_required", None)
                self.store.checkpoint(state)
                raise
            # A failed login candidate is not evidence that the saved grant failed.
            # Refresh successors, however, can never fall back to a used predecessor.
            if not self._is_login_candidate(pending) or not (state.get("credentials") or state.get("signed_in_identity")):
                state["login_required"] = True
            self.store.checkpoint(state)
            raise
        current = state.get("credentials", {})
        if (not self._is_login_candidate(pending) and "id_token" not in validated
                and current.get("client_id") == validated["client_id"]
                and current.get("subject") == validated["subject"]
                and current.get("id_token")):
            validated["id_token"] = current["id_token"]
        state["credentials"] = validated
        state.pop("signed_in_identity", None)
        if isinstance(state.get("registration"), dict):
            state["registration"]["subject"] = validated["subject"]
        state.pop("pending_tokens", None)
        state.pop("refresh_in_progress", None)
        state.pop("login_required", None)
        self.store.checkpoint(state)

    def accept_login(self, body: dict, client_id: str, nonce: str) -> None:
        with self.store.transaction() as state:
            if "pending_tokens" in state:
                raise OAuthError("pending_token_validation")
            current = state.get("credentials", {})
            state["pending_tokens"] = {
                "kind": "login",
                "body": body,
                "client_id": client_id,
                "nonce": nonce,
                "subject": (current.get("subject") or state.get("registration", {}).get("subject")
                            or state.get("signed_in_identity", {}).get("subject")),
                "received_at": time.time(),
            }
            self.store.checkpoint(state)
            self._promote_pending(state)

    def _get_access_token(self) -> str:
        with self.store.transaction() as state:
            if "pending_tokens" in state:
                candidate = self._is_login_candidate(state["pending_tokens"])
                try:
                    self._promote_pending(state)
                except OAuthError:
                    current = state.get("credentials", {})
                    # Only an independent, still-valid saved login can remain usable.
                    # Never refresh or reuse an uncertain predecessor in this branch.
                    if (
                        candidate
                        and not state.get("refresh_in_progress")
                        and not state.get("login_required")
                        and current.get("expires_at", 0) > time.time()
                    ):
                        return current["access_token"]
                    raise
            if state.get("refresh_in_progress") or state.get("login_required"):
                raise OAuthError("login_required")
            current = state.get("credentials")
            if not isinstance(current, dict):
                if state.get("signed_in_identity"):
                    raise OAuthError("scope_not_authorized")
                raise OAuthError("login_required")
            now = time.time()
            if current.get("expires_at", 0) > now + 30:
                return current["access_token"]
            if now < current.get("earliest_refresh_at", 0):
                if current.get("expires_at", 0) > now:
                    return current["access_token"]
                raise OAuthError("refresh_not_yet_allowed")
            # Checkpoint before the POST: a crash or ambiguous network failure must not
            # replay a rotating predecessor. Only interactive re-login can resolve it.
            state["refresh_in_progress"] = True
            self.store.checkpoint(state)
            body = self.oauth.refresh(current["client_id"], current["refresh_token"])
            state["pending_tokens"] = {
                "kind": "refresh",
                "body": body,
                "client_id": current["client_id"],
                "subject": current["subject"],
                "scope": current["scope"],
                "received_at": time.time(),
            }
            self.store.checkpoint(state)
            self._promote_pending(state)
            return state["credentials"]["access_token"]

    def logout(self, *, new_account: bool = False) -> dict:
        """Explicit local sign-out, with best-effort revocation and no token replay."""
        self.store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with portalocker.Lock(str(self.store.directory / "login.lock"), timeout=0):
                with self.store.transaction() as state:
                    current = state.get("credentials", {})
                    pending = state.get("pending_tokens")
                    uncertain = bool(state.get("refresh_in_progress") or pending or state.get("signed_in_identity"))
                    attempted = False
                    confirmed = False
                    # Quarantined successors and uncertain rotations must not cause
                    # revocation of a predecessor. Unvalidated tokens are not sent.
                    if current and not state.get("refresh_in_progress") and (
                        not pending or self._is_login_candidate(pending)
                    ):
                        token, client = current.get("refresh_token"), current.get("client_id")
                        if isinstance(token, str) and token and isinstance(client, str) and client:
                            attempted = True
                            try:
                                self.oauth.revoke(client, token)
                                confirmed = True
                            except Exception:
                                # Remote uncertainty never prevents explicit local cleanup.
                                confirmed = False
                    registration = state.get("registration")
                    if isinstance(registration, dict):
                        if new_account:
                            registration.pop("client_id", None)
                            registration.pop("subject", None)
                        elif current.get("subject"):
                            registration["subject"] = current["subject"]
                    for name in ("credentials", "pending_tokens", "refresh_in_progress",
                                 "login_required", "signed_in_identity"):
                        state.pop(name, None)
                    # Commit before reporting local success; storage failure propagates.
                    self.store.checkpoint(state)
                    outcome = ("unconfirmed" if uncertain or (attempted and not confirmed)
                               else "confirmed" if confirmed else "not_needed")
                    return {"local_signed_out": True,
                            "remote_revocation_confirmed": outcome == "confirmed",
                            "revocation_state": outcome}
        except portalocker.exceptions.LockException:
            raise OAuthError("login_already_running") from None

    async def get_access_token(self) -> str:
        return await asyncio.to_thread(self._get_access_token)
