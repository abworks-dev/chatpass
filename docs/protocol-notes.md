# Protocol and reuse evidence

Collected during initial implementation using agent-reach / GitHub gh API. Official DevKit reference revision: `0a36fefeb913055c8c7a1a29b63d82b96b2e841a`. No real login or subscription call was performed during that initial evidence collection; later limited user-authorized DSH probes are recorded separately in [work status](work-status.md). Wire facts below are independently implemented, not copied/translated DevKit source or documentation.

## Sources

- [Direct OSS authorization](https://developers.openai.com/siwc/token-sharing-open-source/sign-in.md)
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference.md)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference.md)
- [Preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations.md)
- [Profiles, sessions and revocation](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions.md)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery.md)
- [Plan-use UI guidelines](https://developers.openai.com/siwc/ui-ux-guidelines.md)
- [Public OIDC discovery](https://auth.openai.com/.well-known/openid-configuration)
- [Pinned OAuth implementation observations](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/0a36fefeb913055c8c7a1a29b63d82b96b2e841a/packages/local/src/oauth.ts)
- [Pinned model implementation observations](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/0a36fefeb913055c8c7a1a29b63d82b96b2e841a/packages/local/src/models.ts)
- [Pinned license](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/0a36fefeb913055c8c7a1a29b63d82b96b2e841a/LICENSE)

## Verified wire facts and implementation choices

- Issuer: `https://auth.openai.com`; discover authorization/token/JWKS endpoints and accept only the same HTTPS origin. Observed live token endpoint: `/api/accounts/oauth/token`, not the legacy `/oauth/token`.
- Resource: `https://api.openai.com/v1`. Scope request: `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`.
- Initial browser authorization uses `dynamic_agent_client`; callback supplies the actual client ID. Returning authorization and token exchange use that issued ID. No client secret or invented registration API.
- Persist one stable `urn:uuid:<uuid>` host ID before authorization, as required by public direct-flow documentation. The inspected DevKit makes this optional, a documentation/implementation discrepancy; ChatPass follows the documented requirement.
- Bind IPv4 loopback before opening browser; exact `/auth/callback`, PKCE S256, independent random state and nonce. Compare state in constant time; nonce belongs to the signed ID token, not callback parameters.
- Require signed RS256 ID token with exact issuer, issued-client audience, nonempty subject, exp/iat and nonce; validate azp when present and for multi-audience. Returning/refresh subject must match the saved subject.
- Token requests are form POSTs; never automatically retry one-time authorization-code or rotating-refresh exchanges. Usable inference grants require Bearer, positive finite lifetime, an offline refresh token and direct-sharing scopes. A nonce-bound verified sign-in without those scopes retains only encrypted identity, never access/refresh tokens or inference authority.
- Serialize refresh through the encrypted-state transaction. A received rotated successor must be checkpointed before remote JWKS validation so a verification outage does not lose the new refresh token. Unverified checkpoint is never usable for inference.
- Upstream model catalog uses `models` entries with `slug`, `display_name`, `visibility`; expose only `visibility == list` as the client's `data/id` catalog.
- Responses uses public `/v1/responses`, no Codex backend routing or tenant/account spoofing headers. Send `store:false`, `stream:true`; string input is normalized to a user item. HTTP preview requires full input history, not `previous_response_id`.
- The compatibility adapter omits explicitly listed unsupported tuning/metadata fields by default, reporting names in `X-ChatPass-Ignored-Fields`; omitted limits/sampling parameters are not implemented. Optional strict mode rejects them. State references, hosted tools and unsafe/lossy conversions still fail explicitly. Bounded framing, finite JSON and a successful terminal outcome are mandatory; partial text plus EOF is not completion.
- Missing upstream Content-Type is accepted only after a typed `response.created`, `response.in_progress` or `response.completed` SSE event with a Responses snapshot (`id`, `object`, `status`, `output`) is observed within the existing deadline and at most one frame-limit of buffered bytes. Probe bytes are replayed unchanged through the normal stream validator. Empty/non-SSE bodies, bare completion events, unknown event prefixes, explicitly blank/wrong media types and compressed responses remain errors; no upstream retry is added.
- Local Responses nonstream aggregation and Chat Completions request/output bridges do not change the public upstream. Function tools and JSON schemas translate; system becomes developer. Chat does not roundtrip opaque reasoning and rejects unsupported refusal/custom-tool/audio outputs. Native Responses remains preferable for full reasoning/custom-tool history.
- Sub2API inspired the separation of normalization, protocol conversion and stream lifecycle checks, not a change of authorization or upstream. Its private Codex/OAuth field stripping is not evidence that the public direct-sharing preview supports those fields. No source, private backend, account pool or retry/fallback mechanism was imported.

## Local lifecycle follow-up (2026-10-13)

- A candidate login never overwrites pending validation; failed candidates preserve saved authority and recovery flags. A JWKS outage retains encrypted quarantine. Valid saved access can be used without rotating its predecessor; expired saved access cannot bypass the refresh window.
- Returning browser sign-in may use a previously verified, encrypted ID-token hint for the same issued client. Manual/emitted/management continuation URLs omit the hint. The management continuation capability is memory-only, authenticated, bounded to 180 seconds and absent from ordinary status.
- Discovery supplies `revocation_endpoint` (observed `/api/accounts/oauth/revoke`), constrained to the exact issuer HTTPS origin. Revocation is a single form POST with `token`, `token_type_hint=refresh_token`, issued `client_id`; empty HTTP 200 is success. No redirect, guessed endpoint, predecessor replay or unvalidated-token revocation. Clear locally even if remote outcome is unknown and tell the user to disconnect in Settings. Normal logout preserves registration/subject; explicit new-account logout resets the local mapping, not the remote app or host ID/key.
- Public SIWC errors use an exact code/parameter allowlist and constrained request ID, never remote message/detail text. Optional error-body reads are identity-encoded JSON, at most 16 KiB and 250 ms, under the request deadline; known HTTP status and 429 Retry-After survive diagnostic timeouts. Failed SSE errors retain safe metadata through native/Chat streaming/nonstreaming without fabricating completion.
- Sign-in identity, direct plan permission and upstream account/quota admission are distinct. Local status does not prove eligibility or remaining budget. `earliest_refresh_at` controls refresh only, not independent candidate validation. No inference replay, API-billing fallback or automatic budget probe is added.

These are independently implemented protocol observations and mocked behavior checks, not new live account/revocation evidence or copied DevKit source. The previously documented limited DSH live probes remain separate historical evidence.

## Responses compatibility follow-up

New public-document readback used the [SIWC preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations.md) for eligibility and the [Responses wire reference](https://developers.openai.com/api/reference/resources/responses/methods/create.md) only for eligible feature shapes. No DevKit code or prose was imported.

- Native requests now accept function/custom namespaces and developer-role `additional_tools` history entries. Namespace children remain function/custom only; calls keep `namespace` separate from `name`. Function schema/description may be null, and nullable `strict` keeps the existing omission behavior.
- Web-search declarations and completed web-search history can be forwarded by native Responses. This is not a local search executor or a guarantee of entitlement: the selected model and account/workspace policy remain authoritative. File search, Code Interpreter, hosted MCP, tool search and programmatic tool calling remain rejected.
- Existing reasoning, text-format/verbosity and include forwarding is regression-covered. No newly verified sampling/output-limit support was found. Those documented unsupported options remain omitted/rejected; `service_tier` support is unconfirmed and its current conservative behavior is unchanged. Generic API image-detail extensions are not evidence of SIWC support and were not enabled.
- The historical live empty-terminal case motivates one completed-item reconstruction owner shared by nonstream collection and Chat. It retains indexed `output_item.done` snapshots and uses them only after successful `response.completed`; populated terminal output is reconciled rather than silently overwritten. Ambiguous identity, duplicate IDs/call IDs, gaps or contradictory output fail closed; empty-terminal reconstruction also rejects added items without a completed item snapshot. Raw native SSE passthrough is unchanged. Chat cannot represent namespace/custom-tool semantics and rejects them explicitly.
- Local mock regressions are implementation evidence, not updated live-service acceptance. Credential lifecycle, public upstream, forced stream/store flags, no-retry behavior and bounded stream limits are unchanged.

## Licensing and remaining uncertainty

The official DevKit's custom Noncommercial v1.0 license restricts employer/business/client development, testing and operation even without charging. Do not copy its code, wording or assets. Independent interface interoperability is distinct from permission to operate a commercial or remotely hosted service; remote/paid applications may require a separate approval path.

Python dependencies are standard independently maintained libraries; exact versions are in `uv.lock`. ChatPass's original code and documentation are MIT-licensed; third-party components retain their own licenses (see `LICENSE` and `THIRD_PARTY_NOTICES.md`). Actual account eligibility, quota accounting, feature support and target-client compatibility require manual authorized validation. Public protocol observations do not prove those outcomes.
