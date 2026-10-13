"""Synthetic returning login and authenticated manual-link recovery."""
import asyncio
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_console import BOOTSTRAP, ORIGIN, Credentials, authorize, settings
from test_credentials import FakeOAuth, make_store
from test_oauth import issuer_client

from chatpass.console import create_console_app
from chatpass.credentials import Credentials as CredentialOwner
from chatpass.oauth import LoginAttempt


@pytest.mark.parametrize("browser", ["opened", "false", "raises", "disabled"])
def test_returning_hint_never_in_emitted_or_manual_link(tmp_path, monkeypatch, browser):
    import chatpass.login as module

    store = make_store(tmp_path)
    with store.transaction() as state:
        state["registration"]["host_id"] = "urn:uuid:local"
        state["credentials"]["id_token"] = "verified-old-hint"
    real, _ = issuer_client()
    real.discover()
    class Fake(FakeOAuth):
        def authorization_url(self, *args, **kwargs):
            return real.authorization_url(*args, **kwargs)
        def exchange(self, *args):
            return {"access_token": "new", "refresh_token": "rotating-new", "id_token": "verified-new-hint"}
        def close(self):
            pass
    class Server:
        redirect_uri = "http://127.0.0.1:1234/auth/callback"
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def configure(self, *args):
            pass
        def wait(self, timeout):
            return "code", "issued"
    opened, emitted, manual = [], [], []
    def open_url(url):
        opened.append(url)
        if browser == "raises":
            raise RuntimeError("browser failure must not be printed")
        return browser == "opened"
    monkeypatch.setattr(module, "OAuthClient", Fake)
    monkeypatch.setattr(module, "CallbackServer", Server)
    monkeypatch.setattr(module.webbrowser, "open", open_url)
    module.login(store, open_browser=browser != "disabled", emit=emitted.append,
                 on_authorization=lambda url, deadline: manual.append((url, deadline)))
    assert len(manual) == 1 and manual[0][1] > time.monotonic()
    assert "id_token_hint" not in manual[0][0] and "verified-old-hint" not in str(emitted)
    if opened:
        assert parse_qs(urlsplit(opened[0]).query)["id_token_hint"] == ["verified-old-hint"]
    if browser != "opened":
        assert manual[0][0] in emitted
    assert store.read()["credentials"]["id_token"] == "verified-new-hint"
    assert b"verified-new-hint" not in store.path.read_bytes()


def test_dynamic_registration_never_takes_hint():
    oauth, _ = issuer_client()
    attempt = LoginAttempt.create("http://127.0.0.1:1234/auth/callback")
    url = oauth.authorization_url(attempt, None, "urn:uuid:local", id_token_hint="old-secret")
    assert "id_token_hint" not in url and "old-secret" not in url


def test_refresh_without_new_identity_retains_verified_hint(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as state:
        state["credentials"]["id_token"] = "verified-old-hint"
    assert CredentialOwner(store, FakeOAuth())._get_access_token() == "new"
    assert store.read()["credentials"]["id_token"] == "verified-old-hint"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [False, True])
async def test_manual_link_only_authenticated_endpoint_and_cleared(tmp_path, monkeypatch, bad):
    store = make_store(tmp_path)
    gate = threading.Event()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    url = "https://auth.openai.com/api/accounts/authorize?state=state&nonce=nonce&code_challenge=challenge"
    if bad:
        url += "&id_token_hint=must-not-reach-management"
    def runner(actual, *, emit, on_authorization):
        on_authorization(url, time.monotonic() + 180)
        loop.call_soon_threadsafe(entered.set)
        assert gate.wait(3)
    monkeypatch.setattr("chatpass.console.login", runner)
    app = create_console_app(settings(), store, Credentials(), bootstrap_token=BOOTSTRAP)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
                assert (await client.get("/admin/api/login-link")).status_code == 401
                headers = await authorize(client)
                assert (await client.post("/admin/api/login", headers=headers)).status_code == 202
                await asyncio.wait_for(entered.wait(), 1)
                link = await client.get("/admin/api/login-link")
                assert link.headers["cache-control"] == "no-store"
                assert link.json()["url"] == (None if bad else url)
                assert "must-not-reach-management" not in link.text
                snapshot = (await client.get("/admin/api/status")).text
                assert "code_challenge" not in snapshot and "https://auth.openai.com" not in snapshot
                if not bad:
                    app.state.console_state.login_link = (url, time.monotonic() - 1)
                    assert (await client.get("/admin/api/login-link")).json()["url"] is None
                gate.set()
                await app.state.console_state.login_task
                assert app.state.console_state.login_link is None
                assert (await client.get("/admin/api/login-link")).json()["url"] is None
    finally:
        gate.set()
    assert app.state.console_state.login_link is None
