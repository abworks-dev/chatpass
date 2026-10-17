# dsh-chatpass (connection-mode alpha)

Connect an **already running ChatPass gateway** to DeepSeek Harness as the independent `chatpass` provider. This package does not start Python, log in, purchase quota, change the Codex provider, or change your default model.

## Compatibility and installation

Version `0.1.0-alpha.3` targets **DSH `0.2.0-rc.2`**, built and tested in isolation against its SDK packages `0.2.0-rc.2`, Cordis `4.0.4`, and pi-ai `0.87.1`. Node `22.19+` (or `24+`) is required. The desktop's official compatibility evaluator accepted the `alpha.2` manifest without exemptions; `alpha.3` retains those SDK/peer versions. The previous `alpha.1` package incorrectly targeted a newer SDK, and `alpha.2` shipped an ESM browser bundle incompatible with this desktop's classic-script loader. Do not use either older archive as the current installation candidate. `alpha.3` corrects the client packaging and passed isolated checks, including the packed client bundle; its actual installation, cold boot, mounting, and browser interactions still remain pending. This is not a published/stable plugin. See the [versioned implementation and repair evidence](../../docs/aegis/work/2026-10-14-dsh-chatpass/90-evidence.md).

From this package directory:

```powershell
pnpm install
pnpm run check
pnpm pack
```

The generated `dsh-chatpass-0.1.0-alpha.3.tgz` contains the ESM host entry, classic-script browser client with the DSH loader wrapper, declarations, and Cordis patch. For the desktop edition, use **sidebar → Plugins → Add Plugin** and enter the absolute archive path; its `desktop` profile cannot be managed by the CLI. Prefer an isolated test profile, or explicitly authorize installation into your current profile. Do not copy files into the application's installed resources or grant compatibility exemptions.

Before distributing a package for another host version, verify its actual SDK rather than merely broadening peers. The read-only `scripts/check-installed-host.mjs` uses the installed host's official compatibility evaluator, checks build SDK versions against installed peers, and includes an incompatible-version negative control. Run it with that desktop's Electron executable in `ELECTRON_RUN_AS_NODE=1` mode and `--expose-internals`, passing the installed DSH root (containing `node_modules`) and optionally the unpacked archive's manifest path. This check does not install or activate anything.

## Use

1. Start ChatPass yourself and configure its account/upstream using the existing core tools.
2. In DSH **Settings → Plugins → ChatPass**, enter `http://127.0.0.1:19840/v1` (or your explicit loopback port), the **ChatPass gateway key**, and verified context-window / maximum-output-token capacities. This is not an upstream OAuth token.
3. **Validate and save** calls only `GET /v1/models`, not inference. A failed candidate leaves the previous working connection unchanged. Only exact `http://127.0.0.1[:port]/v1` URLs are allowed; no DNS names, remote hosts, redirects, environment proxies, or automatic retries.
4. Use the normal DSH model selector under `chatpass`. Settings → Models also offers a compact card and full configuration dialog backed by the same state. The plugin never silently selects a default model.

All settings **and the gateway key are memory-only**. Restarting/reloading the host plugin requires reconfiguration. There is no plaintext disk/browser-storage fallback and no stored-key readback. Blank key on save reuses the existing key only if the endpoint has not changed; changing endpoint requires entering the key explicitly. The gateway key is global to ChatPass, not a per-plugin or per-account scope. Removing this connection does not revoke it in ChatPass and does not cancel already captured in-flight requests.

Model IDs come only from the gateway catalog. Capacities must be entered explicitly: `/v1/models` does not provide authoritative capability metadata. The adapter advertises text input only, no effort-level selector, and no inferred prices (SDK-required cost fields are unknown/zero placeholders). Existing SDK history conversion preserves tool calls/results and opaque reasoning replay state. Failed/truncated streams are not fabricated into completed responses. Token-limit termination remains distinguishable from a normal stop.

## Boundaries and verification gaps

- Two views share one host owner and revision. Conflicting edits require an explicit reload; failed saves preserve non-secret drafts and clear key input. Local dialog dismissal confirms unsaved changes. The embedded tab retains only non-secret drafts across unmount/remount; its password is discarded and must be re-entered. A host reset or revision change requires explicit reload before an old draft can overwrite settings. Host/browser reload still loses these memory-only drafts.
- Catalog refresh is explicit; last discovery and stale/error state are shown. A listed model does not prove account entitlement, availability, quota, image support, or successful inference.
- The transport uses native Node HTTP to bypass proxy environment variables, with catalog byte/count bounds, request bound, cancellation, and finite timeouts. It accepts only models discovery and Responses paths. No custom OAuth, local account-file reading, Responses converter, or SSE state machine is added.
- Tests use fake keys, ephemeral loopback servers, synthetic SSE, and the actual installed SDK converter. No real ChatPass gateway or upstream was contacted. See the task evidence under the repository's `docs/aegis/work/2026-10-14-dsh-chatpass/` for exact counts and remaining checks.
- Real DSH mounting, actual authentication/origin rejection, light/dark/narrow layouts, keyboard/browser interactions, CSP/style handling, host reloads, and upstream interoperability require an isolated real-host acceptance pass. Type checks and jsdom tests do not replace it.

## Development layout

`src/connection.ts` owns runtime settings/key/revision; `src/transport.ts` owns the restricted local socket; `src/provider.ts` composes the existing pi-ai Responses adapter; `src/host-api.ts` validates commands over the host's authenticated Connection carrier; `src/client-store.ts` and `src/client.tsx` implement shared UI state. Existing Python core and management APIs are unchanged.
