# ChatPass

**English** | [简体中文](README.zh-CN.md)

**Connect your agent tools to your ChatGPT subscription.**

A local-first Responses gateway. Clients receive a ChatPass access key; upstream OAuth credentials stay on the gateway.

> **Status: initial implementation with mock testing, not verified against a real account or installed clients.** Account eligibility, quota accounting and Hermes/OpenClaw/DeepSeek Harness compatibility remain unverified. Remote deployment is not supported in this version.

## Implemented boundary

- Public browser authorization with PKCE, state/nonce, dynamic client ID and signed OIDC identity validation.
- AES-GCM state encryption with a platform-keyring master key; no plaintext fallback.
- Single-account refresh with cross-process locking and durable rotation checkpoints, never automatic retries of one-time code/refresh POSTs.
- Separate gateway keys, explicit creation/rotation, authenticated model discovery and streaming Responses.
- Bounded bodies, concurrency, timeouts and streams; sanitized errors.
- No imported tool credentials, account pooling, subscription resale, provider aggregation, Chat Completions adapter or separately billed fallback.

Mock testing establishes implementation behavior, not upstream eligibility, official approval or client compatibility. See [verification](docs/verification.md).

## Local quick start

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and a supported secure platform keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service/KWallet).

```powershell
uv sync --locked
uv run chatpass key init
uv run chatpass login
uv run chatpass status
uv run chatpass serve
```

Save the gateway key printed once by `key init` in your own clients. `login` opens your browser for user-controlled official consent with a 180-second timeout. `login --no-browser` prints the authorization link. `status` never displays OAuth tokens. Keep the serving terminal running.

```text
Base URL: http://127.0.0.1:19840/v1
API Key: the cp_ key printed by key init / key rotate
Model: an id returned by GET /v1/models
API: native streaming Responses
```

This is **not an OpenAI API key**; it works only through the running gateway. Never paste upstream tokens into clients.

`uv run chatpass key rotate` immediately invalidates the old client key. A non-secret data location and port can be selected with `uv run chatpass --data-dir D:\ChatPassData serve --port 19841`; use the same data directory for all commands. On Windows the default is `%LOCALAPPDATA%\ChatPass`.

## Protocol limitations

The verified HTTP preview requires `stream:true` and `store:false`, full input history rather than `previous_response_id`, and explicit rejection of unsupported fields/hosted tools. String input becomes a user item; function/custom tools and their explicit results are preserved. `system` history items are unsupported; use supported `instructions`/`developer` semantics. Nonstream requests, `temperature`, `max_output_tokens` and other unsupported preview fields return a clear 400 instead of silently changing meaning.

The gateway exposes only `GET /v1/models` and `POST /v1/responses` for inference. The model catalog is normalized from upstream `models/slug/visibility` to `data/id`. There is no public authorization management API.

See [protocol evidence and licensing](docs/protocol-notes.md) and [client setup/verification](docs/clients.md). All three target clients remain **not tested** against a real account; clients that only support Chat Completions cannot use this initial release.

## Recovery and security

- Secure-keyring failures stop persistence; fix the system backend rather than using plaintext.
- Login timeout/cancellation: run `login` again. Do not post callback URLs or credentials.
- Ambiguous rotating-refresh results or a crash during refresh: re-login; the predecessor is never replayed.
- Received replacement tokens survive temporary identity-verification failures in encrypted quarantine; they are not usable before validation.
- Upstream 401/403/429: investigate authorization, eligibility or limits. Inference is not automatically replayed or sent to a billed API.
- Literal loopback-only binding is enforced. **Do not expose it using a reverse proxy or tunnel.** Public deployment needs separate TLS, rate limiting, admin isolation and multi-user hardening.
- This does not isolate secrets from malware running as the same OS user. Treat gateway keys as passwords.

A ChatGPT subscription is not a universal or unlimited API plan. Models, eligibility, supported flows and limits depend on upstream service behavior.

## Development

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv build
```

Configure a correct system certificate chain if your package-index proxy requires one; uv's `--system-certs` option may be appropriate on managed machines. Do not disable certificate verification.

[Design](docs/design.md) · [Implementation plan](docs/implementation-plan.md) · [Work checkpoint](docs/work-status.md)

## Licensing and affiliation

ChatPass is not affiliated with or endorsed by OpenAI. Use must comply with service terms and dependency licenses.

**A project license has not yet been selected.** Public visibility alone grants no open-source license. The official DevKit uses a custom noncommercial license; its code has not been copied into this independent implementation. Protocol interoperability is not permission for commercial use or remote hosting.
