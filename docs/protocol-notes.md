# Protocol and reuse evidence

Collected during initial implementation using agent-reach / GitHub gh API. Official DevKit reference revision: `0a36fefeb913055c8c7a1a29b63d82b96b2e841a`. No real login or subscription call was performed. Wire facts below are independently implemented, not copied/translated DevKit source or documentation.

## Sources

- [Direct OSS authorization](https://developers.openai.com/siwc/token-sharing-open-source/sign-in.md)
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference.md)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference.md)
- [Preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations.md)
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
- Token requests are form POSTs; never automatically retry one-time authorization-code or rotating-refresh exchanges. Require Bearer, positive finite lifetime, offline refresh token and direct-sharing scope.
- Serialize refresh through the encrypted-state transaction. A received rotated successor must be checkpointed before remote JWKS validation so a verification outage does not lose the new refresh token. Unverified checkpoint is never usable for inference.
- Upstream model catalog uses `models` entries with `slug`, `display_name`, `visibility`; expose only `visibility == list` as the client's `data/id` catalog.
- Responses uses public `/v1/responses`, no Codex backend routing or tenant/account spoofing headers. Send `store:false`, `stream:true`; string input is normalized to a user item. HTTP preview requires full input history, not `previous_response_id`.
- Reject unsupported preview fields and hosted tools explicitly rather than silently changing request semantics. Transparent SSE forwarding still needs bounded framing, valid JSON and a terminal outcome; partial text plus EOF is not completion.

## Licensing and remaining uncertainty

The official DevKit's custom Noncommercial v1.0 license restricts employer/business/client development, testing and operation even without charging. Do not copy its code, wording or assets. Independent interface interoperability is distinct from permission to operate a commercial or remotely hosted service; remote/paid applications may require a separate approval path.

Python dependencies are standard independently maintained libraries; exact versions are in `uv.lock`. ChatPass's original code and documentation are MIT-licensed; third-party components retain their own licenses (see `LICENSE` and `THIRD_PARTY_NOTICES.md`). Actual account eligibility, quota accounting, feature support and target-client compatibility require manual authorized validation. Public protocol observations do not prove those outcomes.
