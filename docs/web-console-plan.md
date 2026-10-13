# Local Web console implementation plan

## Intent and baseline

Requested: add a local Web management console by adapting an open-source template. Parent product boundary: personal, loopback-only, single-account ChatPass; real account and client compatibility remain unverified. Baseline: `main`, `3504947`, clean. This plan and the existing work-status checkpoint carry continuation state.

## Template decision and evidence

Collection: GitHub `gh api` and npm registry with Python/httpx, 2026-10-13; two template repository queries, one release/tag/license query chain, one exact npm package inspection. Tabler and AdminLTE both report MIT. Choose Tabler's framework-neutral dashboard CSS to avoid introducing a Node application/build chain into the Python package. No third-party dashboard JavaScript, paid template, analytics, remote fonts or CDN.

- Source: https://github.com/tabler/tabler/tree/ec33733290bd0f314ca19f6be58bc69a6ab3e4fa
- License: https://github.com/tabler/tabler/blob/ec33733290bd0f314ca19f6be58bc69a6ab3e4fa/LICENSE — MIT; retain complete notice.
- Release: `@tabler/core@1.6.1`; annotated tag resolves to the source commit above.
- Exact asset package: https://registry.npmjs.org/@tabler/core/-/core-1.6.1.tgz
- Integrity: `sha512-lgmR9gcxRp5JlLxcmRi9Rz85JrEvH6Bz71KnlcPVSa97wtwQNjgSs3g3fxwbAlulHyRMghhIV04QUuW3pqDsIg==`
- Selected asset: `package/dist/css/tabler.min.css`; inspected CSS URL references contain no external resources. Preserve original file bytes and header. Do not vendor unrelated package libraries with different licenses.

Tabler's MIT license covers the vendored template and retains its original authors' notice. ChatPass subsequently adopted its own MIT license for original code and documentation; project and Tabler notices remain separate (see `LICENSE` and `THIRD_PARTY_NOTICES.md`).

## Scope and UI behavior

`chatpass web [--host 127.0.0.1] [--port 19840] [--no-browser]` runs the inference gateway and console together. It can start before a gateway key exists, so first-time users can initialize it in the UI. Existing `serve` remains API-only and preserves its key prerequisite.

Chinese-first responsive Tabler layout: overview, authorization, gateway key, live model discovery and client setup. Show actual sanitized status, uptime and in-process gateway request counts, never invented quota or compatibility badges. Authorization starts only on a user click and opens the existing official browser flow on the server. The UI exposes running/succeeded/failed state and, on an explicit authenticated request, a short-lived hint-free continuation URL for an active login. Callback codes and access/refresh/ID tokens never go to the UI; continuation links are excluded from ordinary status and cleared on expiry, completion or leaving the authorization page. Single in-flight login, existing 180-second callback deadline, no silent retries. Gateway key creation/rotation requires explicit action; rotation confirmation warns that old clients immediately lose access. Generated keys are stored in the existing AES-GCM encrypted state alongside their authentication hash. Authenticated management sessions can retrieve the current key through the no-store `GET /admin/api/key` endpoint; status responses never include it. Gateway-key and client-setup pages load it automatically, mask it by default, and offer reveal/copy controls. Browser copies are cleared on navigation, tab hiding and session loss, with no browser persistence. Legacy hash-only keys remain valid and are never implicitly rotated; retrieving their plaintext requires an explicit confirmed rotation. Initializing an existing key fails, never rotates implicitly. Model queries are explicit upstream reads; no background inference or quota consumption. Client instructions distinguish Responses from Chat Completions and retain unverified client-version status.

## Management security contract

- Literal loopback Host and loopback client validation; reject DNS rebinding Host and cross-origin requests. No CORS. No external scripts or resources. Strict same-origin CSP, no framing, no referrer, no-store management responses.
- CLI creates a high-entropy, five-minute, single-use bootstrap capability, prints/opens an HTTP loopback URL with the capability in the fragment (never server access logs/query). JavaScript immediately removes the fragment, posts it to `POST /admin/api/session`, then discards it. Session token never appears in a JSON response: HttpOnly, SameSite=Strict cookie restricted to `/admin`, eight-hour lifetime, memory-only and invalidated on process restart. CLI disables access logs.
- `GET /admin/api/session` returns CSRF token to an authenticated same-origin page. All unsafe operations require exact Origin and CSRF header `X-ChatPass-CSRF`; bootstrap requires exact Origin plus bounded JSON. Cookies confer management authority; inference Bearer keys do not. No recovery by guessing local IP, using gateway key, or bypassing a missing keyring.
- Browser renders remote model names with textContent, not HTML. No secrets in localStorage/sessionStorage, status, errors, request counters or logs. Static login shell contains no account state. Bodies bounded to 4096 bytes and model responses remain bounded by the canonical gateway owner.
- Startup does not authorize an account or import other tools' credentials. No logout/account erasure, public deployment, config editing, process management or remote-browser support in this slice.

## Interface for independent implementation

`create_console_app(settings, store, credentials, *, client=None, login_runner=None, bootstrap_token=None) -> FastAPI` composes the existing gateway. Credentials and store stay canonical. `app.state.console_bootstrap_token` is CLI-only; frontend never reads it except fragment exchange. Optional dependencies are test seams, not alternative runtime credential owners.

Admin JSON endpoints, protected as above:

- `POST /admin/api/session` body `{token}`; sets cookie and returns `{csrf_token}`. `GET` returns `{csrf_token}` or 401.
- `GET /admin/api/status`: `gateway_key_configured`, `account_authorized`, `access_token_expired`, `pending_token_validation`, `login_required`, `registration_configured`, plus `base_url`, `uptime_seconds`, `requests_total`, `requests_failed`, `requests_active`, and `login: {state, code}`. No subject/email/token/data-directory fields. Storage failures return sanitized error code, not false healthy status.
- `POST /admin/api/key` body `{action: "init"|"rotate"}` returns `{key}`; 409 for init with existing key.
- `POST /admin/api/login` returns 202 `{state:"running"}`; 409 when already running. Worker uses existing `login(store, emit=noop)`; failures return safe fixed codes only in subsequent status. Request does not wait for browser consent.
- `GET /admin/api/models` returns canonical `{object:"list",data:[...]}`; explicit request with one in-flight admission and safe upstream errors. Coordinator extracts the existing gateway model helper `fetch_model_catalog(credentials, client, *, timeout, body_limit)` so there is one catalog protocol owner, no local HTTP self-proxy and no temporary API key.

## Ownership, tests and stop criteria

Coordinator: CLI, shared gateway helper, vendor files/notices, package, integration, documentation and Git. Backend delegate: console.py and test_console.py only. Frontend delegate: static/index.html, console.css, console.js only. No delegate Git changes.

Verification: focused security/session/Host/Origin/CSRF/body-limit/login-admission/key/status/model tests; existing gateway regression suite; actual headless Edge with mocked upstream and isolated fake storage, desktop/mobile screenshots, no external resource calls or browser secrets, one-time key behavior and truthful failure states; wheel includes static resources/license. Independent read-only review before commit. No real OpenAI login or inference without user-controlled consent. Stop for unresolved security review or license mismatch; otherwise complete this local UI slice, not official-service acceptance.
