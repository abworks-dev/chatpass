import asyncio
import base64
import multiprocessing
import time

from chatpass.credentials import Credentials
from chatpass.storage import StateStore, StorageError


class FixedTestKeyring:
    def get_password(self, *_):
        return base64.b64encode(b"K" * 32).decode()


def _initialize(path, barrier, outcomes):
    store = StateStore(path, backend=FixedTestKeyring())
    barrier.wait(timeout=10)
    try:
        outcomes.put((True, store.initialize_gateway_key()))
    except StorageError:
        outcomes.put((False, None))


def test_key_init_is_atomic_across_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    outcomes = ctx.Queue()
    workers = [ctx.Process(target=_initialize, args=(tmp_path, barrier, outcomes)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        results = [outcomes.get(timeout=3) for _ in workers]
        assert sum(succeeded for succeeded, _ in results) == 1
        key = next(key for succeeded, key in results if succeeded)
        assert StateStore(tmp_path, backend=FixedTestKeyring()).verify_gateway_key(key)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(3)
        outcomes.close()
        outcomes.join_thread()


def _increment(path):
    store = StateStore(path, backend=FixedTestKeyring())
    for _ in range(5):
        with store.transaction() as state:
            state["counter"] = state.get("counter", 0) + 1


def _refresh(path, counter):
    class OAuth:
        def refresh(self, client_id, token):
            assert token == "old-refresh"
            with counter.get_lock():
                counter.value += 1
            return {"access_token": "new-access", "refresh_token": "new-refresh"}

        def validate_tokens(self, body, client_id, **kwargs):
            return {
                **body,
                "client_id": client_id,
                "subject": "person",
                "scope": "scope",
                "expires_at": time.time() + 3600,
            }

    store = StateStore(path, backend=FixedTestKeyring())
    assert asyncio.run(Credentials(store, OAuth()).get_access_token()) == "new-access"


def test_process_transactions_do_not_lose_updates(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    workers = [ctx.Process(target=_increment, args=(tmp_path,)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        if worker.is_alive():
            worker.terminate()
            worker.join()
        assert worker.exitcode == 0
    assert StateStore(tmp_path, backend=FixedTestKeyring()).read()["counter"] == 20


def test_rotating_refresh_is_single_use_across_processes(tmp_path):
    store = StateStore(tmp_path, backend=FixedTestKeyring())
    with store.transaction() as state:
        state["credentials"] = {
            "client_id": "issued",
            "subject": "person",
            "scope": "scope",
            "access_token": "old",
            "refresh_token": "old-refresh",
            "expires_at": 0,
        }
    ctx = multiprocessing.get_context("spawn")
    counter = ctx.Value("i", 0)
    workers = [ctx.Process(target=_refresh, args=(tmp_path, counter)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        if worker.is_alive():
            worker.terminate()
            worker.join()
        assert worker.exitcode == 0
    assert counter.value == 1
