<p align="center">
  <img src="src/chatpass/static/brand/chatpass-icon.svg" width="96" height="96" alt="ChatPass conversation pass icon">
</p>

# ChatPass

[简体中文](README.md) | **English**

**Connect your agent tools to your ChatGPT subscription.**

ChatPass is a local gateway for personal use. It uses the public Sign in with ChatGPT authorization flow to connect your own agent tools to subscription capabilities within the permissions granted. Clients need only a local API address and a separate gateway key, not your ChatGPT OAuth credentials.

> **Status: local mock validation is complete. Historical limited authorized diagnostics and user feedback established fresh DSH conversation success; full live acceptance of the updated version remains incomplete, and Hermes/OpenClaw are untested.** Account eligibility, available models and quota depend on upstream authorization. A subscription is not unlimited usage or a general-purpose API entitlement. See [verification scope](docs/verification.md).

## Quick start

### 1. Get the project and prepare your environment

Download and extract a ZIP from the [GitHub repository](https://github.com/abworks-dev/chatpass), or clone it:

```powershell
git clone https://github.com/abworks-dev/chatpass.git
cd chatpass
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and confirm that `uv --version` works in your terminal.

- Python 3.11+ is required; uv can download a suitable version if necessary.
- A secure OS credential store is required: Windows Credential Manager, macOS Keychain, or Linux Secret Service/KWallet. Without one, credential persistence fails closed rather than falling back to plaintext files.
- First startup may download Python and runtime dependencies. Authorization and model requests also require internet access.

### 2. Start the local management console

**Recommended on Windows:** double-click [start.cmd](start.cmd) in the project directory. It needs no administrator privileges, registers no background service and does not automatically authorize an account.

On other platforms, or if you prefer a terminal, run from the project directory:

```powershell
uv run --locked --no-dev --system-certs chatpass web
```

The browser opens when the service is ready. Keep the terminal running; press `Ctrl+C` to stop. uv prepares the runtime from the lockfile, so an initial `uv sync` is unnecessary. `--system-certs` uses the system trust store without disabling TLS verification.

To change the port or open the management link printed in the terminal manually:

```powershell
uv run --locked --no-dev --system-certs chatpass web --port 19841 --no-browser
```

The Windows launcher also accepts `--port 19841 --no-browser`, but forwards only `web` arguments. Use the CLI directly for other subcommands and global options.

For `WinError 10054` in Windows Proactor connection cleanup, you can opt into `start.cmd --event-loop selector` for diagnosis (the default remains `auto`). This does not fix explicit upstream error events. See the [stream failure and connection cleanup guide](docs/stream-failure-troubleshooting.md).

### 3. Authorize and get connection details

1. Create a key under **Gateway key**, then copy it and store it securely.
2. Click sign in under **Account authorization** and confirm authorization on the official page.
3. Query **Model catalog** for actually available models, then configure your agent tools.

The console can start before authorization or key creation. Its overview request counters reset on restart and **are not official remaining quota**.

<details>
<summary>Optional: use the CLI without the management console</summary>

Run these commands from the project directory:

```powershell
uv run --locked --no-dev --system-certs chatpass key init
uv run --locked --no-dev --system-certs chatpass login
uv run --locked --no-dev --system-certs chatpass status
uv run --locked --no-dev --system-certs chatpass serve
```

`key init` generates and encrypts the key locally; authenticated console sessions can view or copy it later, masked by default. Legacy hash-only keys remain valid but cannot be recovered; rotate explicitly if lost. `login` opens the official authorization page; finish within 180 seconds, or use `login --no-browser` to open the authorization link yourself. `status` displays only non-sensitive state.

`serve` runs only the API; `web` runs both the console and API. They share the default port, so **run one or the other**. Existing CLI authorization and keys also work with the console.

</details>

## Client configuration

In an agent tool supporting a custom API address, set:

```text
Base URL: http://127.0.0.1:19840/v1
API Key: the cp_ key created in the console or by key init
Model: a data[].id from the model catalog or GET /v1/models
API: native streaming Responses
```

If you changed the startup port, update the API address too. The gateway key **is not an OpenAI API key or OAuth token**: it works only through the running ChatPass gateway. Do not paste subscription credentials into clients or assume a model name without querying the catalog.

The current source supports streaming/nonstreaming Responses and Chat Completions; prefer native streaming Responses. Limited authorized probes and user feedback established fresh DSH conversation success, not universal client or protocol acceptance. Completed-item reconstruction for empty terminal output now has local mock coverage; updated live acceptance remains pending. Verify Hermes, OpenClaw and DSH versions, histories and tool turns separately; see [client acceptance](docs/clients.md).

## How it differs from subscription relay platforms

**Personal use, one account, on your own machine; no account pools, quota resale, multi-user relay or provider aggregation.** ChatPass is still a gateway/proxy. The distinction is not whether it uses OAuth, but its authorization path, upstream interface and audience.

| Dimension | ChatPass | Approaches other subscription proxies may use |
| --- | --- | --- |
| Authorization | Sign in with ChatGPT under its own app name, requesting `chatgpt.tokens.use.direct` plan usage permission; no imported tool credentials | OAuth flows for tools such as Codex, or imported tool credentials |
| Upstream | Public `https://api.openai.com/v1/models` and `/v1/responses` | Some adapt the private Codex backend; a client-facing `/v1` address does not identify the upstream |
| Audience | One person connecting their own local tools | Some are also personal proxies; others target multiple accounts, user key distribution and billing operations |

For example, [Sub2API](https://github.com/Wei-Shaw/sub2api) targets subscription quota distribution and management, including multiple accounts, user keys, billing and scheduling. ChatPass does not target that operating model. This does not imply that all subscription proxies use private interfaces, or constitute a blanket judgment about their security or legality. Public SIWC is not an exclusive differentiator; this project prioritizes personal, local, single-account use and conservative credential lifecycle handling. See [protocol notes](docs/protocol-notes.md) for evidence and technical details.

## Limitations and security

### API limitations

The current source provides `GET /v1/models`, `POST /v1/responses` and `POST /v1/chat/completions`. It is not a complete OpenAI compatibility layer, a publication claim or universal live-client acceptance.

- Both inference interfaces can stream or aggregate JSON. Upstream always uses `stream:true`, `store:false`; only `response.completed` proves success.
- Send full history. `previous_response_id`, background work, server-state references, file search, Code Interpreter and hosted MCP are unsupported. Native Responses permits web-search requests subject to model and account/workspace policy.
- Native Responses preserves function/custom tools, namespaces, `additional_tools` and opaque reasoning history. Chat supports a narrower ordinary-function subset and explicitly rejects namespaced/custom tools. `system` is normalized to `developer`.
- Default compatible mode omits listed unsupported tuning fields and reports names in `X-ChatPass-Ignored-Fields`; omitted output limits are not enforced. Choose `--compatibility-mode strict` to reject them.

Empty terminal `output` is reconstructed from contiguous `response.output_item.done` snapshots, still requiring successful `response.completed`. Conflicts, duplicate identities, missing items and interrupted streams cannot establish success. Local regressions cover this repair; updated live upstream/client acceptance remains pending. Native `reasoning`, `text` and `include` fields are preserved; general API documentation alone does not justify enabling SIWC-restricted tuning.

### Credential and access security

- OAuth credentials are encrypted locally; the OS secure credential store protects the master key. Do not manually delete the master key: existing encrypted state would become unreadable.
- **Loopback access only. Do not expose the service through reverse proxies, tunnels or port forwarding.** This version lacks the TLS, multi-user isolation and quota management needed for public deployment.
- Management entry links are single-use and expire after five minutes; do not share or screenshot them. Management sessions expire after eight hours or a restart. Restart `web` to get a new entry link. Gateway keys cannot sign in to the console. See [console documentation](docs/web-console.md).
- Rotate the key with `uv run chatpass key rotate`. The old key stops working immediately, so update clients; this does not rotate upstream account credentials.
- Use the same data directory for all commands. Windows defaults to `%LOCALAPPDATA%\ChatPass`. Example override: `uv run chatpass --data-dir D:\ChatPassData serve --port 19841`.
- ChatPass does not isolate malware running as the same OS user. Never publish keys, tokens, callback links or full conversations.

### Sign-in, sign-out and recovery

Requests consume the budget shared with your ChatGPT plan, not unlimited API access. The console asks for first-use confirmation; actual permissions are granted on the official page. [Official usage](https://chatgpt.com/settings/usage) may reflect plan or app limits; local request counters are not token or billing quota.

- Browser not opening: explicitly fetch a short-lived continuation link while login runs. It is management-session-only, cleared on expiry/completion, and contains no identity token; still do not share it.
- Signed in without plan permission: only verified identity is retained encrypted, never used for inference or refresh. Reauthorization is your choice; failed candidates do not replace a valid saved grant.
- Sign out: confirm in the console or run `uv run chatpass logout`. After attempting official revocation, local credentials are cleared; the gateway key, registration and account binding remain. If remote revocation is unconfirmed, disconnect in ChatGPT Settings. Admitted requests cannot be recalled.
- Change account: explicitly use `uv run chatpass logout --new-account` or the console checkbox. Reset client/account binding while keeping the host ID and gateway key, then sign in manually.
- `refresh_not_yet_allowed`: wait for the official refresh window and refresh status for updated timing. Validating a pending login candidate is not blocked by the old token's refresh window.


- Authorization timeout or cancellation: run `login` again.
- An uncertain refresh result or process exit during refresh: stop requests and sign in again to avoid reusing a rotated token. A durably saved token pending validation is only revalidated; the old refresh request is not repeated.
- Upstream 401/403/429: check authorization, account eligibility and quota. ChatPass does not automatically switch to a separately billed API or replay inference requests.
- Installation TLS errors: configure the correct system certificates and use `--system-certs` where necessary; do not disable verification.

## Development and licensing

### Development

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv build
```

Further reading: [design](docs/design.md) · [implementation plan](docs/implementation-plan.md) · [work status](docs/work-status.md) · [protocol notes](docs/protocol-notes.md) · [verification](docs/verification.md).

### License and disclaimer

ChatPass is not affiliated with OpenAI, an official OpenAI product or an approved partner integration. **A public protocol is not official endorsement; successful OAuth sign-in does not itself grant plan usage permission.** Actual upstream authorization and applicable terms govern use. Paid or remotely hosted apps require separate verification of OpenAI's access requirements.

Original ChatPass code and documentation use the [MIT License](LICENSE), allowing use, modification, distribution, commercial use and closed-source redistribution with copyright and license notices retained. The software is provided as-is without warranty. **MIT grants software rights, not rights to OpenAI services or trademarks.**

The console uses Tabler 1.6.1 (MIT), bundled locally without a CDN, remote fonts or telemetry. See [third-party notices](THIRD_PARTY_NOTICES.md) for license and copyright notices. Python dependencies retain their own licenses. The official DevKit has a separate custom non-commercial license; this implementation does not copy or bundle its code, documentation or assets.
