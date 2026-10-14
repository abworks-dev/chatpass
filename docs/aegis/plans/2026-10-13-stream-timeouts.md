# Stream timeout implementation plan

## Goal and authority
Human requested Sub2API-inspired improvement after recurring Upstream request timed out. Existing design.md requires bounded requests, disconnect cancellation, explicit errors and no blind inference replay. Change Necessity: code-change; merely increasing the shared timer still truncates active streams and hides phase information.

## Architecture and stack
Python 3.11+, asyncio, HTTPX and existing ASGI generator/resource owners. Settings/CLI remain runtime-policy owners; gateway remains transport/timeout owner. No dependency, credential/persistence, adapter or output reconstruction changes. Review-driven bounded scope extension: BridgeError carries only allowlisted timeout_phase metadata so nonstream/Chat conversions preserve the same public diagnostic field; no bridge output reconstruction logic changes. Execution inline; read-only source research delegated, independent review after implementation.

## Baseline and reference
Task start HEAD 49ca7107fab850cce363d8d9d997bb28913a70c0; pre-existing dirty paths in ../work/2026-10-13-stream-timeouts/10-intent.md must be preserved. Sub2API reference SHA 4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1:
- https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/repository/http_upstream.go (dial 10s; transport headers separated; client no total timeout).
- https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/config/config.go (generic headers 600s; stream idle 180s; OpenAI profile defaults differ).
- https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/service/openai_gateway_response_handling.go (upstream progress timer independent from downstream keepalive).
Static source reference, not live validation. Independently implement phase separation; do not copy code, unlimited header waits, account failover or billing drain semantics.

## Compatibility and policy
Production defaults: preparation/auth/catalog 120s; connect/pool/write 10s; response headers 600s; upstream stream read idle 180s; stream absolute total 1800s. Total=0 explicitly disables only total, never idle or connection budgets. Valid runtime CLI seconds: positive bounded finite fields, total nonnegative bounded finite. Preserve timeout_seconds as preparation/admin/catalog budget. Settings lacking new fields (legacy injected protocol objects) retain their original timeout as legacy per-phase/total values; production Settings always exposes explicit defaults. Error code upstream_timeout remains compatible, message and safe timeout_phase distinguish preparation, response_headers, connect, pool, write, stream_idle and stream_total. Streaming errors do not send success/[DONE]. A continuous healthy stream can exceed the preparation budget; idle raw bytes reset the read timer, downstream backpressure does not consume idle time while not reading. Optional stream total intentionally still bounds all stream work.

## Triage, complexity and retirement
CanonicalOwner: gateway transport/request lifetime. PatchShape: owner-level timeout policy change, not consumer retry or phrase workaround. Affected consumers: native/Chat, stream/nonstream, headerless SSE probe, console composed app, injected clients. Retire reuse of preparation deadline as stream deadline. Preserve body/frame limits, fixed official URL, explicit upstream errors, cancellation and worker capacity. Existing asyncio timeout scope is rescheduled for headers then disarmed after headers; separate stream owner gets total deadline and per-read idle policy. No new polling, retry, heartbeat worker or alternate transport. Scope fence: keepalive generation and first semantic output retries deferred because local direct client path supplies no evidence requiring them.

## TDD and verification
TDD Mode: off; TDD Route: skipped. Tests used as diagnostic/regression evidence, not mandatory RED gate. Add focused tests independently of concurrently edited test_gateway.py. Verify active streams survive short preparation limit, stall fails at idle, trickled bytes reset idle, heartbeat cannot escape total, headerless probe respects idle/total, total=0 semantics, HTTPX phase-specific limits, phase-safe errors and logs, nonstream collection, disconnect/slot cleanup, and CLI/config validation. Existing gateway, bridge, compatibility, console, CLI/config, upstream error and overload suites are regression scope. No real OAuth/inference or service replacement.

## Tasks
1. src/chatpass/config.py and src/chatpass/cli.py: explicit timeout fields/validation and flags for serve/web; tests/test_timeouts.py verifies defaults/config/CLI wiring.
2. src/chatpass/gateway.py: separate prep, headers, total and idle owner; shared bounded read helper reused by headerless probe; classify fixed phases without logging raw exceptions; preserve known HTTP status.
3. tests/test_timeouts.py: deterministic bounded synthetic streams and call counters; run focused and broad regression and read-only independent review.
4. docs/stream-timeouts.md: defaults, CLI usage, safe phase interpretation, source reference, client timeout boundary and no automatic replay. Update this work checkpoint/evidence.

## Stop
Done only when fresh regressions and owner-level review pass; disclose external client watchdogs and unmeasured real upstream latency. Pause for competing edits to same owner or changed public/credential boundary. No automatic deployment, commit of concurrent work or live calls.
