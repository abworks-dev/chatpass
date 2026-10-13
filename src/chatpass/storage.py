"""One encrypted, process-locked owner for gateway keys and account credentials."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import keyring
import portalocker
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class StorageError(RuntimeError):
    """Sanitized persistence failure; never includes backend exception details."""


class StateStore:
    def __init__(self, directory: Path, *, backend=None):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "state.enc"
        self.service = "chatpass/" + hashlib.sha256(str(self.directory).encode()).hexdigest()[:24]
        self._backend = backend
        self._local = threading.local()

    def _master_key(self) -> bytes:
        try:
            backend = self._backend or keyring.get_keyring()
            if self._backend is None:
                name = type(backend).__module__
                # Only explicitly recognized OS-backed stores; reject plaintext/unknown plugins.
                allowed = (
                    "keyring.backends.Windows",
                    "keyring.backends.macOS",
                    "keyring.backends.SecretService",
                    "keyring.backends.kwallet",
                )
                if not name.startswith(allowed):
                    raise StorageError("No supported secure system keyring is available.")
            encoded = backend.get_password(self.service, "master-key")
            if encoded is None:
                if self.path.exists():
                    raise StorageError(
                        "Encrypted state exists but its system keyring key is missing."
                    )
                encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
                backend.set_password(self.service, "master-key", encoded)
                if backend.get_password(self.service, "master-key") != encoded:
                    raise StorageError("System keyring did not persist the master key.")
            value = base64.b64decode(encoded, validate=True)
            if len(value) != 32:
                raise StorageError("System keyring contains an invalid master key.")
            return value
        except StorageError:
            raise
        except Exception:
            raise StorageError("Secure system keyring operation failed.") from None

    def _load(self, key: bytes) -> dict:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError
            data = self.path.read_bytes()
            if data[:3] != b"CP1":
                raise ValueError
            decoded = AESGCM(key).decrypt(data[3:15], data[15:], self.service.encode())
            state = json.loads(decoded)
            if not isinstance(state, dict):
                raise ValueError
            return state
        except Exception:
            raise StorageError(
                "Cannot decrypt state: corrupt data or incorrect system key."
            ) from None

    def _save(self, state: dict, key: bytes) -> None:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(state, ensure_ascii=False, allow_nan=False).encode()
        if len(plaintext) > 3 * 1024 * 1024:
            raise StorageError("State exceeds its storage limit.")
        data = b"CP1" + nonce + AESGCM(key).encrypt(nonce, plaintext, self.service.encode())
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".state-", delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise StorageError("Atomic encrypted-state persistence failed.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def transaction(self) -> Iterator[dict]:
        """Hold one exclusive lock through load, refresh/update and atomic commit."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with portalocker.Lock(str(self.directory / "state.lock"), timeout=30):
                key = self._master_key()
                state = self._load(key)
                original = copy.deepcopy(state)
                self._local.active = (state, key)
                try:
                    yield state
                    if state != original:
                        self._save(state, key)
                finally:
                    self._local.active = None
        except (portalocker.exceptions.LockException, OSError):
            raise StorageError("Cannot acquire or access the state transaction.") from None

    def checkpoint(self, state: dict) -> None:
        """Durably record single-use token progress while retaining its exclusive lock."""
        active = getattr(self._local, "active", None)
        if active is None or active[0] is not state:
            raise StorageError("Checkpoint requires the active state transaction.")
        self._save(state, active[1])

    def read(self) -> dict:
        with self.transaction() as state:
            return copy.deepcopy(state)

    def initialize_gateway_key(self) -> str:
        return self._issue_gateway_key(require_absent=True)

    def rotate_gateway_key(self) -> str:
        return self._issue_gateway_key(require_absent=False)

    def _issue_gateway_key(self, *, require_absent: bool) -> str:
        value = "cp_" + secrets.token_urlsafe(32)
        with self.transaction() as state:
            if require_absent and state.get("gateway_key_hash"):
                raise StorageError("A gateway key already exists. Use key rotate to replace it.")
            state["gateway_key_hash"] = hashlib.sha256(value.encode()).hexdigest()
        return value

    def verify_gateway_key(self, value: str) -> bool:
        if not isinstance(value, str) or not value.startswith("cp_") or len(value) > 256:
            return False
        if not self.path.exists():
            return False
        expected = self.read().get("gateway_key_hash", "")
        return (
            isinstance(expected, str)
            and bool(expected)
            and hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), expected)
        )
