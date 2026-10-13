"""Opt-in real-browser integration; never opens official login or real keyring."""

import asyncio
import os
import socket
import threading
from pathlib import Path

import httpx
import pytest
import uvicorn

from chatpass.config import Settings
from chatpass.console import create_console_app
from chatpass.storage import StateStore

pytestmark = pytest.mark.skipif(
    os.environ.get("CHATPASS_BROWSER_TEST") != "1",
    reason="Opt-in browser test requires installed Edge or Playwright Chromium",
)


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[service, name] = value


class FakeCredentials:
    error = None

    async def get_access_token(self):
        if self.error:
            raise self.error
        return "isolated-test-token"


async def test_real_browser_console_security_and_key_flow(tmp_path):
    from playwright.async_api import async_playwright

    store = StateStore(tmp_path, backend=MemoryKeyring())
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{
            "visibility": "list", "slug": "isolated-model",
            "display_name": "<img src=x onerror=alert(1)>",
        }]})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    credentials = FakeCredentials()
    app = create_console_app(Settings(port=port), store, credentials, client=client,
                             bootstrap_token="isolated-browser-capability")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                          log_level="critical", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        async with async_playwright() as playwright:
            edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
            options = {"executable_path": str(edge)} if edge.exists() else {}
            browser = await playwright.chromium.launch(headless=True, **options)
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1050})
                page = await context.new_page()
                errors, resources = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: resources.append(request.url))
                await page.goto(origin + "/#token=isolated-browser-capability")
                await page.locator("#console-content").wait_for(state="visible")
                await page.locator('[data-view="key"]').click()
                await page.locator("#init-key").click()
                await page.locator("#key-reveal").wait_for(state="visible")
                key = await page.locator("#revealed-key").text_content()
                assert store.verify_gateway_key(key)
                assert "token=" not in page.url and not calls
                assert await page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
                cookie = next(c for c in await context.cookies() if c["name"] == "chatpass_admin")
                assert cookie["httpOnly"] and cookie["sameSite"] == "Strict"
                assert cookie["path"] == "/admin"
                assert "chatpass_admin" not in await page.evaluate("document.cookie")
                await page.locator('[data-view="clients"]').click()
                assert await page.locator("#revealed-key").text_content() == ""
                await page.locator('[data-view="key"]').click()
                assert await page.locator("#key-reveal").is_hidden()
                await page.locator("#rotate-key").click()
                await page.locator("#cancel-rotate").click()
                assert store.verify_gateway_key(key)
                await page.locator("#rotate-key").click()
                await page.locator("#confirm-rotate").click()
                await page.locator("#key-reveal").wait_for(state="visible")
                successor = await page.locator("#revealed-key").text_content()
                assert store.verify_gateway_key(successor) and not store.verify_gateway_key(key)
                await page.locator('[data-view="models"]').click()
                assert not calls
                await page.locator("#load-models").click()
                await page.locator("#model-list").wait_for(state="visible")
                assert calls == ["/v1/models"]
                assert await page.locator(".model-info strong").text_content() == "<img src=x onerror=alert(1)>"
                assert await page.locator("#model-list img").count() == 0
                await page.locator('[data-model-choice="isolated-model"]').click()
                await page.locator('[data-view="clients"]').click()
                assert await page.locator("#client-model-id").text_content() == "isolated-model"
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.locator("#menu-toggle").click()
                await page.locator('[data-view="overview"]').click()
                assert await page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.reload()
                await page.locator("#console-content").wait_for(state="visible")
                # Canonical pending-token guidance must not suggest repeating OAuth.
                with store.transaction() as state:
                    state["pending_tokens"] = {"isolated_test": True}
                    state["login_required"] = True
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '待验证'")
                credentials.error = RuntimeError("never-display-this-secret")
                await page.locator("#menu-toggle").click()
                await page.locator('[data-view="models"]').click()
                await page.locator("#load-models").click()
                await page.wait_for_function("() => document.querySelector('#models-state').textContent.includes('不要重复发起授权')")
                assert "never-display" not in await page.locator("#models-state").text_content()
                # A fake clock proves the 210-second polling budget really stops.
                await page.clock.install()
                app.state.console_state.login_state = "running"
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#login-state').textContent === '等待官方浏览器授权'")
                await page.clock.fast_forward(211_001)
                await page.wait_for_function("() => document.querySelector('#error-notice').textContent.includes('已停止自动轮询')")
                before = resources.count(origin + "/admin/api/status")
                await page.clock.fast_forward(10_000)
                assert resources.count(origin + "/admin/api/status") == before
                # An explicit status refresh can deliberately restart the budget.
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => !document.querySelector('#refresh-status').disabled")
                async with page.expect_request(origin + "/admin/api/status"):
                    await page.clock.fast_forward(2001)
                assert resources.count(origin + "/admin/api/status") > before + 1
                app.state.console_state.login_state = "idle"
                app.state.console_state.session_expires = 0
                # Reload deterministically exercises expired GET-session recovery;
                # do not race a polling response against a now-hidden refresh button.
                await page.reload()
                await page.locator("#locked-instructions").wait_for(state="visible")
                assert await page.locator("#console-content").is_hidden()
                assert all(url.startswith(origin + "/") for url in resources)
                assert not errors
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 5)
        await client.aclose()
        sock.close()
    assert not thread.is_alive()
