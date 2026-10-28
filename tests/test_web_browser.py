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
                await page.locator("#setup-card").wait_for(state="visible")
                assert await page.locator("#page-title").text_content() == "概览"
                assert await page.locator(".eyebrow, .boundary-card, .nav-short").count() == 0
                assert await page.locator("#setup-account").is_visible()
                assert await page.locator("#setup-key").is_visible()
                assert await page.locator("#metric-account-note").is_hidden()
                await page.locator(".sidebar-footer summary").focus()
                await page.keyboard.press("Enter")
                assert await page.locator(".sidebar-footer a").is_visible()
                await page.keyboard.press("Enter")
                assert await page.locator(".sidebar-footer a").is_hidden()
                await page.locator('[data-view="key"]').click()
                await page.locator("#init-key").click()
                await page.locator("#key-reveal").wait_for(state="visible")
                key = await page.locator("#revealed-key").input_value()
                assert store.verify_gateway_key(key)
                await page.evaluate("""() => {
                    window.copiedKey = null;
                    Object.defineProperty(navigator, 'clipboard', { configurable: true,
                        value: { writeText: async text => { window.copiedKey = text; } } });
                }""")
                assert await page.locator('#revealed-key').get_attribute('type') == 'password'
                await page.locator('#copy-key').click()
                assert await page.evaluate('window.copiedKey') == key
                eye = page.locator('[data-toggle-key="revealed-key"]')
                await eye.focus()
                await page.keyboard.press('Enter')
                assert await page.locator('#revealed-key').get_attribute('type') == 'text'
                assert await eye.get_attribute('aria-label') == '隐藏密钥'
                assert await eye.get_attribute('aria-pressed') == 'true'
                await eye.click()
                assert await page.locator('#revealed-key').get_attribute('type') == 'password'
                assert "token=" not in page.url and not calls
                assert await page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
                cookie = next(c for c in await context.cookies() if c["name"] == "chatpass_admin")
                assert cookie["httpOnly"] and cookie["sameSite"] == "Strict"
                assert cookie["path"] == "/admin"
                assert "chatpass_admin" not in await page.evaluate("document.cookie")
                await page.wait_for_function("() => document.querySelector('#metric-key').textContent === '已配置'")
                await page.locator('[data-view="overview"]').click()
                assert await page.locator("#setup-key").is_hidden()
                assert await page.locator("#setup-account").is_visible()
                # First plan-use confirmation is explicit; cancel never starts OAuth.
                await page.locator('[data-view="authorization"]').click()
                assert await page.locator('a[href="https://chatgpt.com/settings/usage"]').count() == 1
                await page.locator("#start-login").click()
                await page.locator("#plan-dialog").wait_for(state="visible")
                assert "共享的计划预算" in await page.locator("#plan-description").text_content()
                await page.locator("#cancel-plan").click()
                assert not any(url.endswith("/admin/api/login") for url in resources)
                await page.locator('[data-view="overview"]').click()
                with store.transaction() as state:
                    state["signed_in_identity"] = {"subject": "mock-person", "id_token": "never-display-identity"}
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已登录，计划未授权'")
                assert "不会调用模型" in await page.locator("#metric-account-note").text_content()
                assert "never-display-identity" not in await page.content()
                with store.transaction() as state:
                    state.pop("signed_in_identity")
                # The checklist follows actual local credential state, not browser persistence.
                with store.transaction() as state:
                    state["credentials"] = {"access_token": "isolated-test-token",
                                            "expires_at": 4102444800}
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已保存授权'")
                assert await page.locator("#setup-card").is_hidden()
                assert await page.locator("#metric-account-note").is_hidden()
                assert "setup-complete" in await page.locator("#overview-grid").get_attribute("class")
                with store.transaction() as state:
                    state["credentials"]["expires_at"] = 1
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已登录，待刷新'")
                assert await page.locator("#setup-card").is_visible()
                assert await page.locator("#metric-account-note").is_visible()
                assert await page.locator("#setup-account-action").get_attribute("data-go") == "models"
                import time
                with store.transaction() as state:
                    state["credentials"]["earliest_refresh_at"] = time.time() + 90
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已登录，等待刷新窗口'")
                assert await page.locator("#load-models").is_disabled()
                assert "不会提前刷新" in await page.locator("#metric-account-note").text_content()
                assert await page.locator("#setup-account-action").get_attribute("data-go") == "models"
                assert await page.locator("#start-login").is_disabled()
                assert "已有账号授权" in await page.locator("#setup-account h3").text_content()
                with store.transaction() as state:
                    state["refresh_in_progress"] = True
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已登录，刷新结果不确定'")
                assert "不能盲目重放" in await page.locator("#metric-account-note").text_content()
                assert await page.locator("#start-login").is_enabled()
                with store.transaction() as state:
                    state["pending_tokens"] = {"kind": "login", "isolated_test": True}
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '待验证'")
                assert await page.locator("#load-models").is_enabled()
                assert "查询模型" in await page.locator("#setup-account-action").text_content()
                assert await page.locator("#start-login").is_disabled()
                assert not any(url.endswith("/admin/api/login") for url in resources)
                with store.transaction() as state:
                    state.pop("refresh_in_progress")
                    state.pop("pending_tokens")
                    state["credentials"].pop("earliest_refresh_at")
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '已登录，待刷新'")
                await page.locator("#setup-account-action").click()
                assert await page.locator("#page-title").text_content() == "模型目录"
                assert not calls
                await page.locator('[data-view="overview"]').click()

                async def unavailable_status(route):
                    await route.fulfill(status=503, content_type="application/json",
                                        body='{"error":{"code":"console_unavailable"}}')

                await page.route("**/admin/api/status", unavailable_status)
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '状态不可用'")
                assert await page.locator("#setup-card").is_hidden()
                assert await page.locator("#metric-account-note").is_visible()
                await page.unroute("**/admin/api/status", unavailable_status)
                with store.transaction() as state:
                    state.pop("credentials")
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '尚未授权'")
                assert await page.locator("#setup-account").is_visible()
                await page.locator('[data-view="clients"]').click()
                assert await page.locator("#revealed-key").input_value() == ""
                await page.locator('[data-view="key"]').click()
                await page.locator('#key-reveal').wait_for(state='visible')
                assert await page.locator('#revealed-key').input_value() == key
                assert await page.locator('#revealed-key').get_attribute('type') == 'password'
                await page.locator("#rotate-key").click()
                await page.locator("#cancel-rotate").click()
                assert store.verify_gateway_key(key)
                await page.locator("#rotate-key").click()
                await page.locator("#confirm-rotate").click()
                await page.wait_for_function("old => document.querySelector('#revealed-key').value && document.querySelector('#revealed-key').value !== old", arg=key)
                await page.locator("#key-reveal").wait_for(state="visible")
                successor = await page.locator("#revealed-key").input_value()
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
                client_key = page.locator('#client-api-key')
                client_eye = page.locator('[data-toggle-key="client-api-key"]')
                await page.wait_for_function("key => document.querySelector('#client-api-key').value === key", arg=successor)
                assert await client_key.get_attribute('readonly') is not None
                assert await client_eye.is_enabled()
                assert await page.locator('#copy-client-key').is_enabled()
                assert await client_key.get_attribute('type') == 'password'
                await page.locator('#copy-client-key').click()
                assert await page.evaluate('window.copiedKey') == successor
                await client_eye.click()
                assert await client_key.get_attribute('type') == 'text'
                await client_eye.click()
                assert await client_key.get_attribute('type') == 'password'
                await page.evaluate("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied'); } } })")
                heading_before = await page.locator('.page-heading').bounding_box()
                await page.locator('#copy-client-key').click()
                assert '浏览器未允许复制' in await page.locator('#error-notice').text_content()
                assert await page.locator('.page-heading').bounding_box() == heading_before
                assert await page.locator('#error-notice').evaluate("el => getComputedStyle(el).position") == 'fixed'
                await page.get_by_role('button', name='关闭提示').click()
                assert not await page.locator('#error-notice').is_visible()
                assert await client_key.input_value() == successor
                await page.reload()
                await page.wait_for_function("key => document.querySelector('#client-api-key').value === key", arg=successor)
                assert await client_key.get_attribute('type') == 'password'
                assert await page.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
                guidance = await page.locator(".protocol-note").text_content()
                assert "Chat Completions" in guidance
                assert "X-ChatPass-Ignored-Fields" in guidance
                assert "--compatibility-mode strict" in guidance
                assert "不支持 Chat Completions" not in guidance
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.evaluate("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied'); } } })")
                await page.locator('#copy-client-key').click()
                await page.locator('#error-notice').wait_for(state='visible')
                await page.wait_for_timeout(200)
                toast_box = await page.locator('#error-notice').bounding_box()
                assert toast_box['x'] >= 0 and toast_box['x'] + toast_box['width'] <= 390
                assert toast_box['y'] >= 0
                await page.get_by_role('button', name='关闭提示').click()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await client_eye.click()
                assert await client_key.get_attribute('type') == 'text'
                await page.locator("#menu-toggle").click()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.locator('[data-view="overview"]').click()
                assert await client_key.input_value() == ''
                assert await client_key.get_attribute('type') == 'password'
                assert await page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
                assert await client_key.input_value() == ''
                assert await client_key.get_attribute('type') == 'password'
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.reload()
                await page.locator("#console-content").wait_for(state="visible")
                # A clock-skew failure must explain recovery, without retrying OAuth.
                app.state.console_state.login_state = "failed"
                app.state.console_state.login_code = "id_token_not_yet_valid"
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#login-state').textContent === '授权流程失败'")
                await page.locator("#menu-toggle").click()
                await page.locator('[data-view="authorization"]').click()
                assert "本机时间落后" in await page.locator("#login-result").text_content()
                assert "立即同步" in await page.locator("#login-result").text_content()
                assert "不会跳过" in await page.locator("#login-result").text_content()
                assert not any(url.endswith("/admin/api/login") for url in resources)
                # Manual fallback fetches an authenticated, short-lived link only on click.
                app.state.console_state.login_state = "running"
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => !document.querySelector('#show-login-link').disabled")
                manual_url = "https://auth.openai.com/api/accounts/authorize?state=test&nonce=test&code_challenge=test"
                async def manual_link(route):
                    import json
                    await route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"url": manual_url, "expires_in_seconds": 1}))
                await page.route("**/admin/api/login-link", manual_link)
                await page.locator("#show-login-link").click()
                await page.locator("#manual-login-link").wait_for(state="visible")
                assert await page.locator("#manual-login-link").get_attribute("href") == manual_url
                assert "noreferrer" in await page.locator("#manual-login-link").get_attribute("rel")
                await page.locator("#manual-login-link").wait_for(state="hidden")
                assert await page.locator("#manual-login-link").get_attribute("href") is None
                await page.unroute("**/admin/api/login-link", manual_link)
                # Logout is explicit and cancelled dialogs perform no mutation.
                app.state.console_state.login_state = "idle"
                app.state.console_state.login_code = None
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => !document.querySelector('#logout-account').disabled")
                logout_bodies = []
                async def fake_logout(route):
                    logout_bodies.append(route.request.post_data_json)
                    with store.transaction() as saved:
                        saved.pop("credentials", None)
                    await route.fulfill(status=200, content_type="application/json",
                        body='{"local_signed_out":true,"remote_revocation_confirmed":false,"revocation_state":"unconfirmed"}')
                await page.route("**/admin/api/logout", fake_logout)
                await page.locator("#logout-account").click()
                assert await page.locator("#logout-new-account").is_checked() is False
                await page.locator("#cancel-logout").click()
                assert not logout_bodies
                await page.locator("#logout-account").click()
                await page.locator("#logout-new-account").check()
                await page.locator("#confirm-logout").click()
                await page.wait_for_function("() => document.querySelector('#error-notice').textContent.includes('官方撤销未确认')")
                assert logout_bodies == [{"new_account": True}]
                assert store.verify_gateway_key(successor)
                assert await page.locator("#client-model-id").text_content() == "请先查询并选择实际模型 ID"
                await page.unroute("**/admin/api/logout", fake_logout)
                # An expired management response must not latch logoutBusy forever.
                async def expired_logout(route):
                    await route.fulfill(status=401, content_type="application/json",
                                        body='{"error":{"code":"admin_session_required"}}')
                await page.route("**/admin/api/logout", expired_logout)
                await page.locator("#logout-account").click()
                await page.locator("#confirm-logout").click()
                await page.locator("#locked-screen").wait_for(state="visible")
                assert not await page.locator("#logout-dialog").evaluate("dialog => dialog.open")
                await page.unroute("**/admin/api/logout", expired_logout)
                await page.locator("#session-retry").click()
                await page.locator("#console-content").wait_for(state="visible")
                await page.wait_for_function("() => !document.querySelector('#logout-account').disabled && !document.querySelector('#start-login').disabled")
                app.state.console_state.login_state = "idle"
                app.state.console_state.login_code = None
                # Canonical pending-token guidance must not suggest repeating OAuth.
                with store.transaction() as state:
                    state["pending_tokens"] = {"isolated_test": True}
                    state["login_required"] = True
                await page.locator("#refresh-status").click()
                await page.wait_for_function("() => document.querySelector('#metric-account').textContent === '待验证'")
                assert await page.locator("#setup-account-action").get_attribute("data-go") == "models"
                assert "凭据已加密保留" in await page.locator("#metric-account-note").text_content()
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
