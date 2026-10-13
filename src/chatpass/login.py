"""Bounded one-shot browser login on an ephemeral IPv4 loopback callback."""

from __future__ import annotations

import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import portalocker

from .credentials import Credentials
from .oauth import LoginAttempt, OAuthClient, OAuthError, parse_callback
from .storage import StateStore


class _HTTPServer(HTTPServer):
    deadline = float("inf")
    request_timeout = 1.0

    def finish_request(self, request, client_address):
        # Socket read timeouts are idle-only; enforce a hard handler deadline too.
        remaining = max(0, min(self.request_timeout, self.deadline - time.monotonic()))

        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        try:
            super().finish_request(request, client_address)
        finally:
            timer.cancel()
            timer.join()

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(1)
        return sock, address

    def handle_error(self, request, client_address):
        # Base server would print raw request/exception state into stderr.
        pass


class CallbackServer:
    def __init__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                status, text = 400, "Invalid callback."
                if (
                    self.headers.get_all("Host") == [owner.host_header]
                    and parts.path == "/auth/callback"
                    and not parts.scheme
                    and not parts.netloc
                    and not parts.fragment
                    and owner.result is None
                    and owner.error is None
                    and owner.attempt is not None
                    and time.monotonic() < owner.http.deadline
                ):
                    try:
                        owner.result = parse_callback(
                            parts.query, owner.attempt, owner.saved_client
                        )
                        status, text = 200, "Authorization received. You can close this window."
                    except OAuthError as exc:
                        if exc.code == "consent_denied":
                            owner.error = exc
                content = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(content)

        self.http = _HTTPServer(("127.0.0.1", 0), Handler)
        self.http.timeout = 0.1
        self.host_header = "127.0.0.1:" + str(self.http.server_port)
        self.origin = "http://" + self.host_header
        self.redirect_uri = self.origin + "/auth/callback"
        self.attempt = None
        self.saved_client = None
        self.result = None
        self.error = None

    def configure(self, attempt: LoginAttempt, saved_client: str | None):
        self.attempt, self.saved_client = attempt, saved_client

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.server_close()

    def wait(self, timeout: float = 180) -> tuple[str, str]:
        self.http.deadline = time.monotonic() + timeout
        while self.result is None and self.error is None:
            remaining = self.http.deadline - time.monotonic()
            if remaining <= 0:
                raise OAuthError("login_timeout")
            self.http.timeout = min(0.1, remaining)
            self.http.handle_request()
        if self.error is not None:
            raise self.error
        return self.result


def login(store: StateStore, *, open_browser: bool = True, emit=print, timeout=180,
          on_authorization=None) -> None:
    store.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with portalocker.Lock(str(store.directory / "login.lock"), timeout=0):
            with store.transaction() as state:
                if "pending_tokens" in state:
                    raise OAuthError("pending_token_validation")
                registration = state.setdefault(
                    "registration", {"host_id": "urn:uuid:" + str(uuid.uuid4())}
                )
                host_id = registration["host_id"]
                saved_client = registration.get("client_id")
                current = state.get("credentials") or state.get("signed_in_identity", {})
                hint = current.get("id_token") if current.get("client_id") == saved_client else None
            if not current:
                emit("ChatPass requests use your shared ChatGPT plan budget; subscription is not unlimited.")
                emit("Manage usage: https://chatgpt.com/settings/usage. No automatic paid API fallback.")
            oauth = OAuthClient()
            try:
                with CallbackServer() as server:
                    attempt = LoginAttempt.create(server.redirect_uri)
                    server.configure(attempt, saved_client)
                    manual_url = oauth.authorization_url(attempt, saved_client, host_id)
                    if on_authorization is not None:
                        on_authorization(manual_url, time.monotonic() + timeout)
                    url = (oauth.authorization_url(attempt, saved_client, host_id, id_token_hint=hint)
                           if hint and open_browser else manual_url)
                    try:
                        opened = open_browser and webbrowser.open(url)
                    except Exception:
                        opened = False
                    if not opened:
                        emit("Open this authorization URL in your browser:")
                        emit(manual_url)
                    else:
                        emit("Complete authorization in your browser (timeout: 180 seconds).")
                    code, client_id = server.wait(timeout)
                # Persist callback-issued registration before the single-use exchange.
                with store.transaction() as state:
                    state["registration"]["client_id"] = client_id
                body = oauth.exchange(attempt, code, client_id)
                Credentials(store, oauth).accept_login(body, client_id, attempt.nonce)
            finally:
                oauth.close()
    except portalocker.exceptions.LockException:
        raise OAuthError("login_already_running") from None
