# Response compatibility repair

Requested outcome: repair empty-terminal output aggregation, tool-call compatibility and parameters evidenced as supported by public SIWC upstream.

TaskStartSnapshot: clean working tree; HEAD 49ca7107fab850cce363d8d9d997bb28913a70c0; workspace E:\Repository\Github\chatpass. No Git mutations, live authorization/inference, credential access, dependency addition or service replacement authorized by this task.

Baseline read set: README.md, docs/design.md, docs/protocol-notes.md, docs/work-status.md, docs/siwc-improvement-plan.md, pyproject.toml, src/chatpass/bridges.py, src/chatpass/adapters.py. Current README/protocol limitations supersede historical design status. No AGENTS.md found.

Change Necessity: code-change. Existing collect_response ignores completed output-item events; historical live probes report populated response.output_item.done followed by empty response.completed.response.output. Documentation cannot restore discarded output. Repair one canonical stream reconstruction owner shared by collector and Chat bridge, preserve mandatory response.completed success and safe bounds.

ArchitectureReviewRequired: yes, shared stream/compatibility contract. Preserve native stream passthrough, credential owners, public upstream, store:false/stream:true, no retries, no local persistence. Do not synthesize success on EOF, incomplete or failed events. Preserve opaque native reasoning/custom-tool output. Chat remains an explicitly narrower representable subset.

Scope: indexed completed output-item reconciliation, identity/conflict/error/bounds regression coverage, function-call/result roundtrip compatibility, evidence-driven parameter forwarding. Non-goals: WebSocket, background jobs, previous_response_id, hosted tools, account pooling, billing, universal Chat compatibility.

Verification: focused bridge/adapter/gateway regressions, full pytest, Ruff, diff whitespace, independent read-only review. Mocks do not establish live account/client acceptance. Parameters require SIWC-specific upstream evidence, not general OpenAI API documentation alone.

TDD Mode: off; add diagnostic/regression tests without claiming strict TDD. Stop states: done at local verified boundary; needs-verification for external contract unknowns; scope-exceeded if changes require credential or persistence redesign.
