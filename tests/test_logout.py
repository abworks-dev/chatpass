"""Synthetic logout contracts only: no keyring, account, or live network."""
import asyncio
from urllib.parse import parse_qs

import httpx
import portalocker
import pytest
from test_console import BOOTSTRAP, ORIGIN, authorize, settings
from test_credentials import FakeOAuth, make_store
from test_storage import MemoryKeyring

from chatpass.cli import main
from chatpass.console import create_console_app
from chatpass.credentials import Credentials
from chatpass.oauth import ISSUER, OAuthClient, OAuthError
from chatpass.storage import StateStore, StorageError


class Revoker(FakeOAuth):
    def __init__(self, fail=False):
        super().__init__()
        self.revoked = []
        self.fail = fail

    def revoke(self, client_id, token):
        self.revoked.append((client_id, token))
        if self.fail:
            raise OAuthError("revocation_unconfirmed")

    def close(self):
        pass


@pytest.mark.parametrize("status", [200, 302, 400, 503])
def test_revoke_empty_body_no_redirect_no_retry(status):
    seen = []
    endpoint = ISSUER + "/api/accounts/oauth/revoke"
    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        oauth = OAuthClient(http)
        oauth._discovery = {"revocation_endpoint": endpoint}
        if status == 200:
            assert oauth.revoke("issued", "synthetic-refresh") is None
        else:
            with pytest.raises(OAuthError, match="revocation_unconfirmed"):
                oauth.revoke("issued", "synthetic-refresh")
    assert len(seen) == 1
    assert str(seen[0].url) == endpoint
    assert parse_qs(seen[0].content.decode()) == {
        "client_id": ["issued"], "token": ["synthetic-refresh"],
        "token_type_hint": ["refresh_token"]}


@pytest.mark.parametrize("endpoint", [None, "http://auth.openai.com/revoke",
    "https://evil.example/revoke", "https://auth.openai.com@evil.example/revoke",
    "https://auth.openai.com/revoke#fragment", "https://auth.openai.com:443/revoke"])
def test_revoke_endpoint_fail_closed(endpoint):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no token POST"))) as http:
        oauth = OAuthClient(http)
        oauth._discovery = {"revocation_endpoint": endpoint}
        with pytest.raises(OAuthError, match="invalid_discovery"):
            oauth.revoke("issued", "synthetic-refresh")


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("new_account", [False, True])
def test_logout_cleanup_and_registration(tmp_path, fail, new_account):
    store = make_store(tmp_path)
    key = store.rotate_gateway_key()
    with store.transaction() as state:
        state["registration"]["host_id"] = "urn:uuid:local"
    oauth = Revoker(fail)
    result = Credentials(store, oauth).logout(new_account=new_account)
    assert result == {"local_signed_out": True, "remote_revocation_confirmed": not fail,
                      "revocation_state": "unconfirmed" if fail else "confirmed"}
    assert oauth.revoked == [("issued", "rotating-old")]
    state = store.read()
    assert store.verify_gateway_key(key)
    assert state["registration"]["host_id"] == "urn:uuid:local"
    assert ("client_id" in state["registration"]) is (not new_account)
    assert ("subject" in state["registration"]) is (not new_account)
    for field in ("credentials", "pending_tokens", "refresh_in_progress", "login_required"):
        assert field not in state
    assert "rotating-old" not in str(state)


@pytest.mark.parametrize("kind", ["ambiguous", "refresh", "legacy-refresh", "login"])
def test_uncertain_session_never_revokes_predecessor(tmp_path, kind):
    store = make_store(tmp_path)
    with store.transaction() as state:
        if kind == "ambiguous":
            state["refresh_in_progress"] = True
        else:
            state["pending_tokens"] = {"body": {"refresh_token": "unverified-new"}}
            if kind != "legacy-refresh":
                state["pending_tokens"]["kind"] = kind
    oauth = Revoker()
    result = Credentials(store, oauth).logout()
    assert oauth.revoked == ([("issued", "rotating-old")] if kind == "login" else [])
    assert result["revocation_state"] == "unconfirmed"
    assert "pending_tokens" not in store.read()


def test_logout_serializes_against_interactive_login(tmp_path):
    store = make_store(tmp_path)
    before = store.read()
    with portalocker.Lock(str(tmp_path / "login.lock"), timeout=0):
        with pytest.raises(OAuthError, match="login_already_running"):
            Credentials(store, Revoker()).logout()
    assert store.read() == before


