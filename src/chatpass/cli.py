"""Explicit local operations; no account credentials are printed by this CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import Settings, default_data_dir
from .credentials import Credentials
from .login import login
from .oauth import OAuthError
from .storage import StateStore, StorageError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="chatpass")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("key", help="Create or rotate a local gateway key (shown once)")
    key.add_argument("action", choices=["init", "rotate"])
    signin = commands.add_parser("login", help="Authorize through the official browser flow")
    signin.add_argument("--no-browser", action="store_true")
    commands.add_parser("status", help="Display only non-secret configuration status")
    serve = commands.add_parser("serve", help="Serve the loopback Responses gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=19840)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            settings = Settings(data_dir=args.data_dir, host=args.host, port=args.port)
        store = StateStore(args.data_dir)
        if args.command == "key":
            issue = store.initialize_gateway_key if args.action == "init" else store.rotate_gateway_key
            print(issue())
        elif args.command == "login":
            login(store, open_browser=not args.no_browser)
            print("Authorization saved securely.")
        elif args.command == "status":
            state = store.read() if store.path.exists() else {}
            credentials = state.get("credentials", {})
            print(
                json.dumps(
                    {
                        "gateway_key_configured": bool(state.get("gateway_key_hash")),
                        "account_authorized": bool(credentials)
                        and not state.get("login_required", False)
                        and not state.get("refresh_in_progress", False),
                        "access_token_expired": bool(credentials)
                        and credentials.get("expires_at", 0) <= time.time(),
                        "pending_token_validation": "pending_tokens" in state,
                        "login_required": bool(
                            state.get("login_required") or state.get("refresh_in_progress")
                        ),
                        "registration_configured": bool(
                            state.get("registration", {}).get("client_id")
                        ),
                    },
                    indent=2,
                )
            )
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
