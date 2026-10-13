# ChatPass

**Connect your agent tools to your ChatGPT subscription.**

A local-first, self-hostable gateway for ChatGPT subscription access via the Responses API.

> **Status: design stage.** This repository contains the product scope and proposed architecture, not a working gateway. Authentication eligibility, quota behavior and client compatibility require real integration tests.

## Scope

ChatPass is intended for individuals connecting their own agent tools to an authorized ChatGPT account. It is not a general-purpose multi-provider relay, an account pool or a subscription resale service.

The intended client configuration consists of:

```text
Base URL: http://127.0.0.1:<port>/v1
API Key: <gateway-issued-access-key>
Model: <model-available-to-your-authorized-account>
```

The gateway-issued key is **not** an OpenAI API key. It authenticates the client to a running ChatPass instance. Upstream OAuth credentials remain on the gateway and are not distributed to clients.

## Planned capabilities

- Supported account authorization and automatic credential refresh.
- Responses API access, streaming output and structured tool calls.
- Model discovery based on verified upstream availability.
- Explicit errors for authorization, eligibility, quota and unsupported features.
- No automatic fallback to a separately billed API.

These are design targets, not shipped features. Chat Completions translation will be considered only if a target client's tested version requires it; complete OpenAI API compatibility is not promised.

## Compatibility targets

| Client | Status | Acceptance target |
| --- | --- | --- |
| Hermes Agent | Not tested | Streaming, tool call/result round trip and multi-turn conversation |
| OpenClaw | Not tested | Streaming, tool call/result round trip and multi-turn conversation |
| DeepSeek Harness | Not tested | Streaming, tool call/result round trip and multi-turn conversation |

The first deployment target is a Windows computer running all three clients. Actual compatibility claims must identify tested client versions.

## Deployment

**Local first:** bind to `127.0.0.1` by default. No cloud server is required for the initial use case.

**Self-hostable by design:** keep the gateway core independent of Windows, make listener and data locations configurable, and separate browser authorization from gateway execution. Future remote deployments use the same client API contract.

Remote access is not implemented. Public-network deployment requires TLS, authenticated inference routes, rate limits and protected management endpoints. Prefer a private network or VPN where possible. Allowing remote connections does not authorize account sharing or resale.

See [the design boundary](docs/design.md) for the proposed architecture and acceptance criteria.

## Security

- Keep gateway access keys separate from upstream OAuth credentials.
- Do not log keys, OAuth tokens or full request bodies by default.
- Use secure platform credential storage; do not silently fall back to plaintext.
- Default to loopback-only access and require deliberate remote configuration.
- Treat credential refresh and persistence as concurrency-sensitive operations.

Do not submit account credentials or request contents in public issues. Security-reporting instructions will be published before a usable release.

## Limitations

A ChatGPT subscription is not a universal or unlimited API plan. Available models, eligible accounts, supported authorization flows and usage limits depend on the upstream service. They must be verified before being advertised.

## Roadmap

1. Validate the authorized upstream flow and dependency licensing.
2. Implement and test the Windows local gateway.
3. Verify the three target clients with streaming, tools and multi-turn requests.
4. Document installation, configuration and troubleshooting from tested behavior.
5. Add hardened server deployment without changing the client-facing contract.

## Licensing and affiliation

ChatPass is not affiliated with or endorsed by OpenAI. Use must comply with applicable service terms and dependency licenses.

**A project license has not yet been selected.** Public repository visibility alone does not grant an open-source license. Do not assume upstream noncommercial code can be incorporated into a permissively licensed release; licensing must be resolved before implementation reuse or distribution.
