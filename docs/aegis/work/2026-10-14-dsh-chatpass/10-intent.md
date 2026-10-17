# ChatPass DSH connection plugin — intent

> Historical implementation intent. On 2026-10-18, the user separately authorized reviewing, committing and pushing the related implementation and documentation. The original no-commit boundary below is historical; this later Git authorization does not authorize installation, live-account operations or publication.

Parent: docs/dsh-chatpass-implementation-plan.md; approved design docs/dsh-chatpass-design.md. User authorized implementation, not installation into current DSH, real-account inference, publishing or commits.

Scope: stages 0/1 only, new plugins/dsh-chatpass package. Existing Python core, credentials, defaults, Codex provider and concurrent edits remain untouched. No retired path. Stop: isolated verified plugin with explicit real-host gaps; do not advance to managed mode.

TaskStartSnapshot: E:/Repository/Github/chatpass, main, cefd86d4641a44efa95507db48aa27d7581cb17a, origin/main. One worktree at same root; no staged edits, no active Git operation observed. Preexisting dirty paths: README.en.md, README.md, docs/clients.md, docs/dsh-chatpass-design.md, docs/protocol-notes.md, docs/verification.md, docs/work-status.md, src/chatpass/adapters.py, src/chatpass/bridges.py, tests/test_gateway.py; untracked docs/aegis/work/2026-10-14-response-compatibility, tests/test_output_reconstruction.py, tests/test_siwc_tools.py. Preserve all of them. No commits per task boundary.

Baseline: design/implementation plan read back; core baseline references acknowledged in plan. Installed SDK exact 0.2.1-alpha.1/Cordis 4.0.5-alpha.1/pi-ai 0.87.1 verified from packages, not asserted as current running DSH. Existing Codex reference is on pi-ai 0.85.1. Two settings slot names verified from reference and will typecheck installed SDK. Public authenticated Connection RPC chosen over new bare HTTP routes. Secret persistence not verified: all runtime state memory-only, no plaintext fallback.

TDD Route: Mode off / Decision skipped / Strict authority none. Post-implementation isolated regression tests; no claimed strict RED/GREEN.

Change Necessity: Python cannot register native DSH provider or settings slots; new thin SDK plugin required. No-change/docs-only cannot deliver requested integration. Minimum new code boundary: connection owner, restricted loopback HTTP transport, SDK provider wrapper, authenticated command handler and shared dual-entry UI. No new OAuth/protocol normalization owner.

Complexity Budget: several small single-responsibility files in new package; avoid adding new responsibilities to existing core. Pre-edit owner-fit: add owner files for distinct connection, transport, provider, client responsibilities; wiring-only host entry. No shared mutable files between agents: provider agent owns provider.ts/tests/provider.test.ts; UI agent owns client files/tests; coordinator owns remaining files/Git.

Readiness: immutable per-request connection; explicit capacities required (directory lacks metadata), text-only; no redirects/proxies/retries, no silent conflict overwrite, failed save keeps previous connection, browser receives no stored key. Verification uses fake keys, ephemeral localhost fixtures, synthetic SSE, no ChatPass 19840 or upstream calls. Real installation/browser visual test requires separate evidence, not replaced by unit tests.
