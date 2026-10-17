import { resolveRetryPolicy } from '@deepseek-ai/dsh-llm';
import { PiAiAdapter, type ResolvedPiAiProviderProfile } from '@deepseek-ai/dsh-llm-pi-ai';
import { createProvider, InMemoryCredentialStore, createAssistantMessageEventStream, type AssistantMessageEventStream, type Model } from '@earendil-works/pi-ai';
import { stream, streamSimple } from '@earendil-works/pi-ai/api/openai-responses';

/** Request stateless opaque reasoning without advertising unverified effort levels. */
function replayPayload(payload: unknown): unknown {
  const request = payload as Record<string, unknown>;
  const include = Array.isArray(request.include) ? request.include : [];
  return { ...request, include: [...new Set([...include, 'reasoning.encrypted_content'])] };
}
import type { ConnectionOwner, ConnectionSnapshot } from './connection.js';
import { gatewayFetch } from './transport.js';

export const PROVIDER_ID = 'chatpass';
const NO_RETRY = resolveRetryPolicy({ mode: 'normal', maxRetries: 0 }, 'chatpass.retryPolicy');

/** SDK errors can contain arbitrary upstream error text; retain the protocol,
 * but never publish that text as a Harness/UI failure. No SSE parsing here.
 */
function safeEvents(source: AssistantMessageEventStream): AssistantMessageEventStream {
  const output = createAssistantMessageEventStream();
  void (async () => {
    for await (const event of source) {
      if (event.type === 'error') {
        output.push({ ...event, error: {
          ...event.error, diagnostics: undefined,
          errorMessage: event.reason === 'aborted' ? 'ChatPass request cancelled' : 'ChatPass model request failed',
        } });
      } else output.push(event);
    }
    output.end();
  })();
  return output;
}

/** Construct only catalog models, with capacities explicitly supplied by the user.
 * No ambient credentials, catalog guessing, conversion fork, or global fetch.
 */
function profileFor(snapshot?: ConnectionSnapshot): ResolvedPiAiProviderProfile {
  const models: Model<'openai-responses'>[] = (snapshot?.models ?? []).map((id) => ({
    id, name: id, api: 'openai-responses', provider: PROVIDER_ID,
    baseUrl: snapshot!.settings.baseURL,
    contextWindow: snapshot!.settings.contextWindow, maxTokens: snapshot!.settings.maxTokens,
    input: ['text'], reasoning: false,
    // Unknown pricing is not inferred from the model id or a different provider.
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  }));
  const piProvider = createProvider({
    id: PROVIDER_ID, name: 'ChatPass', models,
    auth: { apiKey: {
      name: 'ChatPass gateway key',
      // pi-ai passes the request apiKey override as a transient credential.
      // Resolving it is mandatory even with request auth; never consult ctx/env.
      resolve: async ({ credential, signal }) => {
        signal.throwIfAborted();
        return credential?.key ? { auth: { apiKey: credential.key } } : undefined;
      },
    } },
    api: {
      stream: (model, context, options) => safeEvents(stream(model as Model<'openai-responses'>, context, {
        ...options, apiKey: snapshot!.apiKey, fetch: gatewayFetch(snapshot!), maxRetries: 0, onPayload: replayPayload,
      })),
      streamSimple: (model, context, options) => safeEvents(streamSimple(model as Model<'openai-responses'>, context, {
        ...options, apiKey: snapshot!.apiKey, fetch: gatewayFetch(snapshot!), maxRetries: 0, onPayload: replayPayload,
      })),
    },
  });
  return Object.freeze({
    provider: PROVIDER_ID, displayName: 'ChatPass', api: 'openai-responses',
    piProvider, retryPolicy: NO_RETRY, cacheRetention: 'none', transport: 'sse',
    streamIdleTimeoutMs: 300_000, maxRequestImageBytes: 1,
    requestImagePixelBudget: 1, requestImageMaxBytes: 1,
    modelErrors: new Map<string, string>(),
    configuredMaxTokens: new Map(models.map((model) => [model.id, model.maxTokens])),
  });
}

/** Memoized profile identity is the existing adapter's immutable-call seam.
 * Keys are bound to the captured profile, never read again after an await.
 */
export function createAdapter(owner: ConnectionOwner): PiAiAdapter {
  let captured: ConnectionSnapshot | undefined;
  let profiles: ReadonlyMap<string, ResolvedPiAiProviderProfile> | undefined;
  const keys = new WeakMap<ResolvedPiAiProviderProfile, string>();
  return new PiAiAdapter({
    profiles: () => {
      const next = owner.state().configured ? owner.capture() : undefined;
      if (!profiles || next !== captured) {
        captured = next;
        const profile = profileFor(next);
        if (next) keys.set(profile, next.apiKey);
        profiles = new Map([[PROVIDER_ID, profile]]);
      }
      return profiles;
    },
    resolveApiKey: async (_provider, profile) => keys.get(profile),
    auth: {
      credentials: new InMemoryCredentialStore(),
      authContext: { env: async () => undefined, fileExists: async () => false },
    },
  });
}
