# Initial implementation checkpoint

## Intent and baseline

Approved request: Chinese README and the local-first ChatGPT subscription gateway, preserving a future server-deployment boundary. Product authority: [design](design.md). Execution plan: [implementation plan](implementation-plan.md). Task-start branch/HEAD: `main` / `74a3081`, clean before this task. Coordinator owns Git staging/commit/push; child agents did not mutate Git. No task branch or worktree was created.

## Completed initial implementation

- Bilingual usage documentation and independently implemented Python 3.11 package with CLI.
- Encrypted state and secure-keyring master key; atomic gateway-key initialization/rotation; process-safe refresh with encrypted successor checkpoints and no ambiguous predecessor retry.
- OAuth/OIDC validation, dynamic registration and bounded IPv4 loopback callback.
- Authenticated model discovery and native streaming Responses; explicit privacy/preview constraints, bounded admission/body/streams, function/custom tool histories and safe errors.
- Two independent read-only review scopes closed concrete findings. Auth/storage/config/CLI reviewer: 42 focused tests passed, independent live slow-header deadline and 8-caller key-init race probes passed. Gateway reviewer: 138 focused gateway/HTTP tests passed, independent overflow, timeout/cancellation saturation and legal mixed-SSE/EOF probes passed.
- Coordinator final full suite: **180 passed, 1 skipped** (opt-in native test); Ruff passed; dependency lock check passed. Opt-in isolated Windows keyring test separately **1 passed**. Cross-process evidence includes concurrent state changes, one refresh and one valid successful initialization.
- wheel/sdist built and inspected for runtime-state/credential/environment exclusion. Rebuilt wheel reinstalled into a clean non-editable virtual environment: version `0.1.0`, CLI and serve help passed. No PyPI publication or release tag created.
- Exact task paths staged and checked for whitespace errors, prohibited runtime paths and credential-shaped literals. Commit identity and remote synchronization are recorded in Git history, not assumed from tests.

Detailed evidence and reproduction commands: [verification](verification.md). These are local initial-implementation facts, not official-service acceptance or a production-release claim.

## Review corrections and boundary alignment

CLI-to-gateway argument wiring, finite JSON numbers, bounded verifier task lifetime, callback absolute deadline, atomic key initialization and mixed SSE terminators were corrected at their single canonical owners after reproducing failure. No parallel owner, plaintext fallback, paid fallback, old adapter or migration was introduced. The initial plan was reconciled to the verified preview protocol and encrypted quarantine checkpoint requirement; [protocol notes](protocol-notes.md) are the source observation record.

## Unverified next step

No interactive login, real subscription call or existing-tool credential import was performed. Real official login, eligibility/quota and Hermes/OpenClaw/DeepSeek Harness compatibility require user-controlled authorization and client-version-specific tests in [client checks](clients.md). macOS/Linux system keyring has not been exercised. The project subsequently adopted MIT for original code and documentation; third-party and service permissions remain separate. Public/remote deployment is not supported; do not tunnel or reverse-proxy this local service.

Initial code/documentation work is complete within that boundary. Real-service acceptance still requires real login, model discovery and one short authorized Responses call, then each target client separately. Do not silently replace this evidence with an upstream/client compatibility claim.

## Completed follow-up implementation: local Web console

User approved a local Web management console adapted from an open-source template. Follow-up baseline: `main` / `3504947`, clean. [Web console plan](web-console-plan.md) records the local scope, endpoints, template/license evidence and file ownership; [console guide](web-console.md) records user and permission flows. Tabler MIT 1.6.1 is pinned and integrity-checked, with complete license notice and no external resource URLs in the selected stylesheet. Explicit Aegis activation retained the practical implementation path without a separate method-pack ceremony.

Implemented: responsive Chinese overview, authorization, gateway-key creation/rotation, explicit model discovery and client setup. `web` starts without an existing key; `serve` remains API-only and key-gated. Management uses a single-use five-minute capability, HttpOnly/SameSite=Strict scoped cookie, eight-hour in-memory session and exact Host/Origin/CSRF checks. OAuth values never enter the UI. Catalog parsing and credential/status owners are shared with the gateway/CLI rather than duplicated.

Final frozen-source evidence (2026-10-13): 258 ordinary tests passed, 2 opt-in tests skipped, 17.67 seconds; separate isolated native keyring test 1 passed (0.40 seconds); durable headless Edge regression 1 passed (3.51 seconds); extended desktop/mobile browser smoke 27 checks passed. Ruff, JavaScript syntax and 46-package lock checks passed. Rebuilt wheel/sdist contain matching static assets and complete MIT notice, exclude runtime state/credentials, and install non-editably; CLI help plus installed console/static HTTP checks passed without credential or upstream use.

Two read-only independent review scopes closed concrete findings. Backend review independently ran 221 focused tests and four shutdown/client-ownership probes. Frontend review independently reproduced polling cutoff/restart and canonical pending-validation recovery, checked fragment ordering, CSP, packaging configuration and license hashes. No concrete Critical/Important finding remains in their reviewed scope; their judgment is advisory. Shutdown drains retained credential/verifier/management workers before owned client teardown; noncancellable OS calls can still prolong shutdown.

Historical evidence correction: early gateway regressions passed but the original 30ms timeout fixture later failed under load before SSE started. The final test alone uses 0.5s startup/header headroom and a 2s outer bound; its unreleased stream gate still forces truncation. Production timeout semantics were not altered. Authentication has its own timeout; the stream deadline starts after authentication, not before. Browser fake-clock tests now wait for actual network completion and reload to trigger expired-session GET checks, avoiding test-only races. Final results above supersede preliminary checkpoints, not the real-service unknowns.

Coordinator alone owns Git closeout, with exact task paths and remote synchronization recorded in Git history; tests alone do not prove push. No task branch/worktree, real official login, existing credential import, inference quota use, PyPI publication or public deployment was introduced. The next product-level verification remains user-controlled real login, catalog/inference and each named client/version separately; this completed local UI slice does not close that acceptance boundary.

## Follow-up: MIT license and distribution metadata

User explicitly selected MIT for original code and documentation and requested metadata, bilingual explanations, third-party notices, package verification and push. Baseline: `main` / `0eb8377`. Added standard `LICENSE`, PEP 639 license expression and three explicit license files, author/keywords/Alpha classifiers/project URLs, independent Tabler and dependency/service boundaries, and durable packaging checks. Dependency versions and runtime behavior are unchanged.

Verification on tracked baseline plus only this task's candidate files: explicit wheel/sdist tests 3 passed (3.57 seconds); ordinary suite 263 passed, 3 opt-in tests skipped (19.38 seconds); Ruff and 46-package lock check passed. Archives carry matching project, third-party and Tabler notices, complete static resources and no runtime state; installed metadata/notices and CLI help passed in a non-editable Python 3.11.9 environment. No existing credentials, official login, inference or real browser session was used. Detailed commands and boundaries: [verification](verification.md#mit-许可与打包验证2026-10-13).

Pre-existing/concurrent frontend and browser-test changes remain outside this task's staging and test snapshot. Coordinator stages only license-related files, preserving that unrelated working-tree state; commit and remote synchronization require Git readback. No PyPI publication, release tag or new runtime/license restriction was introduced. Real account/client acceptance remains unverified.