def test_logout_waits_for_refresh_and_revokes_verified_successor(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store = make_store(tmp_path)
    started, release = threading.Event(), threading.Event()
    class BlockingOAuth(Revoker):
        def refresh(self, client_id, token):
            started.set()
            assert release.wait(3)
            return super().refresh(client_id, token)
    oauth = BlockingOAuth()
    credentials = Credentials(store, oauth)
    with ThreadPoolExecutor(max_workers=2) as executor:
        refreshing = executor.submit(credentials._get_access_token)
        assert started.wait(3)
        signing_out = executor.submit(credentials.logout)
        assert not oauth.revoked
        release.set()
        assert refreshing.result(3) == "new"
        assert signing_out.result(3)["remote_revocation_confirmed"] is True
    assert oauth.revoked == [("issued", "rotating-new")]
    assert "credentials" not in store.read()


def test_logout_storage_failure_not_claimed_success(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    def fail(_):
        raise StorageError("synthetic persistence failure")
    monkeypatch.setattr(store, "checkpoint", fail)
    with pytest.raises(StorageError):
        Credentials(store, Revoker()).logout()
    assert "credentials" in store.read()


def test_normal_return_binds_subject_new_account_unbinds(tmp_path):
    store = make_store(tmp_path)
    class Identity(Revoker):
        def validate_tokens(self, body, client_id, **kwargs):
            expected = kwargs.get("subject")
            if expected and expected != "other":
                raise OAuthError("account_mismatch")
            return super().validate_tokens(body, client_id, **kwargs) | {"subject": "other"}
    credentials = Credentials(store, Identity())
    credentials.logout()
    with pytest.raises(OAuthError, match="account_mismatch"):
        credentials.accept_login({"access_token": "new", "refresh_token": "new-refresh"}, "issued", "nonce")
    assert "credentials" not in store.read()
    credentials.logout(new_account=True)
    credentials.accept_login({"access_token": "new", "refresh_token": "new-refresh"}, "different-client", "nonce")
    assert store.read()["credentials"]["subject"] == "other"


def test_cli_logout_safe_result(tmp_path, monkeypatch, capsys):
    store = make_store(tmp_path)
    credentials = Credentials(store, Revoker(True))
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    monkeypatch.setattr("chatpass.cli.Credentials", lambda _: credentials)
    assert main(["--data-dir", str(tmp_path), "logout", "--new-account"]) == 0
    output = capsys.readouterr().out
    assert "rotating-old" not in output and '"local_signed_out": true' in output
    assert "Disconnect ChatPass" in output
    assert "client_id" not in store.read()["registration"]


@pytest.mark.asyncio
async def test_console_logout_security_and_cleanup(tmp_path):
    store = make_store(tmp_path)
    credentials = Credentials(store, Revoker())
    app = create_console_app(settings(), store, credentials, bootstrap_token=BOOTSTRAP)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            assert (await client.post("/admin/api/logout", json={"new_account": False},
                                      headers={"Origin": ORIGIN})).status_code == 401
            headers = await authorize(client)
            assert (await client.post("/admin/api/logout", json={"new_account": False},
                                      headers={"Origin": ORIGIN})).status_code == 403
            for body in ({}, {"new_account": "false"}, {"new_account": False, "token": "echo"}):
                assert (await client.post("/admin/api/logout", json=body, headers=headers)).status_code == 400
            result = await client.post("/admin/api/logout", json={"new_account": False}, headers=headers)
            assert result.status_code == 200 and result.json()["local_signed_out"]
            assert "rotating-old" not in result.text
            assert "credentials" not in store.read()
            assert (await client.get("/admin/api/status")).json()["account_authorized"] is False


@pytest.mark.asyncio
async def test_logout_admission_while_login_or_worker_active(tmp_path):
    store = StateStore(tmp_path, backend=MemoryKeyring())
    app = create_console_app(settings(), store, Credentials(store, Revoker()), bootstrap_token=BOOTSTRAP)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            headers = await authorize(client)
            state = app.state.console_state
            gate = asyncio.Event()
            state.login_task = asyncio.create_task(gate.wait())
            result = await client.post("/admin/api/logout", json={"new_account": False}, headers=headers)
            assert result.status_code == 409
            gate.set()
            await state.login_task
            task = asyncio.create_task(asyncio.sleep(0))
            state.storage_tasks.add(task)
            assert (await client.post("/admin/api/login", headers=headers)).status_code == 429
            assert (await client.post("/admin/api/logout", json={"new_account": False}, headers=headers)).status_code == 429
            state.storage_tasks.discard(task)
            await task
