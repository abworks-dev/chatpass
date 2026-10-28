"""Opt-in Uvicorn loop selection; no global policy or stdlib mutation."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from test_storage import MemoryKeyring

from chatpass.cli import main
from chatpass.storage import StateStore


@pytest.mark.parametrize("command", ["serve", "web"])
@pytest.mark.parametrize("choice", [None, "auto", "selector"])
def test_cli_event_loop_selection(tmp_path, monkeypatch, command, choice):
    import sys

    import uvicorn

    if choice == "selector" and not callable(getattr(uvicorn.Config, "get_loop_factory", None)):
        pytest.skip("Explicit Selector requires Uvicorn 0.36+")
    store = StateStore(tmp_path, backend=MemoryKeyring())
    store.rotate_gateway_key()
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: store)
    captured = []
    app = SimpleNamespace(state=SimpleNamespace(console_bootstrap_token="test-bootstrap"))
    monkeypatch.setattr("chatpass.gateway.create_app", lambda *a: app)
    monkeypatch.setitem(sys.modules, "chatpass.console", SimpleNamespace(create_console_app=lambda *a: app))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: captured.append(kw["loop"]))

    class Server:
        def __init__(self, config):
            captured.append(config.loop)

        def run(self):
            pass

    monkeypatch.setattr("uvicorn.Server", Server)
    args = ["--data-dir", str(tmp_path), command]
    if command == "web":
        args.append("--no-browser")
    if choice is not None:
        args.extend(["--event-loop", choice])
    policy = asyncio.get_event_loop_policy()
    assert main(args) == 0
    assert captured == ["asyncio:SelectorEventLoop" if choice == "selector" else "auto"]
    assert asyncio.get_event_loop_policy() is policy


@pytest.mark.parametrize("command", ["serve", "web"])
def test_selector_rejects_unsupported_uvicorn_before_storage(monkeypatch, capsys, command):
    monkeypatch.delattr("uvicorn.Config.get_loop_factory", raising=False)
    monkeypatch.setattr("chatpass.cli.StateStore", lambda _: pytest.fail("must not read credentials"))
    assert main([command, "--event-loop", "selector"]) == 1
    assert "requires Uvicorn 0.36 or newer" in capsys.readouterr().err


def test_explicit_selector_factory_resolves_without_changing_policy():
    import uvicorn

    if not callable(getattr(uvicorn.Config, "get_loop_factory", None)):
        pytest.skip("Explicit Selector requires Uvicorn 0.36+")
    policy = asyncio.get_event_loop_policy()
    factory = uvicorn.Config("unused:app", loop="asyncio:SelectorEventLoop").get_loop_factory()
    assert factory is asyncio.SelectorEventLoop
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
    assert asyncio.get_event_loop_policy() is policy


@pytest.mark.parametrize("tls", [False, True])
def test_selector_serves_streaming_socket_and_tls_without_callback_errors(tmp_path, tls):
    """Actual local HTTP/TLS on Selector, independent of the pytest event loop."""
    import ipaddress
    import ssl
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    server_ssl = None
    client_ssl = True
    if tls:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .sign(key, hashes.SHA256()))
        cert_path, key_path = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ssl.load_cert_chain(cert_path, key_path)
        client_ssl = ssl.create_default_context(cafile=str(cert_path))

    async def exercise():
        callback_errors = []
        loop = asyncio.get_running_loop()
        assert isinstance(loop, asyncio.SelectorEventLoop)
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: callback_errors.append(context))
        closed = asyncio.Event()

        async def handle(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
                await writer.drain()
                writer.write(b": ping\n\n")
                await writer.drain()
                writer.write(b'data: {"type":"response.completed"}\n\n')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ssl)
        try:
            port = server.sockets[0].getsockname()[1]
            scheme = "https" if tls else "http"
            async with httpx.AsyncClient(verify=client_ssl, trust_env=False, timeout=3) as client:
                async with client.stream("GET", f"{scheme}://127.0.0.1:{port}/") as response:
                    body = b"".join([chunk async for chunk in response.aiter_bytes()])
            assert response.status_code == 200 and b"response.completed" in body
            await asyncio.wait_for(closed.wait(), 3)
            assert callback_errors == []
        finally:
            server.close()
            await server.wait_closed()
            loop.set_exception_handler(previous)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(asyncio.wait_for(exercise(), 5))
