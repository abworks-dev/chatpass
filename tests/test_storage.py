import pytest

from chatpass.storage import StateStore, StorageError


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


def test_encrypted_roundtrip_and_gateway_key(tmp_path):
    backend = MemoryKeyring()
    store = StateStore(tmp_path, backend=backend)
    key = store.rotate_gateway_key()
    assert key.startswith('cp_')
    assert store.verify_gateway_key(key)
    assert not store.verify_gateway_key('bad')
    with store.transaction() as state:
        state['credentials'] = {'access_token': 'secret-oauth-token'}
    assert b'secret-oauth-token' not in store.path.read_bytes()
    assert key.encode() not in store.path.read_bytes()
    assert StateStore(tmp_path, backend=backend).read()['credentials']['access_token'] == 'secret-oauth-token'
    rotated = store.rotate_gateway_key()
    assert store.verify_gateway_key(rotated)
    assert not store.verify_gateway_key(key)
    assert StateStore(tmp_path, backend=backend).get_gateway_key() == rotated
    assert rotated.encode() not in store.path.read_bytes()


def test_corruption_fails_closed(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    data = bytearray(store.path.read_bytes())
    data[-1] ^= 1
    store.path.write_bytes(data)
    with pytest.raises(StorageError, match='decrypt|corrupt'):
        store.read()


def test_failed_transaction_does_not_persist(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        with store.transaction() as state:
            state['credentials'] = {'access_token': 'bad'}
            raise ValueError('test failure')
    assert store.path.read_bytes() == before
    assert 'credentials' not in store.read()


def test_keyring_error_is_sanitized_and_no_plaintext(tmp_path):
    class Broken:
        def get_password(self, *_):
            raise RuntimeError('secret-backend-detail')
    store = StateStore(tmp_path, backend=Broken())
    with pytest.raises(StorageError) as exc:
        store.rotate_gateway_key()
    assert 'secret-backend-detail' not in str(exc.value)
    assert not store.path.exists()


def test_missing_master_key_cannot_overwrite_existing_state(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    original = store.path.read_bytes()
    with pytest.raises(StorageError):
        StateStore(tmp_path, backend=MemoryKeyring()).rotate_gateway_key()
    assert store.path.read_bytes() == original


def test_legacy_hash_remains_valid_without_implicit_rotation(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    key = store.initialize_gateway_key()
    with store.transaction() as state:
        state.pop('gateway_key')  # Simulate a pre-upgrade encrypted state.
    original = store.path.read_bytes()
    assert store.get_gateway_key() is None
    assert store.verify_gateway_key(key)
    assert store.path.read_bytes() == original
    with pytest.raises(StorageError, match='already exists'):
        store.initialize_gateway_key()
    successor = store.rotate_gateway_key()
    assert store.get_gateway_key() == successor
    assert not store.verify_gateway_key(key)


@pytest.mark.parametrize('value', ['cp_wrong-secret', 123, 'cp_' + 'x' * 300])
def test_recoverable_key_must_match_hash(tmp_path, value):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.initialize_gateway_key()
    with store.transaction() as state:
        state['gateway_key'] = value
    with pytest.raises(StorageError, match='Stored gateway key is invalid'):
        store.get_gateway_key()


def test_invalid_key_does_not_create_state(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    assert not store.verify_gateway_key('bad')
    assert not store.path.exists()
