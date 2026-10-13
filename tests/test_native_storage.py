"""Opt-in native backend probe using only a new, isolated temporary service."""
import os

import keyring
import pytest

from chatpass.storage import StateStore


@pytest.mark.skipif(
    os.environ.get("CHATPASS_NATIVE_KEYRING_TEST") != "1",
    reason="Native secure-store writes require an explicit opt-in.",
)
def test_native_keyring_roundtrip_and_rotation(tmp_path):
    store = StateStore(tmp_path)
    backend = keyring.get_keyring()
    # A random pytest temporary directory yields a fresh path-specific service.
    assert backend.get_password(store.service, "master-key") is None
    try:
        first = store.rotate_gateway_key()
        assert store.verify_gateway_key(first)
        with store.transaction() as state:
            state["smoke_marker"] = "nonsecret-storage-smoke"
        assert b"nonsecret-storage-smoke" not in store.path.read_bytes()
        assert store.read()["smoke_marker"] == "nonsecret-storage-smoke"
        second = store.rotate_gateway_key()
        assert not store.verify_gateway_key(first)
        assert store.verify_gateway_key(second)
    finally:
        # Remove only the new test service, never any existing application key.
        if backend.get_password(store.service, "master-key") is not None:
            backend.delete_password(store.service, "master-key")
        assert backend.get_password(store.service, "master-key") is None
