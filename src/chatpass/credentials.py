"""Single-account credentials, refresh serialization and durable rotation checkpoints."""

from __future__ import annotations

import asyncio
import time

from .oauth import OAuthClient, OAuthError
from .storage import StateStore


class Credentials:
    def __init__(self, store: StateStore, oauth: OAuthClient | None = None):
        self.store = store
        self.oauth = oauth or OAuthClient()

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
            state["login_required"] = True
            self.store.checkpoint(state)
            raise
        state["credentials"] = validated
        state.pop("pending_tokens", None)
        state.pop("refresh_in_progress", None)
        state.pop("login_required", None)
        self.store.checkpoint(state)

    def accept_login(self, body: dict, client_id: str, nonce: str) -> None:
        with self.store.transaction() as state:
            current = state.get("credentials", {})
            state["pending_tokens"] = {
                "body": body,
                "client_id": client_id,
                "nonce": nonce,
                "subject": current.get("subject"),
                "received_at": time.time(),
            }
            self.store.checkpoint(state)
            self._promote_pending(state)

    def _get_access_token(self) -> str:
        with self.store.transaction() as state:
            if "pending_tokens" in state:
                self._promote_pending(state)
            if state.get("refresh_in_progress") or state.get("login_required"):
                raise OAuthError("login_required")
            current = state.get("credentials")
            if not isinstance(current, dict):
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
                "body": body,
                "client_id": current["client_id"],
                "subject": current["subject"],
                "scope": current["scope"],
                "received_at": time.time(),
            }
            self.store.checkpoint(state)
            self._promote_pending(state)
            return state["credentials"]["access_token"]

    async def get_access_token(self) -> str:
        return await asyncio.to_thread(self._get_access_token)
