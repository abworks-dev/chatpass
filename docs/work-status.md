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

No interactive login, real subscription call or existing-tool credential import was performed. Real official login, eligibility/quota and Hermes/OpenClaw/DeepSeek Harness compatibility require user-controlled authorization and client-version-specific tests in [client checks](clients.md). macOS/Linux system keyring has not been exercised. The project license remains unselected. Public/remote deployment is not supported; do not tunnel or reverse-proxy this local service.

Initial code/documentation work is complete within that boundary. A future task should verify real login first, then model discovery and one short authorized Responses call, then each target client separately. Do not silently replace this evidence with an upstream/client compatibility claim.
