"""Explicit local operations; no account credentials are printed by this CLI."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from ipaddress import ip_address
from pathlib import Path

from .config import Settings, default_data_dir
from .credentials import Credentials
from .login import login
from .oauth import OAuthError
from .storage import StateStore, StorageError


def _open_console_when_ready(server, url: str) -> None:
    """Open only after our own server listens, not after a port belonging to another app."""
    deadline = time.monotonic() + 15
    waiter = threading.Event()
    while not server.started and not server.should_exit and time.monotonic() < deadline:
        waiter.wait(0.05)
    if server.started and not server.should_exit:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # The explicit terminal URL remains the recovery path.


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="chatpass")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("key", help="Create or rotate a local gateway key (shown once)")
    key.add_argument("action", choices=["init", "rotate"])
    signin = commands.add_parser("login", help="Authorize through the official browser flow")
    signin.add_argument("--no-browser", action="store_true")
    signout = commands.add_parser("logout", help="Sign out locally and attempt official revocation")
    signout.add_argument("--new-account", action="store_true",
                         help="Also reset the issued registration for a different account")
    commands.add_parser("status", help="Display only non-secret configuration status")
    serve = commands.add_parser("serve", help="Serve the loopback Responses gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=19840)
    web = commands.add_parser("web", help="Run the local management console and gateway")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=19840)
    web.add_argument("--no-browser", action="store_true")
    for command in (serve, web):
        command.add_argument("--compatibility-mode", choices=("compatible", "strict"),
                             default="compatible", help="Filter unsupported tuning fields or reject them")
    args = parser.parse_args(argv)
    try:
        if args.command in ("serve", "web"):
            settings = Settings(data_dir=args.data_dir, host=args.host, port=args.port,
                                compatibility_mode=args.compatibility_mode)
        store = StateStore(args.data_dir)
        if args.command == "key":
            issue = store.initialize_gateway_key if args.action == "init" else store.rotate_gateway_key
            print(issue())
        elif args.command == "login":
            login(store, open_browser=not args.no_browser)
            print("Authorization saved securely.")
        elif args.command == "logout":
            credentials = Credentials(store)
            try:
                result = credentials.logout(new_account=args.new_account)
            finally:
                credentials.oauth.close()
            print(json.dumps(result, indent=2))
            if result["revocation_state"] == "unconfirmed":
                print("Remote revocation is unconfirmed. Disconnect ChatPass in ChatGPT Settings.")
            print("Already admitted requests cannot be recalled.")
        elif args.command == "status":
            state = store.read() if store.path.exists() else {}
            from .console import credential_status

            print(json.dumps(credential_status(state), indent=2))
        elif args.command == "web":
            import uvicorn

            from .console import create_console_app

            credentials = Credentials(store)
            try:
                app = create_console_app(settings, store, credentials)
                address = ip_address(settings.host)
                host = f"[{address}]" if address.version == 6 else str(address)
                url = f"http://{host}:{settings.port}/#token={app.state.console_bootstrap_token}"
                print("Local management link (private, single use, expires in five minutes):")
                print(url)
                server = uvicorn.Server(uvicorn.Config(
                    app, host=settings.host, port=settings.port,
                    access_log=False, log_level="warning"))
                if not args.no_browser:
                    threading.Thread(target=_open_console_when_ready,
                                     args=(server, url), daemon=True).start()
                server.run()
            finally:
                credentials.oauth.close()
        else:
            if not store.path.exists() or not store.read().get("gateway_key_hash"):
                raise StorageError("Run chatpass key init before starting the gateway.")
            import uvicorn

            from .gateway import create_app

            credentials = Credentials(store)
            try:
                uvicorn.run(
                    create_app(settings, credentials, store.verify_gateway_key),
                    host=settings.host,
                    port=settings.port,
                    access_log=False,
                    log_level="warning",
                )
            finally:
                credentials.oauth.close()
        return 0
    except (StorageError, OAuthError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception:
        print(
            "Operation failed. Check secure storage, configuration and network availability.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
