# Stream timeout improvement intent

- Human request: reference Sub2API's related design to improve frequent ChatPass timeouts.
- Goal: prevent the legacy 120-second preparation deadline from truncating healthy long Responses/Chat streams; bound connection/header/idle/total phases independently; expose safe local configuration and phase diagnostics.
- Canonical owners: Settings/CLI own runtime configuration; gateway owns transport/deadlines and resource cleanup. No credential, normalization, output reconstruction or client-provider change.
- TaskStartSnapshot: HEAD 49ca7107fab850cce363d8d9d997bb28913a70c0; existing dirty README.en.md, README.md, docs/clients.md, docs/protocol-notes.md, docs/verification.md, docs/work-status.md, src/chatpass/adapters.py, src/chatpass/bridges.py, tests/test_gateway.py; existing untracked docs/aegis/, tests/test_output_reconstruction.py, tests/test_siwc_tools.py. These belong to concurrent work and must be preserved.
- Baseline read set: docs/design.md failure behavior (cancel, no blind replay, privacy); src/chatpass/config.py, src/chatpass/cli.py, src/chatpass/gateway.py; tests/test_config.py, tests/test_cli.py; legacy gateway/compatibility timeout regression anchors.
- Change Necessity: code-change. Raising one timeout alone does not distinguish header waits, idle streams and continuously active long streams. Minimum repair is existing configuration and gateway lifetime owners; no new transport framework.
- ArchitectureReviewRequired: yes, bounded runtime/deadline contract review, no storage/migration/credential architecture change.
- Non-goals: account pools, fallback routes, automatic inference retry, real account calls, replacing running service, clients' watchdog changes, keepalive-generated success, output reconstruction.
- Privacy: fixed timeout phase labels only; preserve existing safe error handling and one-shot unknown-code opt-in.
- State carrier: this work record plus linked implementation plan (not another workstream). Current work is research/planning; no implementation evidence yet.
