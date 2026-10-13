# ChatPass initial implementation plan

## Goal and authority

Implement the approved local-first gateway described in `docs/design.md`, plus a Chinese README. The user authorized starting implementation after approving the product scope and repository creation. Initial Git baseline: `main`, `74a3081`; no pre-existing source code or unrelated modifications.

A working mock-tested gateway is not evidence of subscription entitlement or compatibility with installed clients. Real login is a user-controlled operation. No existing Codex/OpenCode credentials will be imported or exposed.

## Architecture and stack

Python 3.11+, FastAPI/uvicorn for HTTP serving, httpx for the upstream transport, PyJWT/cryptography for signed identity validation and encrypted state, keyring for platform master-key storage, portalocker for process-safe state transactions. pytest and pytest-asyncio provide focused tests.

The HTTP transport preserves Responses event payloads, with a bounded framing observer to require a terminal outcome rather than accepting interrupted EOF as success. Official HTTP preview constraints require full input history instead of previous_response_id, explicit rejection of unsupported fields, stream:true and model-catalog normalization from models/slug to data/id. This preserves tool and reasoning payloads and avoids a Chat Completions translation layer. The protocol is implemented independently from documented upstream behavior; no noncommercial DevKit source is copied. Reuse dependency libraries for cryptography, locking, transport and validation rather than inventing them.

## Baseline and compatibility

- Product boundary: `docs/design.md` and bilingual README.
- Protocol evidence: official Sign-in with ChatGPT documentation and pinned DevKit source, recorded in [protocol notes](protocol-notes.md). Independent implementation; no DevKit source copied.
- Client contract: authenticated `POST /v1/responses`, `GET /v1/models`; fixed gateway key independent of upstream OAuth.
- Runtime: loopback-only defaults. Reject non-loopback binding in this initial release instead of implying server hardening is implemented. Keep configuration/storage replaceable for future deployment.
- No provider aggregation, account pooling, chat-completions adapter, plaintext secret fallback, silently billed fallback or public management API.

## Change necessity and pressure check

Documentation/configuration cannot accept HTTP requests, refresh credentials or stream upstream responses. Add a small package with four owners: configuration, encrypted state, authorization lifecycle, and client gateway. CLI orchestrates those owners. No plugin system, separate microservices or duplicate credential owner.

Downstream consumers are the three agent clients. Preserve request/event shapes except explicit upstream-supported privacy flags. Do not forward client authorization or arbitrary headers upstream. Serialize credential refresh through the single state owner. New public API and persistence seams require target tests and an independent security review.

## TDD route

Mode: auto. Decision: strict. Authority: writing-plans auto route for public API, authentication and credential persistence. Test posture: new package with no prior tests. Add failing tests before each behavior implementation, then verify the focused test and full suite; this is not an assertion of real upstream/client verification.

## Tasks

1. **Project setup and Chinese documentation**
   - Files: `README.zh-CN.md`, `README.md`, `pyproject.toml`, `.gitignore`.
   - Package Python dependencies and CLI entry point; record license facts without selecting an unapproved project license.
   - Verify installation/import and bilingual links.
2. **Configuration and encrypted state**
   - Files: `src/chatpass/config.py`, `src/chatpass/storage.py`, `tests/test_storage.py`, `tests/test_config.py`.
   - Loopback defaults, bounded timeouts/request sizes/concurrency; authenticated encrypted state, platform keyring master key, atomic replace and exclusive file lock.
   - Hash gateway keys, support creation and rotation; fail closed on missing/unusable secure storage and corrupted state.
   - Verify no plaintext token appears on disk, wrong keys fail, atomic update and multiple-process transactions preserve state.
3. **Verified authorization and refresh**
   - Files: `src/chatpass/oauth.py`, `src/chatpass/credentials.py`, `tests/test_oauth.py`, `tests/test_credentials.py`, `docs/protocol-notes.md`.
   - Implement only the verified current protocol, PKCE/state/nonce, callback-issued client ID, signed ID token and granted-scope checks.
   - Enforce one callback exchange, bounded loopback callback server, explicit local login timeout/cancellation; never print OAuth tokens.
   - Refresh inside process-safe transaction, guarded reload, no automatic retry of rotating-token POSTs. Active credentials must be validated. Persist a replacement-token encrypted quarantine checkpoint before remote signature-key retrieval so a temporary verification failure cannot replay the consumed predecessor.
   - Verify adversarial callback/token cases, refresh concurrency and sanitized failure behavior.
4. **Responses gateway**
   - Files: `src/chatpass/gateway.py`, `tests/test_gateway.py`.
   - Bearer-key validation, no public auth management endpoints, JSON bounds, transparent SSE, safe errors, upstream response closure on cancellation, model discovery.
   - Verify invalid-key rejection, privacy constraints, multi-turn tool payload preservation, streaming/errors/timeouts, model shape, concurrency bound and body limit.
5. **CLI and usage**
   - Files: `src/chatpass/cli.py`, `tests/test_cli.py`, bilingual READMEs and `docs/clients.md`.
   - Commands to initialize/rotate gateway key, log in, report non-secret status and serve; no automatic browser login during installation/tests.
   - CLI never emits OAuth secrets. Gateway key is revealed only by explicit key creation/rotation command.
   - Document tested local operation versus unverified real-client/upstream entitlement.
6. **Review and closure**
   - Run `uv run pytest`, packaging checks, CLI smoke tests and actual local HTTP tests using a mock upstream.
   - Independent review targets key handling, login callback, refresh races, disk state and streaming lifetime.
   - Scan staged changes for secrets; commit only task files after fresh verification. Publish no success claim about real account/client calls without evidence.

## Verification and remaining risks

Real upstream login/call, account eligibility/quota semantics and installed Hermes/OpenClaw/DSH integration remain separate manual checks. Server deployment is reserved architecturally but not advertised as supported until TLS/rate-limit/admin separation tests exist. No old implementation exists to retire. If protocol verification contradicts a planned behavior, update this plan before implementing that behavior.

## Execution

Use bounded parallel ownership for gateway implementation after protocol/interface facts stabilize; coordinator owns storage/auth/CLI integration and final staging. No branch/worktree required because the new repository has no unrelated work. Checkpoint progress in `docs/work-status.md` rather than creating per-slice plans.
