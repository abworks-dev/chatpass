# Stream-timeout evidence

## Boundary and result

- Human objective: Sub2API-inspired improvement of ChatPass timeout policy, not automatic retries or proof that real upstream overload disappears.
- Authority: `docs/aegis/plans/2026-10-13-stream-timeouts.md`, existing `docs/design.md`, and runtime-policy explanation `docs/stream-timeouts.md`.
- Task start / current observed HEAD: `49ca7107fab850cce363d8d9d997bb28913a70c0`, branch `main`. Pre-existing concurrent changes preserved, including bridge output reconstruction, adapters and their tests/docs.
- Scope: config / CLI / gateway timeout owner, bounded BridgeError phase metadata, dedicated synthetic timeout tests, policy docs and this manual work record. No credentials or persistent state changed; no deployment, service restart, live OAuth or live inference.
- Production budgets in seconds: preparation 120, connect/pool/write 10, headers 600, read idle 180, stream total 1800. Total=0 disables only total. Legacy injected settings retain original per-phase timeout.

## Fresh checks

- Initial focused Ruff + timeout/config/CLI regression: 64 passed in 10.35s, job `pwsh-644` exit 0.
- After first independent review repairs: Ruff + focused regression 74 passed in 10.58s, job `pwsh-659` exit 0. Independent follow-up run: 74 passed in 10.27s (reviewer evidence, not the coordinator's final completion check).
- First confined full run `pwsh-651`: 820 passed, 3 skipped, one failure (multiprocessing named pipe `PermissionError: [WinError 5]`), one setup error (event-loop socket allocation `OSError: [WinError 10055]`). These were not passing results and were not silently ignored.
- Exact full command retried with approved broader access, job `pwsh-660`: Ruff passed; 832 passed, 3 skipped in 39.03s; `git diff --check` passed; exit 0.
- Final full recheck after the last request-correlation header fix: job `pwsh-668`, Ruff passed; 832 passed, 3 skipped in 38.54s; `git diff --check` passed; exit 0. This is the coordinator's fresh final business verification.

## Commit-scope verification after human push request

- Staged only the timeout task's 10 files; shared bridge was built from HEAD plus the three timeout-only edits in the Git index. Reconstruction changes and all unrelated files remain unstaged.
- Exported index tree `9e88fcc3c86dc070f4254b6e8dfa15f87f8881a7` to a temporary directory without changing the working tree. Ruff passed. First export pytest attempt inadvertently imported the editable installation's original workspace source, so its mixed-source result (760 passed / one hosted-tool expectation failure / 3 skipped) is not valid staged verification.
- Corrected the verification environment with explicit export `PYTHONPATH` and asserted gateway/adapters/bridges all resolve under the exported source. Job `pwsh-892` then verified the isolated staged business source: **761 passed, 3 skipped in 42.34s**, exit 0. Lower count than working-tree 832 is expected because concurrent tests/features are deliberately excluded from this commit. Staging tree checked unchanged before adding this process-only evidence entry.

## Review and repairs

- Independent read-only reviewer: `6912863b-53cd-4b59-af86-bddc9b02a4d8`, no implementation edits or Git mutations. Reviewed plan/design, actual transport boundary, lifetime owners and four API consumer paths.
- Header/body timeout leak: HTTPcore reuses request `extensions['timeout']['read']` for both headers and body. Setting read=headers allowed short header budgets to truncate longer valid body reads. Repaired transport read to `max(header_timeout, idle_timeout)`; gateway absolute header and per-read idle scopes enforce their actual budgets independently.
- Reviewer used an in-memory real HTTPcore HTTP/1.1 connection, no sockets/API: headers immediate, body delayed 0.2s, header budget 0.1s, idle 0.5s. Repaired request completed; observed transport read limits `[0.5, 0.5]`. Dedicated test now emulates transport enforcement too; MockTransport alone would not prove this case.
- Legacy fallback initially capped connect/pool/write at 10s. Repaired to original timeout for missing-field custom settings; production Settings still has explicit 10s default.
- Native nonstream / Chat conversions initially dropped `timeout_phase`. BridgeError now carries only known phase enums for `upstream_timeout`; private strings, mappings, lists, booleans and None are rejected. Four consumer idle-error tests assert phase and no false `[DONE]`.
- Last reviewer minor: nonstream BridgeError responses omitted `X-ChatPass-Request-ID`. Repaired at existing gateway exception owner, with all four consumer tests checking response ID is present in logs. This last repair requires the final recheck above.
- Reviewer raw ASGI backpressure probe: downstream sends delayed 0.12s, idle budget 0.05s, total disabled; completed=True, timeout=False, closed=True. This is supporting synthetic evidence, not real client validation.

## Ownership / retirement / complexity

- Old invalid responsibility retired on the production main path: preparation deadline no longer governs inference body lifetime. Existing preparation scope is rescheduled for header waiting then disarmed after headers. Gateway remains canonical transport owner; shared `_stream_chunks` owns bounded raw reads, including headerless SSE sniffing.
- Preserve: existing bounded requests/frames/bytes, authorization, worker capacity, response closure, disconnect cancellation, safe logs, known upstream error semantics, and no automatic inference replay.
- Retained legacy duck-typed settings branch is compatibility debt, not claimed clean retirement. Reason: injected settings are an existing gateway caller/test boundary documented in the plan. Retirement trigger: explicitly migrate all such callers to the expanded Settings contract and separately approve removing old-object support. Production main path never uses the missing-field branch.
- Complexity: local fix without new responsibility in the existing gateway transport owner; no polling/retry/keepalive worker or alternate transport. Gateway remains an oversized maintained file (~960 lines); broad extraction deferred to a separate ownership task rather than mixing it with this fix. Tests isolated in one cohesive dedicated file (~323 lines), not added to the already large concurrently edited gateway test file. Closure is bounded owner-level repair with a visible size follow-up, not a claim of repository-wide cleanup.

## Uncovered scope and structural checks

- Confidence B: synthetic target + full regressions, with bounded real-world unknowns. Not measured: real OpenAI latency/overload, DSH/client watchdogs, reverse proxy timeouts, installed/running service uptake. Restart ChatPass with new source/config before live confirmation; inspect safe timeout_phase/request ID if failures persist.
- Manual work record was intentionally used without initializing the entire Aegis workspace. `aegis-workspace.py bundle` exit 2: missing `task-intent-draft.json`; `check` exit 1: missing workspace adr/baseline/specs directories and README/INDEX/BASELINE-GOVERNANCE. Thus no structural bundle validation claim. No broad baseline initialization was added to this bounded timeout task.
- Initial implementation closeout: no task commit; bridge/gateway ownership intersected pre-existing uncommitted work, so shared files were not staged wholesale. No task branch/worktree or service changes. Subsequent direct human request authorized commit and push of this timeout task; isolate only timeout bridge edits in the index, preserve concurrent reconstruction changes in the working tree, and verify the exact staged source before committing.
