# ChatPass design brief

Status: approved product direction; implementation and upstream eligibility remain unverified.

## Outcome

Provide a stable gateway URL and gateway-issued access key so one person's Hermes Agent, OpenClaw and DeepSeek Harness can use their authorized ChatGPT subscription access. Begin with all clients and the gateway on the same Windows computer. Preserve the ability to run the gateway on a personal server later.

## Selected approach and alternatives

- Selected: a narrow ChatGPT-focused gateway with local defaults and a self-hostable runtime boundary.
- Not selected: an SDK-only tool, because target users need configurable endpoints and keys in existing applications.
- Deferred: a general multi-provider relay, because provider aggregation, account pooling and commercial distribution are outside the current requirement.

## Logical boundaries

These are responsibility boundaries, not requirements to create separate services or a plugin framework.

1. **Client API:** authenticate gateway keys, expose Responses requests and preserve streaming, tool calls and conversation semantics.
2. **Upstream access:** own account authorization, model availability and upstream request behavior. Reuse proven components only after license and protocol verification.
3. **Credential lifecycle:** own refresh, concurrency control and durable persistence. Use platform secure storage behind a small replaceable interface; never silently persist secrets in plaintext.
4. **Runtime configuration:** own listener address, data paths, request limits and deployment settings independently of Windows-specific UI.

Request path:

```text
Agent client -> ChatPass client authentication -> authorized upstream Responses request
```

Gateway access keys never become upstream OAuth tokens. Clients must not receive upstream credentials.

## Local and remote deployment contract

- Bind to `127.0.0.1` by default; select the port during implementation and check conflicts.
- Configure listener and data locations without changing the client API.
- Keep browser authorization independent of inference server startup. Verify an upstream-supported remote authorization mechanism before publishing instructions; do not promise callback forwarding or token portability without evidence.
- Preserve a replaceable secure-storage boundary for Windows and server environments. Server secret provisioning is deferred, not solved by plaintext fallback.
- Prepare the gateway-key boundary for independent client key issuance and revocation; multi-user account management is not in scope.
- Remote inference requires TLS, authentication, rate limits, request-size limits and appropriate timeouts. TLS may be terminated by a correctly configured reverse proxy.
- Separate management/authentication operations from client inference access; never expose raw credentials through a public management route.
- Do not trust arbitrary forwarded headers. Public binding alone does not constitute a secure or supported server deployment.

## Failure behavior

- Report upstream authorization, eligibility and quota failures explicitly.
- Never silently switch to separately billed API credentials.
- Cancel upstream requests when downstream clients disconnect where the transport supports cancellation.
- Do not claim exactly-once tool execution or blindly retry partially delivered streams.
- Coordinate credential refresh across concurrent requests and, where credentials can be shared, processes. Persist updates atomically.
- Do not log keys, tokens or full request contents by default.

## First-release acceptance

A release is usable only after evidence demonstrates:

1. Supported account authorization and at least one real authorized upstream request; quota semantics are recorded from evidence rather than assumed from token validity.
2. Stable gateway-key authentication, rejected invalid keys and no unintended LAN/public listener.
3. Each named client, at an identified version, completes streaming output, a tool call/result round trip and a multi-turn conversation through the gateway.
4. Credential refresh works under concurrent calls without corrupting or losing credentials.
5. Cancellation, timeout, unsupported input and quota/authorization errors remain intelligible to clients.
6. Logs and committed files contain no credentials.
7. Dependency license review permits the chosen reuse and distribution model.

If an installed client needs Chat Completions instead of Responses, evaluate a bounded adapter after testing that requirement. Do not advertise complete OpenAI compatibility.

## Deferred work

Server packaging, remote authorization UX, hardened deployment examples, management UI, detailed API contract, implementation language and final project license remain open. None is represented as already implemented.

## Non-goals

Multi-provider aggregation, subscription transfer, account pooling, shared quota resale, public registration, billing, unlimited API access and universal OpenAI API compatibility.
